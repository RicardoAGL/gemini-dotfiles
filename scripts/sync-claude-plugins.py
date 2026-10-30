#!/usr/bin/env python3
"""Install reviewed Claude skills into Gemini/Antigravity as managed snapshots.

A clean, published ``claude-dotfiles`` main checkout is preferred.  A deployed
Claude skill tree is an explicitly attested fallback.  Every installed skill is
validated, copied as a complete package, and recorded in a manifest so later runs
can update or quarantine only entries this script owns.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
from ctypes import wintypes


OWNER = "claude-dotfiles-sync"
MANIFEST_NAME = ".claude-sync-manifest.json"
SCHEMA_VERSION = 2
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PLUGIN_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ATTESTED_REVISION_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
QUARANTINE_NAME = ".claude-sync-quarantine"
TRANSACTION_NAME = ".claude-sync-transaction"
LOCK_NAME = ".claude-sync.lock"
WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WINDOWS_SYNCHRONIZE = 0x00100000
WINDOWS_WAIT_OBJECT_0 = 0x00000000
WINDOWS_WAIT_TIMEOUT = 0x00000102
WINDOWS_STILL_ACTIVE = 259
LINUX_AT_FDCWD = -100
LINUX_RENAME_NOREPLACE = 1
DARWIN_RENAME_EXCL = 0x00000004


def configured_path(environment_name: str, default: Path) -> Path:
    value = os.environ.get(environment_name)
    selected = Path(value).expanduser() if value else default
    return Path(os.path.abspath(os.fspath(selected)))


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
class CanonicalRoot:
    path: Path
    source_type: str
    revision: str


@dataclass(frozen=True)
class DesiredSkill:
    name: str
    source: Path
    source_type: str
    sha256: str
    source_revision: str | None = None
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
        if self.source_revision:
            entry["sourceRevision"] = self.source_revision
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


def assert_no_symlink_components(path: Path, label: str) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.is_absolute():
        raise SyncError(f"{label} must be absolute: {path}")
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            break
        except OSError as error:
            raise SyncError(f"Cannot inspect {label} component {current}: {error}") from error
        if component_is_link_or_reparse(current, info):
            raise SyncError(f"{label} contains a symlink/reparse component: {current}")


def component_is_link_or_reparse(path: Path, info: object) -> bool:
    if stat.S_ISLNK(getattr(info, "st_mode")):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if getattr(info, "st_file_attributes", 0) & reparse_flag:
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            return bool(is_junction())
        except OSError:
            return True
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


def git_output(repo_root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise SyncError(
            f"Cannot verify canonical repository {repo_root}: {detail.strip()}"
        ) from error
    return result.stdout.strip()


def remote_repository_name(remote: str) -> str:
    normalized = remote.rstrip("/").rsplit(":", 1)[-1]
    name = Path(normalized).name
    return name[:-4] if name.endswith(".git") else name


def validate_repository_source(skills_dir: Path, expected_repo_name: str) -> CanonicalRoot:
    if skills_dir.is_symlink() or not skills_dir.is_dir():
        raise SyncError(f"Canonical repository skills path is invalid: {skills_dir}")
    repo_root = skills_dir.parent.resolve()
    discovered_root = Path(git_output(repo_root, "rev-parse", "--show-toplevel")).resolve()
    if discovered_root != repo_root:
        raise SyncError(
            f"Canonical skills must be directly inside the repository root: {skills_dir}"
        )
    branch = git_output(repo_root, "symbolic-ref", "--short", "HEAD")
    if branch != "main":
        raise SyncError(
            f"Canonical repository must be on main, found '{branch}': {repo_root}"
        )
    dirty = git_output(repo_root, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise SyncError(f"Canonical repository must be clean: {repo_root}")
    head = git_output(repo_root, "rev-parse", "HEAD")
    published = git_output(repo_root, "rev-parse", "refs/remotes/origin/main")
    if head != published:
        raise SyncError(
            f"Canonical repository HEAD must equal published origin/main: {repo_root}"
        )
    remote = git_output(repo_root, "remote", "get-url", "origin")
    if remote_repository_name(remote) != expected_repo_name:
        raise SyncError(
            f"Canonical repository origin is not {expected_repo_name}: {remote}"
        )
    if not GIT_REVISION_PATTERN.fullmatch(head):
        raise SyncError(f"Canonical repository returned an invalid revision: {head}")
    return CanonicalRoot(skills_dir.resolve(), "canonical", head)


def attest_deployed_source(skills_dir: Path) -> CanonicalRoot:
    if skills_dir.is_symlink() or not skills_dir.is_dir():
        raise SyncError(f"Deployed canonical skill directory is invalid: {skills_dir}")
    digest = hashlib.sha256()
    for entry in sorted(skills_dir.iterdir(), key=lambda item: item.name):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if entry.is_symlink():
            raise SyncError(f"Deployed canonical skill may not be a symlink: {entry}")
        name, _ = package_manifest(entry, entry.name)
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(directory_hash(entry).encode("ascii"))
        digest.update(b"\0")
    return CanonicalRoot(
        skills_dir.resolve(), "deployed-canonical", f"sha256:{digest.hexdigest()}"
    )


def get_canonical_skills_dirs() -> list[CanonicalRoot]:
    """Return one reviewed canonical source, preferring the versioned repository."""
    explicit = os.environ.get("GEMINI_SYNC_CANONICAL_SKILLS")
    if explicit:
        source = Path(explicit).expanduser()
        if not source.is_dir():
            raise SyncError(f"Configured canonical skill directory is missing: {source}")
        return [attest_deployed_source(source)]

    for candidate in repository_skill_candidates():
        if candidate.is_dir():
            return [validate_repository_source(candidate, "claude-dotfiles")]

    if CLAUDE_USER_SKILLS.is_dir():
        return [attest_deployed_source(CLAUDE_USER_SKILLS)]
    return []


def revalidate_canonical_sources(sources: list[CanonicalRoot]) -> None:
    for source in sources:
        if source.source_type == "canonical":
            current = validate_repository_source(
                source.path, source.path.parent.name
            )
        elif source.source_type == "deployed-canonical":
            current = attest_deployed_source(source.path)
        else:
            raise SyncError(f"Unsupported canonical source type: {source.source_type}")
        if current.path != source.path or current.revision != source.revision:
            raise SyncError(
                f"Canonical source changed after discovery and staging: {source.path}"
            )


def legacy_canonical_roots(canonical_roots: list[CanonicalRoot]) -> list[Path]:
    """Recognize links produced by the old repo-symlink implementation."""
    roots = [source.path for source in canonical_roots]
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
    enabled_plugins: list[str] = []
    for key, enabled in plugins.items():
        if not enabled:
            continue
        if not isinstance(key, str):
            raise SyncError("Claude plugin identifiers must be strings.")
        plugin_identity(key)
        enabled_plugins.append(key)
    return sorted(enabled_plugins)


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
    if source.is_symlink() or not source.is_dir():
        raise SyncError(f"Skill package must be a real directory: {source}")
    skill_file = source / "SKILL.md"
    if skill_file.is_symlink() or not skill_file.is_file():
        raise SyncError(f"Skill package has no SKILL.md: {source}")
    name, _ = parse_frontmatter(skill_file)
    if expected_name and name != expected_name:
        raise SyncError(
            f"Skill directory '{expected_name}' disagrees with frontmatter name "
            f"'{name}': {skill_file}"
        )
    return name, skill_file


def directory_hash(source: Path) -> str:
    if source.is_symlink() or not source.is_dir():
        raise SyncError(f"Skill package must be a real directory: {source}")
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix()):
        if path.is_symlink():
            raise SyncError(f"Skill packages may not contain symlinks: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise SyncError(f"Skill packages may contain only files/directories: {path}")
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
    if current.source_type in {"canonical", "deployed-canonical"}:
        return
    if current.sha256 != skill.sha256:
        raise SyncError(
            f"Conflicting managed sources for skill '{skill.name}': "
            f"{current.source} and {skill.source}"
        )


def discover_canonical_skills(roots: list[CanonicalRoot]) -> dict[str, DesiredSkill]:
    desired: dict[str, DesiredSkill] = {}
    for canonical in roots:
        root = canonical.path
        if not root.is_dir():
            raise SyncError(f"Canonical skill directory is missing: {root}")
        print(f"Scanning canonical skills in: {root}")
        for entry in sorted(root.iterdir(), key=lambda item: item.name):
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            if entry.is_symlink():
                raise SyncError(f"Canonical skill may not be a symlink: {entry}")
            name, _ = package_manifest(entry, entry.name)
            add_desired(
                desired,
                DesiredSkill(
                    name,
                    entry,
                    canonical.source_type,
                    directory_hash(entry),
                    source_revision=canonical.revision,
                ),
            )
    return desired


def plugin_identity(plugin_id: str) -> tuple[str, str | None]:
    if plugin_id.count("@") > 1:
        raise SyncError(f"Invalid Claude plugin identifier: {plugin_id}")
    name, separator, marketplace = plugin_id.partition("@")
    segments = [name, marketplace] if separator else [name]
    if any(
        not segment
        or ".." in segment
        or not PLUGIN_SEGMENT_PATTERN.fullmatch(segment)
        for segment in segments
    ):
        raise SyncError(f"Invalid Claude plugin identifier: {plugin_id}")
    return name, marketplace if separator else None


def assert_cache_path(path: Path) -> None:
    assert_no_symlink_components(CLAUDE_PLUGINS_CACHE, "Claude plugin cache")
    assert_no_symlink_components(path, "Claude plugin cache path")
    cache = CLAUDE_PLUGINS_CACHE.resolve(strict=False)
    resolved = path.resolve(strict=False)
    if not is_within(resolved, cache):
        raise SyncError(f"Claude plugin cache path escapes its root: {path}")
    try:
        relative = path.relative_to(CLAUDE_PLUGINS_CACHE)
    except ValueError as error:
        raise SyncError(f"Claude plugin cache path is not lexical child: {path}") from error
    current = CLAUDE_PLUGINS_CACHE
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise SyncError(f"Claude plugin cache path contains a symlink: {current}")


def plugin_roots(plugin_id: str) -> list[Path]:
    plugin_name, marketplace = plugin_identity(plugin_id)
    if CLAUDE_PLUGINS_CACHE.exists() or CLAUDE_PLUGINS_CACHE.is_symlink():
        assert_cache_path(CLAUDE_PLUGINS_CACHE)
    if marketplace:
        candidate = CLAUDE_PLUGINS_CACHE / marketplace / plugin_name
        assert_cache_path(candidate)
        return [candidate] if candidate.is_dir() else []
    if not CLAUDE_PLUGINS_CACHE.is_dir():
        return []
    roots: list[Path] = []
    for candidate in CLAUDE_PLUGINS_CACHE.iterdir():
        root = candidate / plugin_name
        assert_cache_path(root)
        if root.is_dir():
            roots.append(root)
    return sorted(roots, key=lambda path: str(path))


def version_directories(plugin_root: Path) -> list[Path]:
    assert_cache_path(plugin_root)
    if any((plugin_root / name).is_dir() for name in ("skills", "agents", "commands")):
        return [plugin_root]
    versions: list[Path] = []
    for child in plugin_root.iterdir():
        assert_cache_path(child)
        if child.is_dir():
            versions.append(child)
    return versions


def parsed_version_key(path: Path) -> tuple:
    match = re.fullmatch(
        r"v?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?",
        path.name,
    )
    if not match:
        return (0, (), 0, "", path.name, str(path))
    numbers = tuple(int(value) for value in match.group(1).split("."))
    prerelease = match.group(2)
    return (
        1,
        numbers,
        1 if prerelease is None else 0,
        prerelease or "",
        path.name,
        str(path),
    )


def select_plugin_version(plugin_id: str) -> Path:
    versions = [
        version
        for root in plugin_roots(plugin_id)
        for version in version_directories(root)
    ]
    if not versions:
        raise SyncError(f"Enabled plugin is missing from the Claude cache: {plugin_id}")
    return max(versions, key=parsed_version_key)


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
            assert_cache_path(skills_root)
            for manifest in sorted(skills_root.rglob("SKILL.md"), key=lambda path: str(path)):
                assert_cache_path(manifest)
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
            assert_cache_path(source_root)
            for document in sorted(source_root.rglob("*.md"), key=lambda path: str(path)):
                assert_cache_path(document)
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


def load_manifest() -> dict:
    path = GEMINI_SKILLS_DIR / MANIFEST_NAME
    if path.is_symlink():
        raise SyncError(f"Managed skill manifest must not be a symlink: {path}")
    if not path.exists():
        return {"schemaVersion": SCHEMA_VERSION, "owner": OWNER, "entries": {}}
    if not path.is_file():
        raise SyncError(f"Managed skill manifest must be a regular file: {path}")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise SyncError(f"Managed skill manifest permissions must be owner-only: {path}")
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
    allowed_keys = {
        "owner",
        "sourceType",
        "source",
        "sourceRevision",
        "sha256",
        "plugin",
        "version",
    }
    for name, entry in manifest["entries"].items():
        if not isinstance(entry, dict):
            raise SyncError(f"Managed skill manifest entry must be an object: {name}")
        unexpected = set(entry) - allowed_keys
        source_type = entry.get("sourceType")
        revision = entry.get("sourceRevision")
        if unexpected:
            raise SyncError(
                f"Managed skill manifest entry has unexpected fields for {name}: "
                + ", ".join(sorted(unexpected))
            )
        if entry.get("owner") != OWNER:
            raise SyncError(f"Managed skill manifest entry has foreign owner: {name}")
        if source_type not in {"canonical", "deployed-canonical", "plugin"}:
            raise SyncError(f"Managed skill manifest entry has invalid source type: {name}")
        if not isinstance(entry.get("source"), str) or not Path(entry["source"]).is_absolute():
            raise SyncError(f"Managed skill manifest entry has invalid source path: {name}")
        if not isinstance(entry.get("sha256"), str) or not SHA256_PATTERN.fullmatch(
            entry["sha256"]
        ):
            raise SyncError(f"Managed skill manifest entry has invalid hash: {name}")
        if source_type == "canonical" and (
            not isinstance(revision, str) or not GIT_REVISION_PATTERN.fullmatch(revision)
        ):
            raise SyncError(f"Managed canonical entry has invalid Git revision: {name}")
        if source_type == "deployed-canonical" and (
            not isinstance(revision, str) or not ATTESTED_REVISION_PATTERN.fullmatch(revision)
        ):
            raise SyncError(f"Managed deployed entry has invalid attestation: {name}")
        if source_type == "plugin":
            plugin = entry.get("plugin")
            if not isinstance(plugin, str):
                raise SyncError(f"Managed plugin entry lacks plugin identity: {name}")
            plugin_identity(plugin)
            if not isinstance(entry.get("version"), str) or not entry["version"]:
                raise SyncError(f"Managed plugin entry lacks plugin version: {name}")
    return manifest


def manifest_bytes(entries: dict[str, dict[str, str]]) -> bytes:
    document = {"schemaVersion": SCHEMA_VERSION, "owner": OWNER, "entries": entries}
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(entries: dict[str, dict[str, str]]) -> None:
    manifest_path = GEMINI_SKILLS_DIR / MANIFEST_NAME
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{MANIFEST_NAME}.", dir=GEMINI_SKILLS_DIR, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(manifest_bytes(entries))
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, manifest_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def normalize_windows_extended_path(value: str) -> str | None:
    """Normalize validated drive/UNC extended paths and reject device namespaces."""
    if value.startswith("\\\\?\\UNC\\"):
        remainder = value[8:]
        parts = remainder.split("\\")
        if (
            "/" in remainder
            or len(parts) < 2
            or any(not part or part in {".", ".."} for part in parts)
        ):
            return None
        return "\\\\" + remainder
    if value.startswith("\\\\?\\"):
        remainder = value[4:]
        tail = remainder[3:]
        if (
            not re.match(r"^[A-Za-z]:\\", remainder)
            or "/" in remainder
            or (
                tail
                and any(
                    not part or part in {".", ".."}
                    for part in tail.split("\\")
                )
            )
        ):
            return None
        return remainder
    if value.startswith("\\\\.\\") or value.startswith("\\??\\"):
        return None
    return value


def symlink_target_path(target: Path) -> Path | None:
    if not target.is_symlink():
        return None
    normalized = normalize_windows_extended_path(os.readlink(target))
    if normalized is None:
        return None
    raw = Path(normalized)
    if raw.is_absolute():
        return raw.resolve(strict=False)
    return (target.parent / raw).resolve(strict=False)


def is_legacy_canonical_link(target: Path, canonical_roots: list[Path]) -> bool:
    linked = symlink_target_path(target)
    return linked is not None and any(is_within(linked, root.resolve()) for root in canonical_roots)


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def atomic_move_noreplace(source: Path, destination: Path) -> None:
    """Atomically move one path without replacing an existing destination."""
    source_path = os.fspath(source)
    destination_path = os.fspath(destination)

    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            renameat2 = libc.renameat2
        except AttributeError as error:
            raise OSError(errno.ENOSYS, "renameat2 is unavailable") from error
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = renameat2(
            LINUX_AT_FDCWD,
            os.fsencode(source_path),
            LINUX_AT_FDCWD,
            os.fsencode(destination_path),
            LINUX_RENAME_NOREPLACE,
        )
        if result != 0:
            error_number = ctypes.get_errno()
            raise OSError(
                error_number,
                os.strerror(error_number),
                source_path,
                destination_path,
            )
        return

    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            renamex_np = libc.renamex_np
        except AttributeError as error:
            raise OSError(errno.ENOSYS, "renamex_np is unavailable") from error
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = renamex_np(
            os.fsencode(source_path),
            os.fsencode(destination_path),
            DARWIN_RENAME_EXCL,
        )
        if result != 0:
            error_number = ctypes.get_errno()
            raise OSError(
                error_number,
                os.strerror(error_number),
                source_path,
                destination_path,
            )
        return

    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        move_file = kernel32.MoveFileExW
        move_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        move_file.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        # No MOVEFILE_REPLACE_EXISTING or COPY_ALLOWED: same-volume, no-replace move.
        if not move_file(source_path, destination_path, 0):
            error_number = ctypes.get_last_error()
            raise ctypes.WinError(error_number)
        return

    raise OSError(
        errno.ENOSYS,
        f"atomic no-replace move is unsupported on {sys.platform}",
    )


def same_path_identity(source_info: os.stat_result, destination_info: os.stat_result) -> bool:
    """Compare stable identity fields and reject inode reuse across object types."""
    return (
        os.path.samestat(source_info, destination_info)
        and stat.S_IFMT(source_info.st_mode) == stat.S_IFMT(destination_info.st_mode)
    )


def move_path(source: Path, destination: Path) -> None:
    """Move one owned entry with no-replace and fail-closed identity checks."""
    try:
        source_identity = source.lstat()
    except OSError as error:
        raise SyncError(f"Cannot identify move source: {source}") from error

    try:
        atomic_move_noreplace(source, destination)
    except OSError as error:
        raise SyncError(
            f"Atomic move failed for {source} -> {destination}: {error}"
        ) from error

    try:
        destination_identity = destination.lstat()
    except OSError as error:
        raise SyncError(
            f"Atomic move destination is missing: {destination}"
        ) from error
    if not same_path_identity(source_identity, destination_identity):
        raise SyncError(
            f"Destination identity changed during atomic move: {destination}"
        )
    if path_exists(source):
        raise SyncError(f"Source path was recreated during atomic move: {source}")


def write_json_atomic(path: Path, value: dict) -> None:
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def stage_skill(skill: DesiredSkill, staged: Path) -> None:
    if skill.single_file:
        staged.mkdir()
        shutil.copy2(skill.source, staged / "SKILL.md")
    else:
        shutil.copytree(skill.source, staged)
    package_manifest(staged, skill.name)
    if directory_hash(staged) != skill.sha256:
        raise SyncError(f"Staged skill hash changed while copying: {skill.name}")


def activate_staged_skill(staged: Path, target: Path, backup: Path) -> None:
    if target.exists() or target.is_symlink():
        move_path(target, backup)
    move_path(staged, target)


def transaction_path() -> Path:
    return GEMINI_SKILLS_DIR / TRANSACTION_NAME


def quarantine_batch_path() -> Path:
    quarantine_root = GEMINI_SKILLS_DIR / QUARANTINE_NAME
    if quarantine_root.is_symlink():
        raise SyncError(f"Skill quarantine root must not be a symlink: {quarantine_root}")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return quarantine_root / f"{timestamp}-{uuid.uuid4().hex[:8]}"


def path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def validate_owner(owner: object) -> dict:
    if not isinstance(owner, dict) or set(owner) != {
        "token",
        "pid",
        "host",
        "createdAt",
    }:
        raise SyncError("Skill sync owner record has an invalid schema.")
    if (
        not isinstance(owner["token"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", owner["token"])
        or not isinstance(owner["pid"], int)
        or owner["pid"] <= 0
        or not isinstance(owner["host"], str)
        or not owner["host"]
        or not isinstance(owner["createdAt"], str)
        or not owner["createdAt"]
    ):
        raise SyncError("Skill sync owner record has invalid values.")
    return owner


def read_lock_owner(lock_path: Path) -> dict:
    if lock_path.is_symlink() or not lock_path.is_file():
        raise SyncError(f"Skill sync lock must be a regular file: {lock_path}")
    if os.name != "nt" and lock_path.stat().st_mode & 0o077:
        raise SyncError(f"Skill sync lock permissions must be owner-only: {lock_path}")
    try:
        return validate_owner(json.loads(lock_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise SyncError(f"Cannot read skill sync lock {lock_path}: {error}") from error


def running_on_windows() -> bool:
    return os.name == "nt"


def load_windows_kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def windows_process_is_active(pid: int) -> bool:
    try:
        kernel32 = load_windows_kernel32()
        handle = kernel32.OpenProcess(
            WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION | WINDOWS_SYNCHRONIZE,
            False,
            pid,
        )
    except (AttributeError, OSError):
        return True
    if not handle:
        return True
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        wait_result = kernel32.WaitForSingleObject(handle, 0)
        if exit_code.value != WINDOWS_STILL_ACTIVE:
            return False
        if wait_result == WINDOWS_WAIT_TIMEOUT:
            return True
        if wait_result == WINDOWS_WAIT_OBJECT_0:
            return True
        return True
    except (AttributeError, OSError, ValueError):
        return True
    finally:
        try:
            kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            pass


def owner_is_active(owner: dict) -> bool:
    if owner["host"] != socket.gethostname():
        return True
    if running_on_windows():
        return windows_process_is_active(owner["pid"])
    try:
        os.kill(owner["pid"], 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def exclusive_sync_lock():
    assert_no_symlink_components(GEMINI_SKILLS_DIR, "Gemini skill root")
    if GEMINI_SKILLS_DIR.exists() and not GEMINI_SKILLS_DIR.is_dir():
        raise SyncError(f"Gemini skill root must be a directory: {GEMINI_SKILLS_DIR}")
    GEMINI_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    assert_no_symlink_components(GEMINI_SKILLS_DIR, "Gemini skill root")
    lock_path = GEMINI_SKILLS_DIR / LOCK_NAME
    owner = {
        "token": uuid.uuid4().hex,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    for attempt in range(2):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except FileExistsError:
            existing_stat = os.lstat(lock_path)
            existing = read_lock_owner(lock_path)
            if owner_is_active(existing):
                raise SyncError(
                    f"Another Gemini skill sync is active on {existing['host']} "
                    f"with pid {existing['pid']}."
                )
            if attempt:
                raise SyncError(f"Cannot replace stale skill sync lock: {lock_path}")
            if os.lstat(lock_path).st_ino != existing_stat.st_ino:
                raise SyncError(f"Skill sync lock changed during inspection: {lock_path}")
            if read_lock_owner(lock_path)["token"] != existing["token"]:
                raise SyncError(f"Skill sync lock owner changed during inspection: {lock_path}")
            lock_path.unlink()
            continue
        except OSError as error:
            raise SyncError(f"Cannot acquire skill sync lock {lock_path}: {error}") from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(owner, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        break
    else:  # pragma: no cover - loop either acquires or raises
        raise SyncError(f"Cannot acquire skill sync lock: {lock_path}")
    try:
        yield owner
    finally:
        try:
            current = read_lock_owner(lock_path)
            if current["token"] == owner["token"]:
                lock_path.unlink()
        except (OSError, SyncError):
            pass


def validate_transaction_journal(journal_path: Path) -> dict:
    if journal_path.is_symlink() or not journal_path.is_file():
        raise SyncError(f"Skill transaction journal must be a regular file: {journal_path}")
    if os.name != "nt" and journal_path.stat().st_mode & 0o077:
        raise SyncError(f"Skill transaction journal permissions must be owner-only: {journal_path}")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SyncError(f"Cannot read skill transaction journal: {error}") from error
    if (
        not isinstance(journal, dict)
        or set(journal) != {
            "schemaVersion",
            "owner",
            "manifestExisted",
            "manifestBeforeSha256",
            "manifestAfterSha256",
            "quarantine",
            "replacements",
            "stale",
        }
        or journal.get("schemaVersion") != 1
        or not isinstance(journal.get("manifestExisted"), bool)
        or not isinstance(journal.get("replacements"), list)
        or not isinstance(journal.get("stale"), list)
        or not isinstance(journal.get("quarantine"), str)
    ):
        raise SyncError("Skill transaction journal has an invalid schema.")
    validate_owner(journal["owner"])
    before_hash = journal["manifestBeforeSha256"]
    after_hash = journal["manifestAfterSha256"]
    if (
        (before_hash is not None and (
            not isinstance(before_hash, str) or not SHA256_PATTERN.fullmatch(before_hash)
        ))
        or not isinstance(after_hash, str)
        or not SHA256_PATTERN.fullmatch(after_hash)
        or journal["manifestExisted"] != (before_hash is not None)
    ):
        raise SyncError("Skill transaction manifest hashes are invalid.")
    quarantine_root = GEMINI_SKILLS_DIR / QUARANTINE_NAME
    if quarantine_root.is_symlink():
        raise SyncError(f"Skill quarantine root must not be a symlink: {quarantine_root}")
    quarantine = Path(journal["quarantine"])
    try:
        relative_quarantine = quarantine.relative_to(quarantine_root)
    except ValueError as error:
        raise SyncError("Skill transaction quarantine path escapes its managed root.") from error
    if len(relative_quarantine.parts) != 1 or quarantine.is_symlink():
        raise SyncError("Skill transaction quarantine batch path is invalid.")

    names: set[str] = set()
    for item in journal["replacements"]:
        if not isinstance(item, dict) or set(item) != {
            "name",
            "hadTarget",
            "oldSha256",
            "newSha256",
        }:
            raise SyncError("Skill transaction replacement entry is invalid.")
        name = item["name"]
        old_sha = item["oldSha256"]
        if (
            not isinstance(name, str)
            or not NAME_PATTERN.fullmatch(name)
            or name in names
            or not isinstance(item["hadTarget"], bool)
            or (
                old_sha is not None
                and (not isinstance(old_sha, str) or not SHA256_PATTERN.fullmatch(old_sha))
            )
            or not isinstance(item["newSha256"], str)
            or not SHA256_PATTERN.fullmatch(item["newSha256"])
        ):
            raise SyncError("Skill transaction replacement values are invalid.")
        names.add(name)
    for item in journal["stale"]:
        if not isinstance(item, dict) or set(item) != {"name", "reason", "oldSha256"}:
            raise SyncError("Skill transaction stale entry is invalid.")
        name = item["name"]
        old_sha = item["oldSha256"]
        if (
            not isinstance(name, str)
            or not NAME_PATTERN.fullmatch(name)
            or name in names
            or item["reason"] not in {"stale-managed", "stale-legacy-symlink"}
            or (
                old_sha is not None
                and (not isinstance(old_sha, str) or not SHA256_PATTERN.fullmatch(old_sha))
            )
        ):
            raise SyncError("Skill transaction stale values are invalid.")
        names.add(name)
    return journal


def rollback_transaction(transaction: Path, journal: dict) -> None:
    quarantine = Path(journal["quarantine"])
    manifest = GEMINI_SKILLS_DIR / MANIFEST_NAME
    manifest_backup = transaction / "manifest.backup"
    unsafe: list[str] = []

    assert_no_symlink_components(transaction, "Skill transaction path")
    if quarantine.exists() or quarantine.is_symlink():
        assert_no_symlink_components(quarantine, "Skill quarantine batch")

    for item in journal["replacements"]:
        name = item["name"]
        target = GEMINI_SKILLS_DIR / name
        backup = transaction / "backups" / name
        if path_exists(backup):
            old_sha = item.get("oldSha256")
            if not item["hadTarget"]:
                unsafe.append(f"{name}: unexpected backup exists for a new install")
            elif old_sha and (
                backup.is_symlink()
                or not backup.is_dir()
                or target_hash(backup) != old_sha
            ):
                unsafe.append(f"{name}: transaction backup changed before recovery")
            elif old_sha is None and not backup.is_symlink():
                unsafe.append(f"{name}: legacy transaction backup is not a symlink")
            if path_exists(target):
                if (
                    target.is_symlink()
                    or not target.is_dir()
                    or target_hash(target) != item["newSha256"]
                ):
                    unsafe.append(
                        f"{name}: current target does not match the journaled new snapshot"
                    )
        elif not item["hadTarget"]:
            if path_exists(target) and (
                target.is_symlink()
                or not target.is_dir()
                or target_hash(target) != item["newSha256"]
            ):
                unsafe.append(f"{name}: unexpected target appeared during recovery")
        elif path_exists(target):
            expected = item.get("oldSha256")
            if (
                target.is_symlink()
                or not target.is_dir()
                or not expected
                or target_hash(target) != expected
            ):
                unsafe.append(f"{name}: pre-transaction target cannot be verified")
        else:
            unsafe.append(f"{name}: transaction lost both target and backup")

    for item in journal["stale"]:
        name = item["name"]
        target = GEMINI_SKILLS_DIR / name
        quarantined = quarantine / name
        if path_exists(quarantined):
            old_sha = item.get("oldSha256")
            if old_sha and (
                quarantined.is_symlink()
                or not quarantined.is_dir()
                or target_hash(quarantined) != old_sha
            ):
                unsafe.append(f"{name}: quarantined snapshot changed before recovery")
            elif old_sha is None and not quarantined.is_symlink():
                unsafe.append(f"{name}: quarantined legacy entry is not a symlink")
            if path_exists(target):
                unsafe.append(f"{name}: target exists beside quarantined recovery copy")
        elif not path_exists(target):
            unsafe.append(f"{name}: transaction lost stale target")
        elif item.get("oldSha256"):
            if (
                target.is_symlink()
                or not target.is_dir()
                or target_hash(target) != item["oldSha256"]
            ):
                unsafe.append(f"{name}: stale target changed before recovery")
        elif not target.is_symlink():
            unsafe.append(f"{name}: legacy stale target is no longer a symlink")

    before_hash = journal["manifestBeforeSha256"]
    after_hash = journal["manifestAfterSha256"]
    if journal["manifestExisted"]:
        if (
            manifest_backup.is_symlink()
            or not manifest_backup.is_file()
            or file_sha256(manifest_backup) != before_hash
        ):
            unsafe.append("manifest: transaction backup is missing or changed")
    elif path_exists(manifest_backup):
        unsafe.append("manifest: unexpected backup exists")
    if path_exists(manifest):
        if manifest.is_symlink() or not manifest.is_file():
            unsafe.append("manifest: current path is not a regular file")
        else:
            current_hash = file_sha256(manifest)
            allowed_hashes = {after_hash}
            if before_hash:
                allowed_hashes.add(before_hash)
            if current_hash not in allowed_hashes:
                unsafe.append("manifest: current contents are not journal-owned")
    elif journal["manifestExisted"]:
        unsafe.append("manifest: pre-transaction manifest disappeared")

    if unsafe:
        raise SyncError(
            "Skill sync recovery requires manual inspection; current state and "
            "transaction evidence were preserved:\n- " + "\n- ".join(unsafe)
        )

    rollback_errors: list[str] = []

    for item in reversed(journal["replacements"]):
        name = item["name"]
        target = GEMINI_SKILLS_DIR / name
        backup = transaction / "backups" / name
        try:
            if path_exists(backup):
                if path_exists(target):
                    remove_path(target)
                move_path(backup, target)
            elif not item["hadTarget"]:
                if path_exists(target):
                    remove_path(target)
        except Exception as error:  # preserve all evidence on rollback failure
            rollback_errors.append(f"{name}: {error}")

    for item in reversed(journal["stale"]):
        name = item["name"]
        target = GEMINI_SKILLS_DIR / name
        quarantined = quarantine / name
        try:
            if path_exists(quarantined):
                if path_exists(target):
                    raise SyncError(f"Cannot restore stale target over existing path: {target}")
                move_path(quarantined, target)
            elif not path_exists(target):
                raise SyncError(f"Transaction lost stale target: {name}")
        except Exception as error:
            rollback_errors.append(f"{name}: {error}")

    try:
        if journal["manifestExisted"]:
            os.replace(manifest_backup, manifest)
        elif path_exists(manifest):
            manifest.unlink()
    except Exception as error:
        rollback_errors.append(f"manifest: {error}")

    metadata = quarantine / "quarantine-manifest.json"
    if metadata.exists() and not rollback_errors:
        metadata.unlink()
    if quarantine.exists() and not rollback_errors:
        try:
            quarantine.rmdir()
            quarantine.parent.rmdir()
        except OSError:
            pass
    if rollback_errors:
        raise SyncError(
            "Skill sync rollback was incomplete; transaction evidence was preserved:\n- "
            + "\n- ".join(rollback_errors)
        )
    remove_path(transaction)


def recover_pending_transaction(current_owner: dict) -> None:
    transaction = transaction_path()
    if transaction.is_symlink():
        raise SyncError(f"Skill transaction path must not be a symlink: {transaction}")
    if not transaction.exists():
        return
    journal_path = transaction / "journal.json"
    if not journal_path.exists():
        raise SyncError(
            f"Unjournaled skill transaction requires manual inspection: {transaction}"
        )
    journal = validate_transaction_journal(journal_path)
    previous_owner = journal["owner"]
    if (
        previous_owner["pid"] != current_owner["pid"]
        or previous_owner["host"] != current_owner["host"]
    ) and owner_is_active(previous_owner):
        raise SyncError(
            f"Skill transaction is owned by an active or unverifiable process on "
            f"{previous_owner['host']} with pid {previous_owner['pid']}."
        )
    rollback_transaction(transaction, journal)


def run_transaction(
    desired: dict[str, DesiredSkill],
    actions: dict[str, str],
    stale: list[dict[str, str | None]],
    old_manifest_exists: bool,
    canonical_sources: list[CanonicalRoot],
    owner: dict,
) -> int:
    transaction = transaction_path()
    transaction.mkdir()
    staged_root = transaction / "staged"
    backup_root = transaction / "backups"
    staged_root.mkdir()
    backup_root.mkdir()
    changed_names = [name for name in sorted(desired) if actions[name] != "unchanged"]
    quarantine = quarantine_batch_path()
    manifest_path = GEMINI_SKILLS_DIR / MANIFEST_NAME
    entries = {name: desired[name].manifest_entry() for name in sorted(desired)}
    manifest_before_sha = file_sha256(manifest_path) if old_manifest_exists else None
    manifest_after_sha = hashlib.sha256(manifest_bytes(entries)).hexdigest()
    if old_manifest_exists:
        shutil.copy2(manifest_path, transaction / "manifest.backup")
    initial_journal = {
        "schemaVersion": 1,
        "owner": owner,
        "manifestExisted": old_manifest_exists,
        "manifestBeforeSha256": manifest_before_sha,
        "manifestAfterSha256": manifest_after_sha,
        "quarantine": str(quarantine),
        "replacements": [],
        "stale": [],
    }
    write_json_atomic(transaction / "journal.json", initial_journal)
    try:
        for name in changed_names:
            stage_skill(desired[name], staged_root / name)
        revalidate_canonical_sources(canonical_sources)
    except Exception:
        remove_path(transaction)
        raise

    replacements = []
    for name in changed_names:
        target = GEMINI_SKILLS_DIR / name
        old_sha = None
        if path_exists(target) and not target.is_symlink():
            old_sha = target_hash(target)
        replacements.append(
            {
                "name": name,
                "hadTarget": path_exists(target),
                "oldSha256": old_sha,
                "newSha256": desired[name].sha256,
            }
        )
    journal = {
        "schemaVersion": 1,
        "owner": owner,
        "manifestExisted": old_manifest_exists,
        "manifestBeforeSha256": manifest_before_sha,
        "manifestAfterSha256": manifest_after_sha,
        "quarantine": str(quarantine),
        "replacements": replacements,
        "stale": stale,
    }
    write_json_atomic(transaction / "journal.json", journal)

    try:
        for item in replacements:
            name = item["name"]
            activate_staged_skill(
                staged_root / name,
                GEMINI_SKILLS_DIR / name,
                backup_root / name,
            )
        if stale:
            quarantine.mkdir(parents=True)
            metadata_entries: dict[str, dict[str, str | None]] = {}
            for item in stale:
                name = str(item["name"])
                target = GEMINI_SKILLS_DIR / name
                if path_exists(target):
                    move_path(target, quarantine / name)
                    metadata_entries[name] = {
                        "reason": str(item["reason"]),
                        "sourceSha256": item.get("oldSha256"),
                    }
            write_json_atomic(
                quarantine / "quarantine-manifest.json",
                {"owner": OWNER, "entries": metadata_entries},
            )
        write_manifest(entries)
    except Exception:
        rollback_transaction(transaction, journal)
        raise

    remove_path(transaction)
    return len(changed_names) + len(stale)


def sync_managed_skills(
    desired: dict[str, DesiredSkill],
    canonical_sources: list[CanonicalRoot],
    owner: dict,
) -> tuple[int, int]:
    assert_no_symlink_components(GEMINI_SKILLS_DIR, "Gemini skill root")
    canonical_roots = legacy_canonical_roots(canonical_sources)
    recover_pending_transaction(owner)
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
        if is_legacy_canonical_link(target, canonical_roots):
            actions[name] = "migrate"
        else:
            conflicts.append(f"unmanaged target already exists: {name}")

    stale: list[dict[str, str | None]] = []
    for name, old in old_entries.items():
        if name in desired:
            continue
        target = GEMINI_SKILLS_DIR / name
        if not (target.exists() or target.is_symlink()):
            continue
        if target.is_symlink() or not isinstance(old, dict) or old.get("owner") != OWNER:
            conflicts.append(f"stale managed target cannot be verified: {name}")
            continue
        if target_hash(target) != old.get("sha256"):
            conflicts.append(f"stale managed target has local changes: {name}")
            continue
        stale.append(
            {"name": name, "reason": "stale-managed", "oldSha256": old.get("sha256")}
        )

    for target in GEMINI_SKILLS_DIR.iterdir():
        name = target.name
        if name.startswith(".") or name in desired or name in old_entries:
            continue
        if is_legacy_canonical_link(target, canonical_roots):
            stale.append(
                {"name": name, "reason": "stale-legacy-symlink", "oldSha256": None}
            )

    if conflicts:
        raise SyncError("Skill sync conflicts:\n- " + "\n- ".join(sorted(conflicts)))

    for name in sorted(desired):
        action = actions[name]
        if action == "unchanged":
            print(f"  unchanged: {name}")
        else:
            print(f"  {action}: {name}")
    for item in stale:
        print(f"  quarantined stale managed skill: {item['name']}")
    expected_entries = {
        name: desired[name].manifest_entry() for name in sorted(desired)
    }
    if (
        not stale
        and all(action == "unchanged" for action in actions.values())
        and expected_entries == old_entries
    ):
        return len(desired), 0
    changed = run_transaction(
        desired,
        actions,
        stale,
        (GEMINI_SKILLS_DIR / MANIFEST_NAME).exists(),
        canonical_sources,
        owner,
    )
    return len(desired), changed


def main() -> int:
    print("=== Syncing managed Claude skills to Gemini/Antigravity ===")
    with exclusive_sync_lock() as owner:
        canonical_roots = get_canonical_skills_dirs()
        if not canonical_roots:
            raise SyncError("No canonical Claude skill directory was found.")
        enabled_plugins = get_enabled_plugins()
        desired = discover_canonical_skills(canonical_roots)
        discover_plugin_skills(enabled_plugins, desired)
        active, changed = sync_managed_skills(desired, canonical_roots, owner)
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
