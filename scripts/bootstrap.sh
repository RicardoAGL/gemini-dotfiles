#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

generated_context="$(write_generated_global_gemini)"
target_context="$(get_gemini_context_path)"
mode="$(link_or_copy "$generated_context" "$target_context")"

merge_managed_settings "$(get_gemini_settings_path)"
ensure_unix_alias "$HOME/.zshrc"
ensure_unix_alias "$HOME/.bashrc"
expected_agyx_wrapper="$(get_user_bin_dir)/agyx"
remove_unix_agyx "$HOME/.zshrc" "$expected_agyx_wrapper"
remove_unix_agyx "$HOME/.bashrc" "$expected_agyx_wrapper"
infisical_wrapper_repo="$(workspace_path_from_source_map "integrations.infisicalWrapper.repoPath")"
if [[ -d "$infisical_wrapper_repo" ]]; then
  ensure_infisical_safe_wrapper_shell_wrapper "$infisical_wrapper_repo"
else
  echo "Skipping infisical-safe-wrapper install because repo is missing: $infisical_wrapper_repo"
fi

python_cmd="$(get_python_cmd)"
if ! command -v "$python_cmd" >/dev/null 2>&1; then
  echo "ERROR: Python is required to establish Gemini governance and skill parity." >&2
  exit 1
fi
agy_cmd="$(command -v agy || true)"
if [[ -z "$agy_cmd" ]]; then
  echo "ERROR: agy is required to install the Antigravity governance plugin." >&2
  exit 1
fi
governance_installer="$(dirname "${BASH_SOURCE[0]}")/install-governance-plugin.py"
governance_plugin="$(get_gemini_dotfiles_root)/plugins/claude-governance"
remove_governance_agyx_wrapper "$python_cmd" "$governance_installer" "$governance_plugin" "$agy_cmd"
"$python_cmd" "$governance_installer" --agy "$agy_cmd" --plugin-dir "$governance_plugin"
agyx_wrapper="$(install_governance_agyx_wrapper "$python_cmd" "$governance_installer" "$governance_plugin" "$agy_cmd")"
ensure_unix_agyx "$HOME/.zshrc" "$agyx_wrapper"
ensure_unix_agyx "$HOME/.bashrc" "$agyx_wrapper"

"$python_cmd" "$(dirname "${BASH_SOURCE[0]}")/sync-claude-plugins.py"

echo "Global GEMINI.md: $mode -> $target_context"
echo "Managed settings: $(get_gemini_settings_path)"
echo "Shell aliases ensured: gem, verified agyx, antigravity"
echo "Shared wrapper ensured: infisical-safe-wrapper"
