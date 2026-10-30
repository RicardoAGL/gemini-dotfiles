#!/usr/bin/env python3
"""Filesystem-level tests for the managed Claude-to-Gemini skill sync."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "sync-claude-plugins.py"
MANIFEST_NAME = ".claude-sync-manifest.json"


def load_sync_module():
    spec = importlib.util.spec_from_file_location("sync_claude_plugins", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_skill(root: Path, name: str, description: str, extras: dict[str, str] | None = None) -> Path:
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
        self.root = Path(self.temp_dir.name)
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
        self.module.get_canonical_skills_dirs = lambda: [self.canonical]

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
        self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_unmanaged_same_name_target_is_preserved_and_sync_fails(self):
        write_skill(self.canonical, "collision", "Canonical version.")
        unmanaged = write_skill(self.target, "collision", "User-owned version.")
        before = (unmanaged / "SKILL.md").read_text(encoding="utf-8")

        with self.assertRaises(self.module.SyncError):
            self.run_sync()

        self.assertEqual((unmanaged / "SKILL.md").read_text(encoding="utf-8"), before)
        self.assertFalse((self.target / MANIFEST_NAME).exists())

    def test_managed_skill_refreshes_and_second_identical_run_is_unchanged(self):
        source = write_skill(self.canonical, "refreshable", "Version one.")
        first_result, _ = self.run_sync()
        first_manifest = self.read_manifest()
        installed = self.target / "refreshable"
        first_inode = installed.stat().st_ino

        (source / "SKILL.md").write_text(
            "---\nname: refreshable\ndescription: Version two.\n---\n",
            encoding="utf-8",
        )
        second_result, _ = self.run_sync()
        second_manifest = self.read_manifest()
        third_result, third_output = self.run_sync()

        self.assertEqual((first_result, second_result, third_result), (0, 0, 0))
        self.assertNotEqual(
            first_manifest["entries"]["refreshable"]["sha256"],
            second_manifest["entries"]["refreshable"]["sha256"],
        )
        self.assertIn("Version two", (installed / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(installed.stat().st_ino, first_inode)
        self.assertIn("unchanged: refreshable", third_output)

    def test_real_plugin_skill_package_beats_command_document(self):
        self.settings.write_text(
            json.dumps({"enabledPlugins": {"coderlm@test-marketplace": True}}),
            encoding="utf-8",
        )
        version = self.cache / "test-marketplace" / "coderlm" / "1.0.0"
        (version / "commands").mkdir(parents=True)
        (version / "commands" / "coderlm.md").write_text("# Command, not a skill\n", encoding="utf-8")
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
        os.utime(old, ns=(1_000_000_000, 1_000_000_000))
        os.utime(new, ns=(2_000_000_000, 2_000_000_000))

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

    def test_stale_unmodified_managed_skill_is_removed(self):
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
        self.assertIn("removed stale managed skill: stale-skill", second_output)

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


if __name__ == "__main__":
    unittest.main()
