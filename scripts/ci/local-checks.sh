#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo '==> bash syntax'
bash -n "$root/scripts/lib.sh" "$root/scripts/install-gemini-cli.sh" "$root/scripts/bootstrap.sh" "$root/scripts/use-project.sh"
echo 'OK  bash syntax'

echo '==> source-map structure'
python3 - <<PY
import json, pathlib
path = pathlib.Path(r"$root/source-map.json")
data = json.loads(path.read_text())
assert data["global"]["contextFileName"] == "GEMINI.md"
assert data["global"]["settingsFileName"] == "settings.json"
assert data["integrations"]["infisicalWrapper"]["repoPath"] == "infisical-safe-wrapper"
print("OK  source-map structure")
PY

echo
echo 'Local CI baseline passed.'
