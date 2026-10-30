#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../../scripts/lib.sh
source "$repo_root/scripts/lib.sh"

tmp="$(cd "$(mktemp -d)" && pwd -P)"
redirect_native=''

cleanup() {
  if [[ "${OS:-}" == "Windows_NT" && -n "$redirect_native" ]] && command -v powershell.exe >/dev/null 2>&1; then
    MSYS_NO_PATHCONV=1 powershell.exe -NoProfile -NonInteractive -Command \
      'if (Test-Path -LiteralPath $args[0]) { Remove-Item -LiteralPath $args[0] -Force -ErrorAction SilentlyContinue }' \
      "$redirect_native" >/dev/null 2>&1 || true
  fi
  rm -rf "$tmp"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

expect_failure() {
  if "$@" >/dev/null 2>&1; then
    fail "command unexpectedly succeeded: $*"
  fi
}

for bad_name in ../escape nested/repo . .. "bad'name" 'bad"name' $'bad\nname'; do
  expect_failure validate_workspace_repo_name "$bad_name"
done
validate_workspace_repo_name infisical-safe-wrapper
echo 'PASS: repository names are a single safe workspace segment'

origin="$tmp/origin.git"
checkout="$tmp/infisical-safe-wrapper"
git init --bare --initial-branch=main "$origin" >/dev/null
git clone "$origin" "$checkout" >/dev/null 2>&1
git -C "$checkout" config user.name Fixture
git -C "$checkout" config user.email fixture@example.invalid
mkdir -p "$checkout/bin"
cat > "$checkout/bin/infisical-safe-wrapper.sh" <<'FIXTURE'
#!/usr/bin/env bash
printf 'fixture:%s\n' "$*"
FIXTURE
chmod +x "$checkout/bin/infisical-safe-wrapper.sh"
cat > "$checkout/bin/infisical-safe-wrapper.ps1" <<'FIXTURE'
Write-Output "fixture:$args"
FIXTURE
git -C "$checkout" add bin
git -C "$checkout" commit -m 'test: add fixture wrappers' >/dev/null
git -C "$checkout" push -u origin main >/dev/null 2>&1
commit="$(git -C "$checkout" rev-parse HEAD)"

runtime="$repo_root/scripts/integrations/infisical-wrapper-runtime.py"
config="$tmp/runtime.json"
python3 - "$config" "$checkout" "$origin" "$commit" <<'PY'
import json, pathlib, sys
path, repo, remote, commit = sys.argv[1:]
pathlib.Path(path).write_text(json.dumps({
    "schemaVersion": 1,
    "repo": repo,
    "expectedRemote": remote,
    "expectedCommit": commit,
    "entrypoint": "bin/infisical-safe-wrapper.sh",
    "executor": "bash",
}) + "\n", encoding="utf-8")
PY

python3 "$runtime" --verify-only "$config"
[[ "$(python3 "$runtime" "$config" hello world)" == 'fixture:hello world' ]] || fail 'verified runtime did not execute fixture'
echo 'PASS: published checkout is pinned and executable through the verifier'

redirect="$tmp/redirected-repo"
redirect_created=0
if [[ "${OS:-}" == "Windows_NT" ]]; then
  if command -v powershell.exe >/dev/null 2>&1 && command -v cygpath >/dev/null 2>&1; then
    redirect_native="$(cygpath -w "$redirect")"
    checkout_native="$(cygpath -w "$checkout")"
    if MSYS_NO_PATHCONV=1 powershell.exe -NoProfile -NonInteractive -Command \
      '$link = $args[0]; $target = $args[1]; New-Item -ItemType Junction -Path $link -Target $target -ErrorAction Stop | Out-Null' \
      "$redirect_native" "$checkout_native"; then
      redirect_created=1
    fi
  fi
  if [[ "$redirect_created" -eq 0 ]]; then
    echo 'SKIP: Windows junction creation is unavailable; Linux symlink coverage remains active.'
  fi
else
  ln -s "$checkout" "$redirect"
  redirect_created=1
fi

if [[ "$redirect_created" -eq 1 ]]; then
python3 - "$config" "$redirect" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["repo"] = sys.argv[2]
path.write_text(json.dumps(data) + "\n", encoding="utf-8")
PY
expect_failure python3 "$runtime" --verify-only "$config"
if [[ "${OS:-}" == "Windows_NT" ]]; then
  MSYS_NO_PATHCONV=1 powershell.exe -NoProfile -NonInteractive -Command \
    'Remove-Item -LiteralPath $args[0] -Force -ErrorAction Stop' "$redirect_native"
else
  unlink "$redirect"
fi
python3 - "$config" "$checkout" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["repo"] = sys.argv[2]
path.write_text(json.dumps(data) + "\n", encoding="utf-8")
PY
echo 'PASS: redirecting repository paths fail closed'
fi

printf '# dirty\n' >> "$checkout/bin/infisical-safe-wrapper.sh"
expect_failure python3 "$runtime" --verify-only "$config"
git -C "$checkout" restore bin/infisical-safe-wrapper.sh

git -C "$checkout" switch -c feature >/dev/null
expect_failure python3 "$runtime" --verify-only "$config"
git -C "$checkout" switch main >/dev/null

mv "$checkout/bin/infisical-safe-wrapper.sh" "$checkout/bin/real.sh"
ln -s real.sh "$checkout/bin/infisical-safe-wrapper.sh"
expect_failure python3 "$runtime" --verify-only "$config"
unlink "$checkout/bin/infisical-safe-wrapper.sh"
mv "$checkout/bin/real.sh" "$checkout/bin/infisical-safe-wrapper.sh"
echo 'PASS: dirty, feature-branch, and symlinked entrypoints fail closed'

bin_dir="$tmp/bin"
home_dir="$tmp/home"
mkdir -p "$home_dir"
HOME="$home_dir" ensure_infisical_safe_wrapper_shell_wrapper "$checkout" "$bin_dir" "$origin"
[[ -x "$bin_dir/infisical-safe-wrapper" ]] || fail 'shell wrapper missing'
[[ -f "$bin_dir/.infisical-safe-wrapper-runtime.py" ]] || fail 'runtime snapshot missing'
[[ -f "$bin_dir/.infisical-safe-wrapper.json" ]] || fail 'runtime config missing'
[[ "$(HOME="$home_dir" "$bin_dir/infisical-safe-wrapper" safe arg)" == 'fixture:safe arg' ]] || fail 'installed wrapper failed'
grep -Fqx 'export PATH="$HOME/.local/bin:$PATH"' "$home_dir/.zshrc" || fail 'zsh PATH entry missing'
grep -Fqx 'export PATH="$HOME/.local/bin:$PATH"' "$home_dir/.bashrc" || fail 'bash PATH entry missing'
echo 'PASS: installed Unix wrapper is safely configured and placed on PATH'

printf '# drift\n' >> "$checkout/bin/infisical-safe-wrapper.sh"
expect_failure "$bin_dir/infisical-safe-wrapper" should-not-run
git -C "$checkout" restore bin/infisical-safe-wrapper.sh

printf '# next\n' >> "$checkout/bin/infisical-safe-wrapper.sh"
git -C "$checkout" add bin/infisical-safe-wrapper.sh
git -C "$checkout" commit -m 'test: advance published main' >/dev/null
git -C "$checkout" push origin main >/dev/null 2>&1
expect_failure "$bin_dir/infisical-safe-wrapper" should-not-run
echo 'PASS: installed wrapper rejects post-install mutation and commit drift'

expect_failure ensure_infisical_safe_wrapper_shell_wrapper "$checkout" "$tmp/other-bin" https://example.invalid/wrong.git
echo 'PASS: unexpected repository identity fails before installation'

grep -Fq 'Get-SafeWorkspaceRepoPath' "$repo_root/scripts/lib.ps1" || fail 'PowerShell safe workspace resolver missing'
grep -Fq 'infisical-wrapper-runtime.py' "$repo_root/scripts/lib.ps1" || fail 'PowerShell shared runtime integration missing'
grep -Fq '%~dp0\.infisical-safe-wrapper-runtime.py' "$repo_root/scripts/lib.ps1" || fail 'CMD wrapper is not adjacent-path based'
grep -Fq 'New-InfisicalCmdContent -PythonPath $pythonCommand.Source' "$repo_root/scripts/lib.ps1" || fail 'CMD wrapper does not use the validated Python executable'
grep -Fq '$PSScriptRoot' "$repo_root/scripts/lib.ps1" || fail 'PowerShell wrapper is not adjacent-path based'
echo 'PASS: PowerShell and CMD wrappers share the verified runtime without interpolated checkout paths'

echo 'All Infisical wrapper boundary tests passed.'
