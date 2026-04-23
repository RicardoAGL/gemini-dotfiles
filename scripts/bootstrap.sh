#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

generated_context="$(write_generated_global_gemini)"
target_context="$(get_gemini_context_path)"
mode="$(link_or_copy "$generated_context" "$target_context")"

merge_managed_settings "$(get_gemini_settings_path)"
ensure_unix_alias "$HOME/.zshrc"
ensure_unix_alias "$HOME/.bashrc"

echo "Global GEMINI.md: $mode -> $target_context"
echo "Managed settings: $(get_gemini_settings_path)"
echo "Shell alias ensured: gem -> gemini --approval-mode=yolo"

