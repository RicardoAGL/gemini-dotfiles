#!/usr/bin/env python3
"""Contract tests for the cross-platform GitHub Actions baseline."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
CHECKOUT_SHA = "d23441a48e516b6c34aea4fa41551a30e30af803"


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_triggers_permissions_and_concurrency_are_restricted(self):
        self.assertRegex(self.text, r"(?m)^on:\s*$")
        self.assertRegex(self.text, r"(?m)^  pull_request:\s*$")
        self.assertRegex(self.text, r"(?m)^  push:\s*$")
        self.assertRegex(self.text, r"(?m)^      - main\s*$")
        self.assertRegex(self.text, r"(?m)^  workflow_dispatch:\s*$")
        self.assertRegex(self.text, r"(?m)^permissions:\s*\n  contents: read\s*$")
        self.assertRegex(self.text, r"(?m)^concurrency:\s*$")
        self.assertIn("github.event.pull_request.number || github.ref", self.text)
        self.assertRegex(self.text, r"(?m)^  cancel-in-progress: true\s*$")

    def test_jobs_cover_linux_and_windows_local_baselines(self):
        self.assertRegex(self.text, r"(?m)^  linux:\s*$")
        self.assertRegex(self.text, r"(?m)^    runs-on: ubuntu-latest\s*$")
        self.assertIn("run: bash scripts/ci/local-checks.sh", self.text)
        self.assertRegex(self.text, r"(?m)^  windows:\s*$")
        self.assertRegex(self.text, r"(?m)^    runs-on: windows-latest\s*$")
        self.assertIn("shell: pwsh", self.text)
        self.assertIn("run: ./scripts/ci/local-checks.ps1", self.text)
        self.assertGreaterEqual(self.text.count("timeout-minutes:"), 2)

    def test_every_action_is_immutably_pinned(self):
        uses = re.findall(r"(?m)^\s+uses:\s*([^\s#]+)", self.text)
        self.assertEqual(
            uses,
            [
                f"actions/checkout@{CHECKOUT_SHA}",
                f"actions/checkout@{CHECKOUT_SHA}",
            ],
        )
        self.assertNotRegex(self.text, r"uses:\s*[^\s]+@(main|master|v\d+)\b")
        hardened_checkout = re.findall(
            rf"(?m)^\s+uses:\s*actions/checkout@{CHECKOUT_SHA}\s*$"
            r"\n^\s+with:\s*$"
            r"\n^\s+persist-credentials:\s*false\s*$",
            self.text,
        )
        self.assertEqual(len(hardened_checkout), 2)
        self.assertEqual(self.text.count("persist-credentials: false"), 2)


if __name__ == "__main__":
    unittest.main()
