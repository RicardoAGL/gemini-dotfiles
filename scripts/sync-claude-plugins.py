#!/usr/bin/env python3
"""Install reviewed Claude skills into Gemini/Antigravity as managed snapshots.

The deployed Claude skill directory is preferred over a mutable repository checkout.
Every installed skill is validated, copied as a complete package, and recorded in a
manifest so later runs can update or remove only entries this script owns.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path


OWNER = "claude-dotfiles-sync"
MANIFEST_NAME = ".claude-sync-manifest.json"
SCHEMA_VERSION = 1
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def configured_path(environment_name: str, default: Path) -> Path:
    value = os.environ.get(environment_name)
    return Path(value).expanduser() if value else default


CLAUDE_SETTINGS_PATH = configured_path(
    "GEMINI_SYNC_CLAUDE_SETTINGS", Path.home() / ".claude" / "settings.json"
)
CLAUDE_PLUGINS_CACHE = configured_path(
    "GEMINI_SYNC_PLUGIN_CACHE", Path.home() / ".claude" / "plugins" / "cache"
)
CLAUDE_USER_SKILLS = configured_path(
    "GEMINI_SYNC_CLAUDE_SKILLS", Path.home() / ".claude" / "skills"
)
GEMINI_SKILLS_DIR = configured_path(
    "GEMINI_SYNC_TARGET", Path.home() / ".gemini" / "skills"
)


class SyncError(RuntimeError):
    """A parity or ownership violation that must fail bootstrap."""


@dataclass(frozen=True)
class DesiredSkill:
    name: str
    source: Path
    source_type: str
    sha256: str
    plugin: str | None = None
    version: str | None = None
    single_file: bool = False

    def manifest_entry(self) -> dict[str, str]:
        entry = {
            "owner": OWNER,
            "sourceType": self.source_type,
            "source": str(self.source.resolve()),
            "sha256": self.sha256,
        }
        if self.plugin:
            entry["plugin"] = self.plugin
        if self.version:
            entry["version"] = self.version
        return entry


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def repository_skill_candidates() -> list[Path]:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    source_map_path = repo_root / "source-map.json"
    candidates: list[Path] = []

    if source_map_path.exists():
        try:
            source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
            claude_repo_name = source_map.get("canonical", {}).get(
                "claudeDotfilesRepo", "claude-dotfiles"
            )
            candidates.append(repo_root.parent / claude_repo_name / "skills")
        except (OSError, json.JSONDecodeError) as error:
            raise SyncError(f"Cannot read {source_map_path}: {error}") from error

    candidates.extend(
        [
            Path.home() / "Desktop" / "github" / "claude-dotfiles" / "skills",
            Path.home() / "desktop" / "github" / "claude-dotfiles" / "skills",
            Path("C:/github/claude-dotfiles/skills"),
        ]
    )
    return list(dict.fromkeys(candidates))


def get_canonical_skills_dirs() -> list[Path]:
    """Return one reviewed canonical source, preferring the versioned repository."""
    explicit = os.environ.get("GEMINI_SYNC_CANONICAL_SKILLS")
    if explicit:
        source = Path(explicit).expanduser()
        if not source.is_dir():
            raise SyncError(f"Configured canonical skill directory is missing: {source}")
        return [source]

    for candidate in repository_skill_candidates():
        if candidate.is_dir():
            return [candidate]

    if CLAUDE_USER_SKILLS.is_dir():
        return [CLAUDE_USER_SKILLS]
    return []


def legacy_canonical_roots(canonical_roots: list[Path]) -> list[Path]:
    """Recognize links produced by the old repo-symlink implementation."""
    roots = list(canonical_roots)
    roots.extend(
        candidate
        for candidate in repository_skill_candidates()
        if candidate.parent.exists()
    )
    return list(dict.fromkeys(path.resolve(strict=False) for path in roots))


def get_enabled_plugins() -> list[str]:
    if not CLAUDE_SETTINGS_PATH.exists():
        return []
    try:
        settings = json.loads(CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SyncError(f"Cannot read Claude plugin settings: {error}") from error
    plugins = settings.get("enabledPlugins", {})
    if not isinstance(plugins, dict):
        raise SyncError("Claude settings enabledPlugins must be an object.")
    return sorted(key for key, enabled in plugins.items() if enabled)


def parse_frontmatter(skill_file: Path) -> tuple[str, str]:
    try:
        content = skill_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SyncError(f"Cannot read skill manifest {skill_file}: {error}") from error
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SyncError(f"Skill manifest lacks YAML frontmatter: {skill_file}")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as error:
        raise SyncError(
            f"Skill manifest has unterminated YAML frontmatter: {skill_file}"
        ) from error

    values: dict[str, str] = {}
    for line in lines[1:end]:
        if not line or line[0].isspace() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()

    name = values.get("name", "").strip("'\"")
    description = values.get("description", "")
    if not NAME_PATTERN.fullmatch(name):
        raise SyncError(f"Invalid skill name '{name}' in {skill_file}")
    if not description:
        raise SyncError(f"Skill description is missing in {skill_file}")
    return name, description


def package_manifest(source: Path, expected_name: str | None = None) -> tuple[str, Path]:
    skill_file = source / "SKILL.md"
    if not skill_file.is_file():
        raise SyncError(f"Skill package has no SKILL.md: {source}")
    name, _ = parse_frontmatter(skill_file)
    if expected_name and name != expected_name:
        raise SyncError(
            f"Skill directory '{expected_name}' disagrees with frontmatter name "
            f"'{name}': {skill_file}"
        )
    return name, skill_file


def directory_hash(source: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix()):
        if path.is_symlink():
            raise SyncError(f"Skill packages may not contain symlinks: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(source).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(b"x" if path.stat().st_mode & 0o111 else b"-")
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def single_file_hash(source: Path) -> str:
    digest = hashlib.sha256()
    digest.update(b"SKILL.md\0")
    digest.update(b"x" if source.stat().st_mode & 0o111 else b"-")
    digest.update(b"\0")
    digest.update(source.read_bytes())
    digest.update(b"\0")
    return digest.hexdigest()


def target_hash(target: Path) -> str:
    if target.is_symlink():
        raise SyncError(f"Managed snapshot unexpectedly became a symlink: {target}")
    if not target.is_dir():
        raise SyncError(f"Managed skill is not a directory: {target}")
    return directory_hash(target)


def add_desired(desired: dict[str, DesiredSkill], skill: DesiredSkill) -> None:
    current = desired.get(skill.name)
    if current is None:
        desired[skill.name] = skill
        return
    if current.source_type == "canonical":
        return
    if current.sha256 != skill.sha256:
        raise SyncError(
            f"Conflicting managed sources for skill '{skill.name}': "
            f"{current.source} and {skill.source}"
        )


def discover_canonical_skills(roots: list[Path]) -> dict[str, DesiredSkill]:
    desired: dict[str, DesiredSkill] = {}
    for root in roots:
        if not root.is_dir():
            raise SyncError(f"Canonical skill directory is missing: {root}")
        print(f"Scanning canonical skills in: {root}")
        for entry in sorted(root.iterdir(), key=lambda item: item.name):
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            name, _ = package_manifest(entry, entry.name)
            add_desired(desired, DesiredSkill(name, entry, "canonical", directory_hash(entry)))
    return desired


def plugin_identity(plugin_id: str) -> tuple[str, str | None]:
    name, separator, marketplace = plugin_id.partition("@")
    return name, marketplace if separator else None


def plugin_roots(plugin_id: str) -> list[Path]:
    plugin_name, marketplace = plugin_identity(plugin_id)
    if marketplace:
        candidate = CLAUDE_PLUGINS_CACHE / marketplace / plugin_name
        return [candidate] if candidate.is_dir() else []
    if not CLAUDE_PLUGINS_CACHE.is_dir():
        return []
    return sorted(
        [
            candidate / plugin_name
            for candidate in CLAUDE_PLUGINS_CACHE.iterdir()
            if (candidate / plugin_name).is_dir()
        ],
        key=lambda path: str(path),
    )


def version_directories(plugin_root: Path) -> list[Path]:
    if any((plugin_root / name).is_dir() for name in ("skills", "agents", "commands")):
        return [plugin_root]
    return [child for child in plugin_root.iterdir() if child.is_dir()]


def select_plugin_version(plugin_id: str) -> Path:
    versions = [
        version
        for root in plugin_roots(plugin_id)
        for version in version_directories(root)
    ]
    if not versions:
        raise SyncError(f"Enabled plugin is missing from the Claude cache: {plugin_id}")
    return max(versions, key=lambda path: (path.stat().st_mtime_ns, path.name, str(path)))


def version_label(version_dir: Path) -> str:
    return version_dir.name


def valid_single_file_candidate(path: Path) -> tuple[str, str] | None:
    try:
        return parse_frontmatter(path)
    except SyncError:
        return None


def discover_plugin_skills(
    enabled_plugins: list[str], desired: dict[str, DesiredSkill]
) -> None:
    for plugin_id in enabled_plugins:
        version_dir = select_plugin_version(plugin_id)
        print(f"Processing plugin: {plugin_id} ({version_label(version_dir)})")
        real_names: set[str] = set()
        skills_root = version_dir / "skills"
        if skills_root.is_dir():
            for manifest in sorted(skills_root.rglob("SKILL.md"), key=lambda path: str(path)):
                package = manifest.parent
                name, _ = package_manifest(package)
                real_names.add(name)
                add_desired(
                    desired,
                    DesiredSkill(
                        name,
                        package,
                        "plugin",
                        directory_hash(package),
                        plugin=plugin_id,
                        version=version_label(version_dir),
                    ),
                )

        for kind in ("agents", "commands"):
            source_root = version_dir / kind
            if not source_root.is_dir():
                continue
            for document in sorted(source_root.rglob("*.md"), key=lambda path: str(path)):
                if document.name.lower() == "readme.md" or {
                    "references",
                    "templates",
                } & set(document.parts):
                    continue
                parsed = valid_single_file_candidate(document)
                if parsed is None:
                    print(f"  skipped non-skill {kind} document: {document}")
                    continue
                name, _ = parsed
                if name in real_names or name in desired:
                    continue
                add_desired(
                    desired,
                    DesiredSkill(
                        name,
                        document,
                        "plugin",
                        single_file_hash(document),
                        plugin=plugin_id,
                        version=version_label(version_dir),
                        single_file=True,
                    ),
                )


def collect_all_legacy_plugin_hashes() -> dict[str, set[str]]:
    hashes: dict[str, set[str]] = {}
    if not CLAUDE_PLUGINS_CACHE.is_dir():
        return hashes
    for document in CLAUDE_PLUGINS_CACHE.rglob("*.md"):
        if document.name.lower() == "readme.md" or {
            "references",
            "templates",
        } & set(document.parts):
            continue
        if not {"agents", "commands", "skills"} & set(document.parts):
            continue
        name = document.stem if document.stem.lower() != "skill" else document.parent.name
        hashes.setdefault(name, set()).add(hashlib.sha256(document.read_bytes()).hexdigest())
    return hashes


def load_manifest() -> dict:
    path = GEMINI_SKILLS_DIR / MANIFEST_NAME
    if not path.exists():
        return {"schemaVersion": SCHEMA_VERSION, "owner": OWNER, "entries": {}}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SyncError(f"Cannot read managed skill manifest {path}: {error}") from error
    if manifest.get("schemaVersion") != SCHEMA_VERSION or manifest.get("owner") != OWNER:
        raise SyncError(f"Unsupported or foreign managed skill manifest: {path}")
    if not isinstance(manifest.get("entries"), dict):
        raise SyncError(f"Managed skill manifest entries must be an object: {path}")
    invalid_names = [name for name in manifest["entries"] if not NAME_PATTERN.fullmatch(name)]
    if invalid_names:
        raise SyncError(
            "Managed skill manifest contains invalid names: "
            + ", ".join(sorted(invalid_names))
        )
    return manifest


def write_manifest(entries: dict[str, dict[str, str]]) -> None:
    manifest_path = GEMINI_SKILLS_DIR / MANIFEST_NAME
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{MANIFEST_NAME}.", dir=GEMINI_SKILLS_DIR, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(
                {"schemaVersion": SCHEMA_VERSION, "owner": OWNER, "entries": entries},
                stream,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
        os.replace(temporary_path, manifest_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def symlink_target_path(target: Path) -> Path | None:
    if not target.is_symlink():
        return None
    raw = Path(os.readlink(target))
    if raw.is_absolute():
        return raw.resolve(strict=False)
    return (target.parent / raw).resolve(strict=False)


def is_legacy_canonical_link(target: Path, canonical_roots: list[Path]) -> bool:
    linked = symlink_target_path(target)
    return linked is not None and any(is_within(linked, root.resolve()) for root in canonical_roots)


def legacy_flat_plugin_target(target: Path, expected_hashes: set[str]) -> bool:
    if not target.is_dir() or target.is_symlink():
        return False
    entries = [entry for entry in target.iterdir() if not entry.name.startswith(".")]
    if len(entries) != 1 or entries[0].name != "SKILL.md" or not entries[0].is_file():
        return False
    return hashlib.sha256(entries[0].read_bytes()).hexdigest() in expected_hashes


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def install_snapshot(skill: DesiredSkill, target: Path) -> None:
    staging_root = Path(tempfile.mkdtemp(prefix=f".{skill.name}.sync-", dir=GEMINI_SKILLS_DIR))
    staged = staging_root / skill.name
    backup = GEMINI_SKILLS_DIR / f".{skill.name}.backup-{uuid.uuid4().hex}"
    replaced = False
    try:
        if skill.single_file:
            staged.mkdir()
            shutil.copy2(skill.source, staged / "SKILL.md")
        else:
            shutil.copytree(skill.source, staged)
        if directory_hash(staged) != skill.sha256:
            raise SyncError(f"Staged skill hash changed while copying: {skill.name}")
        if target.exists() or target.is_symlink():
            target.rename(backup)
            replaced = True
        staged.rename(target)
        if replaced:
            remove_path(backup)
    except Exception:
        if (
            not (target.exists() or target.is_symlink())
            and replaced
            and (backup.exists() or backup.is_symlink())
        ):
            backup.rename(target)
        raise
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)
        if backup.exists() or backup.is_symlink():
            remove_path(backup)


def sync_managed_skills(
    desired: dict[str, DesiredSkill],
    canonical_roots: list[Path],
    legacy_hashes: dict[str, set[str]],
) -> tuple[int, int]:
    if GEMINI_SKILLS_DIR.is_symlink():
        raise SyncError(f"Managed skill root must not be a symlink: {GEMINI_SKILLS_DIR}")
    GEMINI_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    old_entries = manifest["entries"]
    conflicts: list[str] = []
    actions: dict[str, str] = {}

    for name, skill in desired.items():
        target = GEMINI_SKILLS_DIR / name
        exists = target.exists() or target.is_symlink()
        if not exists:
            actions[name] = "install"
            continue
        old = old_entries.get(name)
        if isinstance(old, dict) and old.get("owner") == OWNER:
            if target.is_symlink():
                conflicts.append(f"managed target became a symlink: {name}")
                continue
            current_hash = target_hash(target)
            if current_hash == skill.sha256:
                actions[name] = "unchanged"
            elif current_hash == old.get("sha256"):
                actions[name] = "refresh"
            else:
                conflicts.append(f"managed target has local changes: {name}")
            continue
        if is_legacy_canonical_link(target, canonical_roots) or legacy_flat_plugin_target(
            target, legacy_hashes.get(name, set())
        ):
            actions[name] = "migrate"
        else:
            conflicts.append(f"unmanaged target already exists: {name}")

    stale_managed: list[str] = []
    for name, old in old_entries.items():
        if name in desired:
            continue
        target = GEMINI_SKILLS_DIR / name
        if not (target.exists() or target.is_symlink()):
            stale_managed.append(name)
            continue
        if target.is_symlink() or not isinstance(old, dict) or old.get("owner") != OWNER:
            conflicts.append(f"stale managed target cannot be verified: {name}")
            continue
        if target_hash(target) != old.get("sha256"):
            conflicts.append(f"stale managed target has local changes: {name}")
            continue
        stale_managed.append(name)

    stale_legacy: list[str] = []
    for target in GEMINI_SKILLS_DIR.iterdir():
        name = target.name
        if name.startswith(".") or name in desired or name in old_entries:
            continue
        if is_legacy_canonical_link(target, canonical_roots) or legacy_flat_plugin_target(
            target, legacy_hashes.get(name, set())
        ):
            stale_legacy.append(name)

    if conflicts:
        raise SyncError("Skill sync conflicts:\n- " + "\n- ".join(sorted(conflicts)))

    changed = 0
    for name in sorted(desired):
        action = actions[name]
        if action == "unchanged":
            print(f"  unchanged: {name}")
            continue
        install_snapshot(desired[name], GEMINI_SKILLS_DIR / name)
        changed += 1
        print(f"  {action}: {name}")

    for name in sorted(set(stale_managed + stale_legacy)):
        target = GEMINI_SKILLS_DIR / name
        if target.exists() or target.is_symlink():
            remove_path(target)
        changed += 1
        print(f"  removed stale managed skill: {name}")

    entries = {name: desired[name].manifest_entry() for name in sorted(desired)}
    write_manifest(entries)
    return len(desired), changed


def main() -> int:
    print("=== Syncing managed Claude skills to Gemini/Antigravity ===")
    canonical_roots = get_canonical_skills_dirs()
    if not canonical_roots:
        raise SyncError("No canonical Claude skill directory was found.")
    enabled_plugins = get_enabled_plugins()
    desired = discover_canonical_skills(canonical_roots)
    discover_plugin_skills(enabled_plugins, desired)
    legacy_hashes = collect_all_legacy_plugin_hashes()
    active, changed = sync_managed_skills(
        desired, legacy_canonical_roots(canonical_roots), legacy_hashes
    )
    plugin_count = sum(1 for skill in desired.values() if skill.source_type == "plugin")
    canonical_count = active - plugin_count
    print(
        f"Done. Canonical: {canonical_count}, Plugins: {plugin_count}, "
        f"Managed active: {active}, Changed: {changed}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SyncError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
