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
from unittest import mock


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

    def test_attestation_rejects_redirects_in_parent_components(self):
        module = load_installer()
        redirected_parent = self.root / "redirected-parent"
        try:
            os.symlink(self.root, redirected_parent, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"directory symlink unavailable: {error}")

        entry = {
            "name": "claude-governance",
            "enabled": True,
            "version": "1.0.0",
            "path": str(self.installed),
            "hooks": ["claude-governance"],
        }
        with self.assertRaises(module.GovernanceInstallError):
            module.verify_current_plugin(redirected_parent / "source", [entry])

        entry["path"] = str(redirected_parent / "installed")
        with self.assertRaises(module.GovernanceInstallError):
            module.verify_current_plugin(self.source, [entry])

    def test_attestation_rejects_mocked_windows_reparse_components(self):
        module = load_installer()
        reparse_flag = getattr(module.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        component = self.root / "reparse-component"
        component.mkdir()
        original_lstat = module.os.lstat

        def lstat_with_reparse(path):
            result = original_lstat(path)
            if Path(path) == component:
                return SimpleNamespace(
                    st_mode=result.st_mode,
                    st_file_attributes=reparse_flag,
                )
            return result

        with mock.patch.object(module.os, "lstat", side_effect=lstat_with_reparse):
            with self.assertRaises(module.GovernanceInstallError):
                module.assert_no_redirect_components(
                    component / "plugin", "mocked Windows plugin path"
                )

    def test_attestation_reparse_helper_honors_path_is_junction(self):
        module = load_installer()
        candidate = mock.Mock()
        candidate.is_junction.return_value = True
        info = SimpleNamespace(st_mode=module.stat.S_IFDIR, st_file_attributes=0)
        self.assertTrue(module.component_is_link_or_reparse(candidate, info))

    @unittest.skipUnless(os.name == "nt", "real junction test requires Windows")
    def test_attestation_rejects_real_windows_junction(self):
        module = load_installer()
        external = self.root / "junction-external"
        junction = self.root / "junction"
        external.mkdir()
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(external)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.skipTest(f"junction creation unavailable: {result.stderr.strip()}")
        with self.assertRaises(module.GovernanceInstallError):
            module.assert_no_redirect_components(junction / "plugin", "real junction")

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
        self.assertLess(
            powershell.index("Remove-GovernanceAgyx @agyxParameters"),
            powershell.index("& $pythonCommand.Source $governanceInstaller"),
        )
        self.assertLess(
            powershell.index("& $pythonCommand.Source $governanceInstaller"),
            powershell.index("Install-GovernanceAgyx @agyxParameters"),
        )
        self.assertIn("--check-only", (ROOT / "scripts" / "lib.sh").read_text(encoding="utf-8"))
        self.assertIn("--check-only", (ROOT / "scripts" / "lib.ps1").read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("bash"), "Bash cleanup test requires Bash")
    def test_unix_agyx_cleanup_preserves_a_profile_with_corrupt_markers(self):
        profile = self.root / "profile"
        original = "# >>> gemini-dotfiles agyx >>>\nkeep-me\n"
        profile.write_text(original, encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(self.root),
                "PROFILE_UNDER_TEST": str(profile),
                "WRAPPER_UNDER_TEST": str(self.root / ".local" / "bin" / "agyx"),
            }
        )
        result = subprocess.run(
            [
                shutil.which("bash"),
                "-c",
                'source scripts/lib.sh; remove_unix_agyx "$PROFILE_UNDER_TEST" "$WRAPPER_UNDER_TEST"',
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(profile.read_text(encoding="utf-8"), original)

    @unittest.skipUnless(shutil.which("bash"), "Bash cleanup test requires Bash")
    def test_unix_agyx_cleanup_preserves_foreign_marker_and_wrapper_collisions(self):
        profile = self.root / "profile"
        expected_wrapper = self.root / ".local" / "bin" / "agyx"
        foreign_profile = (
            "# >>> gemini-dotfiles agyx >>>\n"
            "alias agyx=/foreign/agyx\n"
            "# <<< gemini-dotfiles agyx <<<\n"
        )
        profile.write_text(foreign_profile, encoding="utf-8")
        expected_wrapper.parent.mkdir(parents=True)
        expected_wrapper.write_text("foreign wrapper\n", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(self.root),
                "PROFILE_UNDER_TEST": str(profile),
                "WRAPPER_UNDER_TEST": str(expected_wrapper),
            }
        )

        profile_result = subprocess.run(
            [
                shutil.which("bash"),
                "-c",
                'source scripts/lib.sh; remove_unix_agyx "$PROFILE_UNDER_TEST" "$WRAPPER_UNDER_TEST"',
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(profile_result.returncode, 0)
        self.assertEqual(profile.read_text(encoding="utf-8"), foreign_profile)

        wrapper_result = subprocess.run(
            [
                shutil.which("bash"),
                "-c",
                "source scripts/lib.sh; "
                "remove_governance_agyx_wrapper python3 /installer.py /plugin /agy",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(wrapper_result.returncode, 0)
        self.assertEqual(expected_wrapper.read_text(encoding="utf-8"), "foreign wrapper\n")

    @unittest.skipUnless(shutil.which("bash"), "Bash cleanup test requires Bash")
    def test_unix_exact_legacy_and_current_profile_entries_are_reconciled(self):
        profile = self.root / "profile"
        wrapper = self.root / ".local" / "bin" / "agyx"
        profile.write_text(
            "keep-before\nalias agyx='agy --dangerously-skip-permissions'\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(self.root),
                "PROFILE_UNDER_TEST": str(profile),
                "WRAPPER_UNDER_TEST": str(wrapper),
            }
        )
        result = subprocess.run(
            [
                shutil.which("bash"),
                "-c",
                "source scripts/lib.sh; "
                'remove_unix_agyx "$PROFILE_UNDER_TEST" "$WRAPPER_UNDER_TEST"; '
                'ensure_unix_agyx "$PROFILE_UNDER_TEST" "$WRAPPER_UNDER_TEST"; '
                'remove_unix_agyx "$PROFILE_UNDER_TEST" "$WRAPPER_UNDER_TEST"',
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(profile.read_text(encoding="utf-8").strip(), "keep-before")

    @unittest.skipUnless(shutil.which("bash"), "Bash cleanup test requires Bash")
    def test_unix_owned_profile_accepts_windows_wrapper_backslashes(self):
        profile = self.root / "profile"
        profile.write_text("keep-before\n", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "PROFILE_UNDER_TEST": str(profile),
                "WRAPPER_UNDER_TEST": r"C:\Users\runner\.local\bin\agyx",
            }
        )
        result = subprocess.run(
            [
                shutil.which("bash"),
                "-c",
                "source scripts/lib.sh; "
                'ensure_unix_agyx "$PROFILE_UNDER_TEST" "$WRAPPER_UNDER_TEST"; '
                'remove_unix_agyx "$PROFILE_UNDER_TEST" "$WRAPPER_UNDER_TEST"',
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(profile.read_text(encoding="utf-8").strip(), "keep-before")

    @unittest.skipUnless(shutil.which("bash"), "Bash cleanup test requires Bash")
    def test_unix_owned_agyx_wrapper_is_quarantined_not_deleted(self):
        environment = os.environ.copy()
        environment["HOME"] = str(self.root)
        result = subprocess.run(
            [
                shutil.which("bash"),
                "-c",
                "source scripts/lib.sh; "
                "install_governance_agyx_wrapper python3 /installer.py /plugin /agy >/dev/null; "
                "remove_governance_agyx_wrapper python3 /installer.py /plugin /agy",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        wrapper = self.root / ".local" / "bin" / "agyx"
        self.assertFalse(wrapper.exists())
        quarantined = list(
            (wrapper.parent / ".gemini-dotfiles-quarantine").glob("agyx.*")
        )
        self.assertEqual(len(quarantined), 1)
        self.assertIn("--check-only", quarantined[0].read_text(encoding="utf-8"))

    def test_windows_sources_preserve_unmanaged_ps1_and_use_selected_python(self):
        powershell = (ROOT / "scripts" / "lib.ps1").read_text(encoding="utf-8")
        self.assertNotIn("foreach ($name in @('agyx.cmd', 'agyx.ps1'))", powershell)
        self.assertIn("Resolve-PythonCommand", powershell)
        self.assertIn("New-InfisicalCmdContent", powershell)


if __name__ == "__main__":
    unittest.main()
