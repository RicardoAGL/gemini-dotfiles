#!/usr/bin/env python3
"""Filesystem-level tests for the managed Claude-to-Gemini skill sync."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "sync-claude-plugins.py"
MANIFEST_NAME = ".claude-sync-manifest.json"


class FakeNativeFunction:
    def __init__(self, return_value: int):
        self.return_value = return_value
        self.calls: list[tuple[object, ...]] = []
        self.argtypes: list[object] = []
        self.restype: object | None = None

    def __call__(self, *args: object) -> int:
        self.calls.append(args)
        return self.return_value


def load_sync_module():
    spec = importlib.util.spec_from_file_location("sync_claude_plugins", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_skill(
    root: Path,
    name: str,
    description: str,
    extras: dict[str, str] | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    for relative_path, content in (extras or {}).items():
        path = skill_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return skill_dir


class SkillSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.canonical = self.root / "canonical-skills"
        self.cache = self.root / "plugin-cache"
        self.target = self.root / "gemini-skills"
        self.settings = self.root / "claude-settings.json"
        self.canonical.mkdir()
        self.cache.mkdir()
        self.settings.write_text('{"enabledPlugins": {}}\n', encoding="utf-8")
        self.module = load_sync_module()
        self.module.CLAUDE_SETTINGS_PATH = self.settings
        self.module.CLAUDE_PLUGINS_CACHE = self.cache
        self.module.CLAUDE_USER_SKILLS = self.canonical
        self.module.GEMINI_SKILLS_DIR = self.target
        self.canonical_revision = "f" * 40
        self.module.get_canonical_skills_dirs = lambda: [
            self.module.CanonicalRoot(
                self.canonical, "canonical", self.canonical_revision
            )
        ]
        self.module.revalidate_canonical_sources = lambda _roots: None

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_sync(self) -> tuple[int, str]:
        output = StringIO()
        with redirect_stdout(output):
            result = self.module.main()
        return result, output.getvalue()

    def read_manifest(self) -> dict:
        return json.loads((self.target / MANIFEST_NAME).read_text(encoding="utf-8"))

    def test_canonical_skill_is_snapshotted_with_resources_and_manifest(self):
        write_skill(
            self.canonical,
            "alpha-skill",
            "Alpha canonical skill.",
            {"references/guide.md": "guide\n", "scripts/tool.sh": "#!/bin/sh\n"},
        )

        result, _ = self.run_sync()

        self.assertEqual(result, 0)
        installed = self.target / "alpha-skill"
        self.assertTrue((installed / "references" / "guide.md").is_file())
        self.assertTrue((installed / "scripts" / "tool.sh").is_file())
        self.assertFalse(installed.is_symlink())
        entry = self.read_manifest()["entries"]["alpha-skill"]
        self.assertEqual(entry["owner"], "claude-dotfiles-sync")
        self.assertEqual(entry["sourceType"], "canonical")
        self.assertEqual(entry["sourceRevision"], self.canonical_revision)
        self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_unmanaged_same_name_target_is_preserved_and_sync_fails(self):
        write_skill(self.canonical, "collision", "Canonical version.")
        write_skill(self.canonical, "would-install", "Must remain unapplied on conflict.")
        unmanaged = write_skill(self.target, "collision", "User-owned version.")
        before = (unmanaged / "SKILL.md").read_text(encoding="utf-8")

        with self.assertRaises(self.module.SyncError):
            self.run_sync()

        self.assertEqual((unmanaged / "SKILL.md").read_text(encoding="utf-8"), before)
        self.assertFalse((self.target / "would-install").exists())
        self.assertFalse((self.target / MANIFEST_NAME).exists())

    def test_symlinked_target_root_is_rejected_without_following_it(self):
        write_skill(self.canonical, "alpha-skill", "Canonical version.")
        external = self.root / "external-target"
        external.mkdir()
        self.target.symlink_to(external, target_is_directory=True)

        with self.assertRaises(self.module.SyncError):
            self.run_sync()

        self.assertEqual(list(external.iterdir()), [])

    def test_unowned_flat_plugin_export_is_not_inferred_as_legacy(self):
        self.settings.write_text(
            json.dumps({"enabledPlugins": {"coderlm@test-marketplace": True}}),
            encoding="utf-8",
        )
        version = self.cache / "test-marketplace" / "coderlm" / "1.0.0"
        (version / "commands").mkdir(parents=True)
        old_command = version / "commands" / "coderlm.md"
        old_command.write_text("# Legacy command export\n", encoding="utf-8")
        write_skill(
            version / "skills",
            "coderlm",
            "Complete plugin package.",
            {"scripts/coderlm_cli.py": "print('complete')\n"},
        )
        legacy = self.target / "coderlm"
        legacy.mkdir(parents=True)
        (legacy / "SKILL.md").write_bytes(old_command.read_bytes())

        before = (legacy / "SKILL.md").read_bytes()

        with self.assertRaises(self.module.SyncError):
            self.run_sync()

        self.assertEqual((legacy / "SKILL.md").read_bytes(), before)
        self.assertFalse((legacy / "scripts").exists())

    def test_disabled_plugin_lookalike_is_preserved_as_unmanaged(self):
        version = self.cache / "test-marketplace" / "retired" / "1.0.0"
        (version / "commands").mkdir(parents=True)
        old_command = version / "commands" / "retired.md"
        old_command.write_text("# Retired plugin command\n", encoding="utf-8")
        legacy = self.target / "retired"
        legacy.mkdir(parents=True)
        (legacy / "SKILL.md").write_bytes(old_command.read_bytes())
        user_skill = write_skill(self.target, "user-skill", "Unmanaged and preserved.")

        result, output = self.run_sync()

        self.assertEqual(result, 0)
        self.assertTrue(legacy.exists())
        self.assertTrue(user_skill.is_dir())
        self.assertNotIn("retired", output)

    def test_broken_legacy_repo_symlink_is_quarantined(self):
        repo_skills = self.root / "repo-skills"
        repo_skills.mkdir()
        self.module.repository_skill_candidates = lambda: [repo_skills]
        self.target.mkdir()
        stale = self.target / "old-branch-skill"
        stale.symlink_to(repo_skills / "old-branch-skill", target_is_directory=True)

        result, output = self.run_sync()

        self.assertEqual(result, 0)
        self.assertFalse(stale.is_symlink())
        quarantined = [
            candidate / "old-branch-skill"
            for candidate in (self.target / ".claude-sync-quarantine").iterdir()
            if (candidate / "old-branch-skill").is_symlink()
        ]
        self.assertEqual(len(quarantined), 1)
        self.assertTrue(quarantined[0].is_symlink())
        self.assertIn("quarantined stale managed skill: old-branch-skill", output)

    def test_windows_extended_symlink_paths_are_normalized(self):
        self.assertEqual(
            self.module.normalize_windows_extended_path(
                r"\\?\C:\workspace\skills\alpha"
            ),
            r"C:\workspace\skills\alpha",
        )
        self.assertEqual(
            self.module.normalize_windows_extended_path(
                r"\\?\UNC\server\share\skills\alpha"
            ),
            r"\\server\share\skills\alpha",
        )
        for unsupported in (
            r"\\?\GLOBALROOT\Device\HarddiskVolume1\skills\alpha",
            r"\\?\Volume{00000000-0000-0000-0000-000000000000}\skills\alpha",
            r"\\?\C:relative\skills\alpha",
            r"\\?\C:\workspace\..\foreign\alpha",
            r"\\?\UNC\server",
            r"\\.\C:\workspace\skills\alpha",
            r"\??\C:\workspace\skills\alpha",
        ):
            with self.subTest(unsupported=unsupported):
                self.assertIsNone(
                    self.module.normalize_windows_extended_path(unsupported)
                )

        self.target.mkdir()
        foreign = self.target / "foreign-device-link"
        foreign.symlink_to(
            r"\\?\GLOBALROOT\Device\HarddiskVolume1\skills\alpha",
            target_is_directory=True,
        )
        self.assertFalse(
            self.module.is_legacy_canonical_link(foreign, [self.canonical])
        )

    def test_move_path_preserves_substituted_destination(self):
        source = self.root / "source"
        destination = self.root / "destination"
        attacker_target = self.root / "attacker-target"
        source.mkdir()

        def substitute_destination(_source: Path, _destination: Path) -> None:
            _source.rename(_destination)
            _destination.rmdir()
            _destination.symlink_to(attacker_target, target_is_directory=True)

        with mock.patch.object(
            self.module,
            "atomic_move_noreplace",
            side_effect=substitute_destination,
        ):
            with self.assertRaisesRegex(
                self.module.SyncError, "identity changed during atomic move"
            ):
                self.module.move_path(source, destination)

        self.assertTrue(destination.is_symlink())
        self.assertEqual(
            self.module.symlink_target_path(destination),
            attacker_target.resolve(strict=False),
        )

    def test_move_identity_rejects_reused_inode_for_different_object_type(self):
        original = SimpleNamespace(
            st_dev=7,
            st_ino=11,
            st_mode=self.module.stat.S_IFDIR | 0o700,
        )
        replacement = SimpleNamespace(
            st_dev=7,
            st_ino=11,
            st_mode=self.module.stat.S_IFLNK | 0o777,
        )

        self.assertFalse(self.module.same_path_identity(original, replacement))

    def test_move_path_preserves_recreated_source(self):
        source = self.root / "source"
        destination = self.root / "destination"
        attacker_target = self.root / "attacker-target"
        source.mkdir()

        def recreate_source(_source: Path, _destination: Path) -> None:
            _source.rename(_destination)
            _source.symlink_to(attacker_target, target_is_directory=True)

        with mock.patch.object(
            self.module,
            "atomic_move_noreplace",
            side_effect=recreate_source,
        ):
            with self.assertRaisesRegex(
                self.module.SyncError, "Source path was recreated during atomic move"
            ):
                self.module.move_path(source, destination)

        self.assertTrue(source.is_symlink())
        self.assertEqual(
            self.module.symlink_target_path(source),
            attacker_target.resolve(strict=False),
        )
        self.assertTrue(destination.is_dir())

    def test_move_path_does_not_replace_existing_destination(self):
        source = self.root / "source"
        destination = self.root / "destination"
        source.mkdir()
        destination.mkdir()

        with self.assertRaisesRegex(self.module.SyncError, "Atomic move failed"):
            self.module.move_path(source, destination)

        self.assertTrue(source.is_dir())
        self.assertTrue(destination.is_dir())

    def test_linux_atomic_move_uses_exact_paths_and_noreplace(self):
        renameat2 = FakeNativeFunction(0)
        libc = SimpleNamespace(renameat2=renameat2)
        source = Path("literal/../source")
        destination = Path("literal/../destination")

        with (
            mock.patch.object(self.module.sys, "platform", "linux"),
            mock.patch.object(self.module.ctypes, "CDLL", return_value=libc),
            mock.patch.object(self.module.ctypes, "set_errno"),
        ):
            self.module.atomic_move_noreplace(source, destination)

        self.assertEqual(
            renameat2.calls,
            [
                (
                    self.module.LINUX_AT_FDCWD,
                    os.fsencode(os.fspath(source)),
                    self.module.LINUX_AT_FDCWD,
                    os.fsencode(os.fspath(destination)),
                    self.module.LINUX_RENAME_NOREPLACE,
                )
            ],
        )

    def test_darwin_atomic_move_uses_exact_paths_and_exclusive_flag(self):
        renamex_np = FakeNativeFunction(0)
        libc = SimpleNamespace(renamex_np=renamex_np)
        source = Path("literal/../source")
        destination = Path("literal/../destination")

        with (
            mock.patch.object(self.module.sys, "platform", "darwin"),
            mock.patch.object(self.module.ctypes, "CDLL", return_value=libc),
            mock.patch.object(self.module.ctypes, "set_errno"),
        ):
            self.module.atomic_move_noreplace(source, destination)

        self.assertEqual(
            renamex_np.calls,
            [
                (
                    os.fsencode(os.fspath(source)),
                    os.fsencode(os.fspath(destination)),
                    self.module.DARWIN_RENAME_EXCL,
                )
            ],
        )

    def test_windows_atomic_move_uses_exact_paths_without_replace_flags(self):
        move_file = FakeNativeFunction(1)
        kernel32 = SimpleNamespace(MoveFileExW=move_file)
        source = Path("literal/../source")
        destination = Path("literal/../destination")

        with (
            mock.patch.object(self.module.sys, "platform", "win32"),
            mock.patch.object(
                self.module.ctypes, "WinDLL", create=True, return_value=kernel32
            ),
            mock.patch.object(self.module.ctypes, "set_last_error", create=True),
        ):
            self.module.atomic_move_noreplace(source, destination)

        self.assertEqual(
            move_file.calls,
            [(os.fspath(source), os.fspath(destination), 0)],
        )

    def test_managed_skill_refreshes_and_second_identical_run_is_unchanged(self):
        source = write_skill(self.canonical, "refreshable", "Version one.")
        first_result, _ = self.run_sync()
        first_manifest = self.read_manifest()
        installed = self.target / "refreshable"
        (source / "SKILL.md").write_text(
            "---\nname: refreshable\ndescription: Version two.\n---\n",
            encoding="utf-8",
        )
        second_result, _ = self.run_sync()
        second_manifest = self.read_manifest()
        refreshed_inode = installed.stat().st_ino
        third_result, third_output = self.run_sync()

        self.assertEqual((first_result, second_result, third_result), (0, 0, 0))
        self.assertNotEqual(
            first_manifest["entries"]["refreshable"]["sha256"],
            second_manifest["entries"]["refreshable"]["sha256"],
        )
        self.assertIn("Version two", (installed / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(installed.stat().st_ino, refreshed_inode)
        self.assertIn("unchanged: refreshable", third_output)

    def test_real_plugin_skill_package_beats_command_document(self):
        self.settings.write_text(
            json.dumps({"enabledPlugins": {"coderlm@test-marketplace": True}}),
            encoding="utf-8",
        )
        version = self.cache / "test-marketplace" / "coderlm" / "1.0.0"
        (version / "commands").mkdir(parents=True)
        (version / "commands" / "coderlm.md").write_text(
            "# Command, not a skill\n", encoding="utf-8"
        )
        write_skill(
            version / "skills",
            "coderlm",
            "The real plugin skill.",
            {"scripts/coderlm_cli.py": "print('ok')\n", "references/api.md": "api\n"},
        )

        result, _ = self.run_sync()

        self.assertEqual(result, 0)
        installed = self.target / "coderlm"
        self.assertIn("real plugin skill", (installed / "SKILL.md").read_text(encoding="utf-8"))
        self.assertTrue((installed / "scripts" / "coderlm_cli.py").is_file())
        self.assertTrue((installed / "references" / "api.md").is_file())
        entry = self.read_manifest()["entries"]["coderlm"]
        self.assertEqual(entry["sourceType"], "plugin")
        self.assertEqual(entry["version"], "1.0.0")

    def test_newest_plugin_cache_version_is_selected_deterministically(self):
        self.settings.write_text(
            json.dumps({"enabledPlugins": {"versioned@test-marketplace": True}}),
            encoding="utf-8",
        )
        old = self.cache / "test-marketplace" / "versioned" / "1.0.0"
        new = self.cache / "test-marketplace" / "versioned" / "2.0.0"
        write_skill(old / "skills", "versioned", "Old plugin version.")
        write_skill(new / "skills", "versioned", "New plugin version.")
        os.utime(old, ns=(2_000_000_000, 2_000_000_000))
        os.utime(new, ns=(1_000_000_000, 1_000_000_000))

        result, _ = self.run_sync()

        self.assertEqual(result, 0)
        self.assertIn(
            "New plugin version",
            (self.target / "versioned" / "SKILL.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(self.read_manifest()["entries"]["versioned"]["version"], "2.0.0")

    def test_invalid_real_skill_fails_before_activation(self):
        invalid = self.canonical / "invalid_skill"
        invalid.mkdir()
        (invalid / "SKILL.md").write_text("# Missing frontmatter\n", encoding="utf-8")

        with self.assertRaises(self.module.SyncError):
            self.run_sync()

        self.assertFalse((self.target / "invalid_skill").exists())

    def test_invalid_manifest_entry_name_fails_without_touching_targets(self):
        self.target.mkdir()
        user_skill = write_skill(self.target, "user-skill", "Unmanaged and preserved.")
        manifest_path = self.target / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(
                {
                    "schemaVersion": self.module.SCHEMA_VERSION,
                    "owner": "claude-dotfiles-sync",
                    "entries": {"../outside": {"owner": "claude-dotfiles-sync"}},
                }
            ),
            encoding="utf-8",
        )
        manifest_path.chmod(0o600)

        with self.assertRaises(self.module.SyncError):
            self.run_sync()

        self.assertTrue(user_skill.is_dir())

    def test_symlinked_manifest_is_rejected(self):
        self.target.mkdir()
        external = self.root / "external-manifest.json"
        external.write_text(
            json.dumps(
                {
                    "schemaVersion": self.module.SCHEMA_VERSION,
                    "owner": "claude-dotfiles-sync",
                    "entries": {},
                }
            ),
            encoding="utf-8",
        )
        (self.target / MANIFEST_NAME).symlink_to(external)

        with self.assertRaises(self.module.SyncError):
            self.run_sync()

        self.assertTrue((self.target / MANIFEST_NAME).is_symlink())

    def test_manifest_entry_owner_and_hash_are_validated(self):
        self.target.mkdir()
        manifest_path = self.target / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(
                {
                    "schemaVersion": self.module.SCHEMA_VERSION,
                    "owner": "claude-dotfiles-sync",
                    "entries": {
                        "claimed": {
                            "owner": "someone-else",
                            "sourceType": "canonical",
                            "source": "/tmp/source",
                            "sourceRevision": "not-a-revision",
                            "sha256": "not-a-hash",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        manifest_path.chmod(0o600)

        with self.assertRaises(self.module.SyncError):
            self.run_sync()

    def test_plugin_identifier_path_traversal_is_rejected(self):
        for plugin_id in ("../../escape@market", "plugin@../../market", "a@b@c"):
            with self.subTest(plugin_id=plugin_id):
                self.settings.write_text(
                    json.dumps({"enabledPlugins": {plugin_id: True}}),
                    encoding="utf-8",
                )
                with self.assertRaises(self.module.SyncError):
                    self.module.get_enabled_plugins()

    def test_symlinked_plugin_cache_component_is_rejected(self):
        external = self.root / "external-cache"
        write_skill(
            external / "plugin" / "1.0.0" / "skills", "plugin", "External."
        )
        (self.cache / "market").symlink_to(external, target_is_directory=True)

        with self.assertRaises(self.module.SyncError):
            self.module.select_plugin_version("plugin@market")

    def test_repository_source_requires_clean_published_main(self):
        repo = self.root / "claude-dotfiles"
        remote = self.root / "claude-dotfiles.git"
        subprocess.run(
            ["git", "init", "--bare", str(remote)], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "init", "-b", "main", str(repo)], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Fixture"], cwd=repo, check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "fixture@example.test"],
            cwd=repo,
            check=True,
        )
        write_skill(repo / "skills", "published", "Published.")
        subprocess.run(["git", "add", "skills"], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-m", "fixture"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "main"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        source = self.module.validate_repository_source(
            repo / "skills", "claude-dotfiles"
        )
        self.assertEqual(source.source_type, "canonical")
        self.assertRegex(source.revision, r"^[0-9a-f]{40}$")

        (repo / "skills" / "published" / "SKILL.md").write_text(
            "dirty\n", encoding="utf-8"
        )
        with self.assertRaises(self.module.SyncError):
            self.module.validate_repository_source(repo / "skills", "claude-dotfiles")

    def test_deployed_fallback_is_distinct_and_content_attested(self):
        write_skill(self.canonical, "attested", "Attested fallback.")

        source = self.module.attest_deployed_source(self.canonical)

        self.assertEqual(source.source_type, "deployed-canonical")
        self.assertRegex(source.revision, r"^sha256:[0-9a-f]{64}$")

    def test_stale_unmodified_managed_skill_is_quarantined(self):
        source = write_skill(self.canonical, "stale-skill", "Will become stale.")
        first_result, _ = self.run_sync()
        self.assertEqual(first_result, 0)
        self.assertTrue((self.target / "stale-skill").is_dir())

        for path in sorted(source.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        source.rmdir()
        second_result, second_output = self.run_sync()

        self.assertEqual(second_result, 0)
        self.assertFalse((self.target / "stale-skill").exists())
        self.assertNotIn("stale-skill", self.read_manifest()["entries"])
        quarantined = list(
            (self.target / ".claude-sync-quarantine").glob("*/stale-skill")
        )
        self.assertEqual(len(quarantined), 1)
        self.assertTrue(quarantined[0].is_dir())
        metadata = json.loads(
            (quarantined[0].parent / "quarantine-manifest.json").read_text()
        )
        self.assertEqual(
            metadata["entries"]["stale-skill"]["reason"], "stale-managed"
        )
        self.assertIn("quarantined stale managed skill: stale-skill", second_output)

    def test_modified_stale_managed_skill_is_preserved_and_reported(self):
        source = write_skill(self.canonical, "locally-modified", "Managed initially.")
        first_result, _ = self.run_sync()
        self.assertEqual(first_result, 0)
        installed_manifest = self.target / "locally-modified" / "SKILL.md"
        installed_manifest.write_text(
            installed_manifest.read_text(encoding="utf-8") + "\nlocal edit\n",
            encoding="utf-8",
        )
        for path in sorted(source.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        source.rmdir()

        with self.assertRaises(self.module.SyncError):
            self.run_sync()

        self.assertTrue((self.target / "locally-modified").is_dir())
        self.assertIn("local edit", installed_manifest.read_text(encoding="utf-8"))

    def test_activation_failure_rolls_back_all_prior_changes(self):
        write_skill(self.canonical, "alpha", "Alpha.")
        write_skill(self.canonical, "beta", "Beta.")
        original = self.module.activate_staged_skill
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected activation failure")
            return original(*args, **kwargs)

        self.module.activate_staged_skill = fail_second
        with self.assertRaises(OSError):
            self.run_sync()

        self.assertFalse((self.target / "alpha").exists())
        self.assertFalse((self.target / "beta").exists())
        self.assertFalse((self.target / MANIFEST_NAME).exists())

    def test_manifest_failure_rolls_back_refresh_and_quarantine(self):
        stale_source = write_skill(self.canonical, "stale", "Stale.")
        refresh_source = write_skill(self.canonical, "refresh", "Version one.")
        self.run_sync()
        original_manifest = (self.target / MANIFEST_NAME).read_bytes()
        original_refresh = (self.target / "refresh" / "SKILL.md").read_bytes()
        stale_source.rename(self.root / "removed-stale-source")
        (refresh_source / "SKILL.md").write_text(
            "---\nname: refresh\ndescription: Version two.\n---\n", encoding="utf-8"
        )

        def fail_manifest(_entries):
            raise OSError("injected manifest failure")

        self.module.write_manifest = fail_manifest
        with self.assertRaises(OSError):
            self.run_sync()

        self.assertEqual((self.target / MANIFEST_NAME).read_bytes(), original_manifest)
        self.assertEqual(
            (self.target / "refresh" / "SKILL.md").read_bytes(), original_refresh
        )
        self.assertTrue((self.target / "stale").is_dir())
        quarantine = self.target / ".claude-sync-quarantine"
        self.assertFalse(quarantine.exists() and any(quarantine.iterdir()))

    def test_next_run_recovers_an_interrupted_transaction(self):
        write_skill(self.canonical, "alpha", "Alpha.")
        write_skill(self.canonical, "beta", "Beta.")
        original = self.module.activate_staged_skill
        calls = 0

        def interrupt_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt("injected interruption")
            return original(*args, **kwargs)

        self.module.activate_staged_skill = interrupt_second
        with self.assertRaises(KeyboardInterrupt):
            self.run_sync()

        self.assertTrue((self.target / self.module.TRANSACTION_NAME).is_dir())
        self.module.activate_staged_skill = original
        result, _ = self.run_sync()

        self.assertEqual(result, 0)
        self.assertTrue((self.target / "alpha").is_dir())
        self.assertTrue((self.target / "beta").is_dir())
        self.assertFalse((self.target / self.module.TRANSACTION_NAME).exists())

    def test_tampered_transaction_journal_cannot_escape_managed_root(self):
        external = self.root / "external"
        external.mkdir()
        sentinel = external / "sentinel.txt"
        sentinel.write_text("preserve\n", encoding="utf-8")
        transaction = self.target / self.module.TRANSACTION_NAME
        transaction.mkdir(parents=True)
        journal = transaction / "journal.json"
        journal.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "manifestExisted": False,
                    "quarantine": str(external),
                    "replacements": [],
                    "stale": [],
                }
            ),
            encoding="utf-8",
        )
        journal.chmod(0o600)

        with self.assertRaises(self.module.SyncError):
            self.run_sync()

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")

    def test_configured_paths_preserve_and_reject_symlinked_components(self):
        external = self.root / "external-config"
        external.mkdir()
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(external, target_is_directory=True)
        lexical_target = linked_parent / "gemini-skills"
        lexical_cache = linked_parent / "plugin-cache"

        with mock.patch.dict(
            os.environ,
            {
                "GEMINI_SYNC_TARGET": str(lexical_target),
                "GEMINI_SYNC_PLUGIN_CACHE": str(lexical_cache),
            },
        ):
            imported = load_sync_module()

        self.assertEqual(imported.GEMINI_SKILLS_DIR, lexical_target)
        self.assertEqual(imported.CLAUDE_PLUGINS_CACHE, lexical_cache)
        with self.assertRaises(imported.SyncError):
            with imported.exclusive_sync_lock():
                pass
        with self.assertRaises(imported.SyncError):
            imported.plugin_roots("plugin@market")
        self.assertEqual(list(external.iterdir()), [])

    def test_active_lock_prevents_recovery_by_second_sync(self):
        with self.module.exclusive_sync_lock():
            transaction = self.target / self.module.TRANSACTION_NAME
            transaction.mkdir()
            sentinel = transaction / "active-owner.txt"
            sentinel.write_text("preserve\n", encoding="utf-8")

            with self.assertRaises(self.module.SyncError):
                self.run_sync()

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")

    def test_recovery_preserves_unexpected_current_target_and_backup(self):
        source = write_skill(self.canonical, "refresh", "Version one.")
        self.run_sync()
        (source / "SKILL.md").write_text(
            "---\nname: refresh\ndescription: Version two.\n---\n", encoding="utf-8"
        )
        original_write_manifest = self.module.write_manifest

        def interrupt_manifest(_entries):
            raise KeyboardInterrupt("injected interruption")

        self.module.write_manifest = interrupt_manifest
        with self.assertRaises(KeyboardInterrupt):
            self.run_sync()
        target_manifest = self.target / "refresh" / "SKILL.md"
        target_manifest.write_text(
            target_manifest.read_text(encoding="utf-8") + "unexpected\n",
            encoding="utf-8",
        )
        self.module.write_manifest = original_write_manifest

        with self.assertRaises(self.module.SyncError):
            self.run_sync()

        self.assertIn("unexpected", target_manifest.read_text(encoding="utf-8"))
        transaction = self.target / self.module.TRANSACTION_NAME
        self.assertTrue((transaction / "backups" / "refresh").is_dir())
        self.assertTrue((transaction / "journal.json").is_file())

    def test_recovery_refuses_to_overwrite_unexpected_manifest(self):
        source = write_skill(self.canonical, "refresh", "Version one.")
        self.run_sync()
        (source / "SKILL.md").write_text(
            "---\nname: refresh\ndescription: Version two.\n---\n", encoding="utf-8"
        )
        original_write_manifest = self.module.write_manifest

        def interrupt_manifest(_entries):
            raise KeyboardInterrupt("injected interruption")

        self.module.write_manifest = interrupt_manifest
        with self.assertRaises(KeyboardInterrupt):
            self.run_sync()
        manifest = self.target / MANIFEST_NAME
        unexpected = b'{"unexpected": true}\n'
        manifest.write_bytes(unexpected)
        manifest.chmod(0o600)
        self.module.write_manifest = original_write_manifest

        with self.assertRaises(self.module.SyncError):
            self.run_sync()

        self.assertEqual(manifest.read_bytes(), unexpected)
        transaction = self.target / self.module.TRANSACTION_NAME
        self.assertTrue((transaction / "manifest.backup").is_file())
        self.assertTrue((transaction / "journal.json").is_file())

    def test_canonical_source_is_revalidated_after_staging_before_activation(self):
        write_skill(self.canonical, "alpha", "Alpha.")
        original_stage = self.module.stage_skill
        staged = False

        def record_stage(*args, **kwargs):
            nonlocal staged
            original_stage(*args, **kwargs)
            staged = True

        def reject_changed_source(_roots):
            self.assertTrue(staged)
            raise self.module.SyncError("canonical source changed after staging")

        self.module.stage_skill = record_stage
        self.module.revalidate_canonical_sources = reject_changed_source

        with self.assertRaises(self.module.SyncError):
            self.run_sync()

        self.assertFalse((self.target / "alpha").exists())
        self.assertFalse((self.target / self.module.TRANSACTION_NAME).exists())

    def test_component_guard_rejects_mocked_windows_reparse_attribute(self):
        reparse_flag = getattr(self.module.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        component = self.root / "reparse-component"
        component.mkdir()
        original_lstat = self.module.os.lstat

        def lstat_with_reparse(path):
            result = original_lstat(path)
            if Path(path) == component:
                return SimpleNamespace(
                    st_mode=result.st_mode,
                    st_file_attributes=reparse_flag,
                )
            return result

        with mock.patch.object(self.module.os, "lstat", side_effect=lstat_with_reparse):
            with self.assertRaises(self.module.SyncError):
                self.module.assert_no_symlink_components(
                    component / "target", "mocked Windows path"
                )

    def test_component_reparse_helper_honors_path_is_junction(self):
        path = mock.Mock()
        path.is_junction.return_value = True
        info = SimpleNamespace(st_mode=self.module.stat.S_IFDIR, st_file_attributes=0)

        self.assertTrue(self.module.component_is_link_or_reparse(path, info))

    @unittest.skipUnless(os.name == "nt", "real junction test requires Windows")
    def test_component_guard_rejects_real_windows_junction(self):
        external = self.root / "junction-external"
        junction = self.root / "junction"
        external.mkdir()
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(external)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.skipTest(f"junction creation unavailable: {result.stderr.strip()}")

        with self.assertRaises(self.module.SyncError):
            self.module.assert_no_symlink_components(
                junction / "target", "real Windows junction path"
            )

    def test_windows_process_probe_reports_active_and_closes_handle(self):
        kernel = FakeWindowsKernel(
            handle=123,
            exit_code=self.module.WINDOWS_STILL_ACTIVE,
            wait_result=self.module.WINDOWS_WAIT_TIMEOUT,
        )
        with mock.patch.object(
            self.module, "load_windows_kernel32", return_value=kernel
        ):
            self.assertTrue(self.module.windows_process_is_active(9876))

        self.assertEqual(kernel.closed_handles, [123])

    def test_windows_process_probe_reports_exited_and_closes_handle(self):
        kernel = FakeWindowsKernel(
            handle=456,
            exit_code=0,
            wait_result=self.module.WINDOWS_WAIT_OBJECT_0,
        )
        with mock.patch.object(
            self.module, "load_windows_kernel32", return_value=kernel
        ):
            self.assertFalse(self.module.windows_process_is_active(9876))

        self.assertEqual(kernel.closed_handles, [456])

    def test_windows_process_probe_fails_closed_on_access_or_query_error(self):
        inaccessible = FakeWindowsKernel(handle=0)
        query_error = FakeWindowsKernel(
            handle=789,
            exit_code=0,
            wait_result=self.module.WINDOWS_WAIT_OBJECT_0,
            query_succeeds=False,
        )
        with mock.patch.object(
            self.module, "load_windows_kernel32", return_value=inaccessible
        ):
            self.assertTrue(self.module.windows_process_is_active(9876))
        with mock.patch.object(
            self.module, "load_windows_kernel32", return_value=query_error
        ):
            self.assertTrue(self.module.windows_process_is_active(9876))

        self.assertEqual(query_error.closed_handles, [789])

    def test_windows_owner_probe_never_invokes_os_kill(self):
        owner = {
            "token": "a" * 32,
            "pid": 9876,
            "host": self.module.socket.gethostname(),
            "createdAt": "2026-10-30T00:00:00+00:00",
        }
        with (
            mock.patch.object(self.module, "running_on_windows", return_value=True),
            mock.patch.object(
                self.module, "windows_process_is_active", return_value=True
            ) as process_probe,
            mock.patch.object(self.module.os, "kill") as kill,
        ):
            self.assertTrue(self.module.owner_is_active(owner))

        process_probe.assert_called_once_with(9876)
        kill.assert_not_called()


class FakeWindowsKernel:
    def __init__(
        self,
        *,
        handle: int,
        exit_code: int = 0,
        wait_result: int = 0,
        query_succeeds: bool = True,
    ):
        self.handle = handle
        self.exit_code = exit_code
        self.wait_result = wait_result
        self.query_succeeds = query_succeeds
        self.closed_handles: list[int] = []

    def OpenProcess(self, _access, _inherit, _pid):
        return self.handle

    def GetExitCodeProcess(self, _handle, code_pointer):
        code_pointer._obj.value = self.exit_code
        return self.query_succeeds

    def WaitForSingleObject(self, _handle, _timeout):
        return self.wait_result

    def CloseHandle(self, handle):
        self.closed_handles.append(handle)
        return True


if __name__ == "__main__":
    unittest.main()
