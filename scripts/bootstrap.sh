#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

generated_context="$(write_generated_global_gemini)"
target_context="$(get_gemini_context_path)"
mode="$(link_or_copy "$generated_context" "$target_context")"

merge_managed_settings "$(get_gemini_settings_path)"
ensure_unix_alias "$HOME/.zshrc"
ensure_unix_alias "$HOME/.bashrc"
infisical_wrapper_repo="$(workspace_path_from_source_map "integrations.infisicalWrapper.repoPath")"
if [[ -d "$infisical_wrapper_repo" ]]; then
  ensure_infisical_safe_wrapper_shell_wrapper "$infisical_wrapper_repo"
else
  echo "Skipping infisical-safe-wrapper install because repo is missing: $infisical_wrapper_repo"
fi

if command -v agy >/dev/null 2>&1; then
  agy plugin install "$(get_gemini_dotfiles_root)/plugins/claude-governance" >/dev/null 2>&1 || true
fi

echo "Global GEMINI.md: $mode -> $target_context"
echo "Managed settings: $(get_gemini_settings_path)"
echo "Shell aliases ensured: gem, agyx, antigravity"
echo "Shared wrapper ensured: infisical-safe-wrapper"
