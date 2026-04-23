#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

project_root="${1:?project root required}"
if [[ ! -d "$project_root" ]]; then
  echo "Project root not found: $project_root" >&2
  exit 1
fi

if [[ ! -f "$project_root/CLAUDE.md" && ! -f "$project_root/AGENTS.md" ]]; then
  echo "Skipping Gemini wrapper because no CLAUDE.md or AGENTS.md exists in $project_root"
  exit 0
fi

target="$(write_project_gemini_wrapper "$project_root")"
echo "Prepared Gemini context wrapper: $target"

