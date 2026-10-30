#!/usr/bin/env python3
"""Governance plugin installation and bootstrap exposure tests."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-governance-plugin.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("install_governance_plugin", INSTALLER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {INSTALLER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GovernanceInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.source = self.root / "source"
        self.installed = self.root / "installed"
        self.source.mkdir()
        self.installed.mkdir()
        (self.source / "plugin.json").write_text(
            json.dumps({"name": "claude-governance", "version": "1.0.0"}),
            encoding="utf-8",
        )
        (self.source / "hooks.json").write_text("{}\n", encoding="utf-8")
        (self.installed / "plugin.json").write_bytes((self.source / "plugin.json").read_bytes())
        (self.installed / "hooks.json").write_bytes((self.source / "hooks.json").read_bytes())

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_install_validates_installs_enables_and_verifies_current_copy(self):
        module = load_installer()
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            if command[-2:] == ["list", "--json"]:
                stdout = json.dumps(
                    [
                        {
                            "name": "claude-governance",
                            "enabled": True,
                            "version": "1.0.0",
                            "path": str(self.installed),
                            "hooks": ["claude-governance"],
                        }
                    ]
                )
            else:
                stdout = ""
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

        module.install_and_verify("agy", self.source, runner=runner)

        self.assertEqual(calls[0], ["agy", "plugin", "validate", "--strict", str(self.source)])
        self.assertEqual(calls[1], ["agy", "plugin", "install", str(self.source)])
        self.assertEqual(calls[2], ["agy", "plugin", "enable", "claude-governance"])
        self.assertEqual(calls[3], ["agy", "plugin", "list", "--json"])

    def test_verification_rejects_disabled_stale_or_failed_plugin(self):
        module = load_installer()
        bad_entries = [
            {"name": "claude-governance", "enabled": False, "version": "1.0.0", "path": str(self.installed)},
            {"name": "claude-governance", "enabled": True, "version": "0.9.0", "path": str(self.installed)},
            {
                "name": "claude-governance",
                "enabled": True,
                "version": "1.0.0",
                "path": str(self.installed),
                "hooks": ["different-hook"],
            },
        ]
        for entry in bad_entries:
            with self.subTest(entry=entry):
                with self.assertRaises(module.GovernanceInstallError):
                    module.verify_current_plugin(self.source, [entry])

        def failed_runner(_command, **_kwargs):
            return SimpleNamespace(returncode=1, stdout="", stderr="failed")

        with self.assertRaises(module.GovernanceInstallError):
            module.install_and_verify("agy", self.source, runner=failed_runner)

    def test_verification_rejects_tampered_installed_files(self):
        module = load_installer()
        (self.installed / "hooks.json").write_text('{"changed": true}\n', encoding="utf-8")
        entry = {
            "name": "claude-governance",
            "enabled": True,
            "version": "1.0.0",
            "path": str(self.installed),
            "hooks": ["claude-governance"],
        }
        with self.assertRaises(module.GovernanceInstallError):
            module.verify_current_plugin(self.source, [entry])

    def test_verification_rejects_symlinked_or_extra_installed_content(self):
        module = load_installer()
        entry = {
            "name": "claude-governance",
            "enabled": True,
            "version": "1.0.0",
            "path": str(self.installed),
            "hooks": ["claude-governance"],
        }
        (self.installed / "unexpected.js").write_text("malicious\n", encoding="utf-8")
        with self.assertRaises(module.GovernanceInstallError):
            module.verify_current_plugin(self.source, [entry])

        (self.installed / "unexpected.js").unlink()
        (self.source / "scripts").mkdir()
        (self.source / "scripts" / "guard.js").write_text("safe\n", encoding="utf-8")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "guard.js").write_text("safe\n", encoding="utf-8")
        try:
            os.symlink(outside, self.installed / "scripts", target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"directory symlink unavailable: {error}")
        with self.assertRaises(module.GovernanceInstallError):
            module.verify_current_plugin(self.source, [entry])

    def test_install_rejects_malformed_plugin_list_and_spawn_errors(self):
        module = load_installer()

        def malformed_runner(command, **_kwargs):
            stdout = "not-json" if command[-2:] == ["list", "--json"] else ""
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

        with self.assertRaises(module.GovernanceInstallError):
            module.install_and_verify("agy", self.source, runner=malformed_runner)

        def missing_runner(_command, **_kwargs):
            raise FileNotFoundError("agy missing")

        with self.assertRaises(module.GovernanceInstallError):
            module.install_and_verify("agy", self.source, runner=missing_runner)

    def test_both_bootstraps_remove_then_verify_before_exposing_agyx(self):
        bash = (ROOT / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "scripts" / "bootstrap.ps1").read_text(encoding="utf-8")
        self.assertLess(bash.index("remove_unix_agyx"), bash.index("install-governance-plugin.py"))
        self.assertLess(bash.index("install-governance-plugin.py"), bash.index("ensure_unix_agyx"))
        self.assertLess(powershell.index("Remove-GovernanceAgyx"), powershell.index("install-governance-plugin.py"))
        self.assertLess(powershell.index("install-governance-plugin.py"), powershell.index("Install-GovernanceAgyx"))
        self.assertIn("--check-only", (ROOT / "scripts" / "lib.sh").read_text(encoding="utf-8"))
        self.assertIn("--check-only", (ROOT / "scripts" / "lib.ps1").read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("bash"), "Bash cleanup test requires Bash")
    def test_unix_agyx_cleanup_preserves_a_profile_with_corrupt_markers(self):
        profile = self.root / "profile"
        original = "# >>> gemini-dotfiles agyx >>>\nkeep-me\n"
        profile.write_text(original, encoding="utf-8")
        environment = os.environ.copy()
        environment.update({"HOME": str(self.root), "PROFILE_UNDER_TEST": str(profile)})
        result = subprocess.run(
            [
                shutil.which("bash"),
                "-c",
                'source scripts/lib.sh; remove_unix_agyx "$PROFILE_UNDER_TEST"',
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(profile.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
