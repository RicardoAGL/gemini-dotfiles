#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo '==> bash syntax'
bash -n "$root/scripts/lib.sh" "$root/scripts/install-gemini-cli.sh" "$root/scripts/bootstrap.sh" "$root/scripts/use-project.sh"
echo 'OK  bash syntax'

echo '==> source-map structure'
PYTHONDONTWRITEBYTECODE=1 python3 - <<PY
import json, pathlib
path = pathlib.Path(r"$root/source-map.json")
data = json.loads(path.read_text())
assert data["global"]["contextFileName"] == "GEMINI.md"
assert data["global"]["settingsFileName"] == "settings.json"
assert data["integrations"]["infisicalWrapper"]["repoPath"] == "infisical-safe-wrapper"
assert data["integrations"]["infisicalWrapper"]["remoteUrl"] == "https://github.com/RicardoAGL/infisical-safe-wrapper.git"
print("OK  source-map structure")
PY

echo '==> GitHub Actions workflow contract'
PYTHONDONTWRITEBYTECODE=1 python3 "$root/tests/test_ci_workflow.py" -v
echo 'OK  GitHub Actions workflow contract'

echo '==> Infisical wrapper boundary tests'
bash "$root/tests/infisical-wrapper/run-tests.sh"
echo 'OK  Infisical wrapper boundary tests'

echo '==> managed skill sync tests'
PYTHONDONTWRITEBYTECODE=1 python3 "$root/tests/test_sync_claude_plugins.py" -v
echo 'OK  managed skill sync tests'

echo '==> governance plugin tests'
if ! command -v node >/dev/null 2>&1; then
  echo 'ERROR: Node.js is required to validate the governance hooks.' >&2
  exit 1
fi
node --check "$root/plugins/claude-governance/scripts/bridge-pretooluse.js"
node --check "$root/plugins/claude-governance/scripts/bridge-stop.js"
node --test "$root/tests/governance-hooks.test.js"
PYTHONDONTWRITEBYTECODE=1 python3 "$root/tests/test_governance_install.py" -v
echo 'OK  governance plugin tests'

echo
echo 'Local CI baseline passed.'
