import json
import os
from pathlib import Path

gemini_settings_path = Path.home() / ".gemini" / "settings.json"
claude_settings_path = Path.home() / ".claude" / "settings.json"

if not gemini_settings_path.exists():
    print(f"Error: {gemini_settings_path} not found")
    exit(1)

settings = json.loads(gemini_settings_path.read_text(encoding="utf-8"))

# If hooks aren't there, maybe we need to copy them from claude settings first
if "hooks" not in settings and claude_settings_path.exists():
    print("Extracting hooks from Claude settings...")
    claude_settings = json.loads(claude_settings_path.read_text(encoding="utf-8"))
    settings["hooks"] = claude_settings.get("hooks", {})

hooks = settings.get("hooks", {})

mapping = {
    "PreToolUse": "BeforeTool",
    "PostToolUse": "AfterTool",
    "SubagentStart": "BeforeAgent",
    "SubagentStop": "AfterAgent",
    "Stop": "SessionEnd",
    "PreCompact": "PreCompress",
    "UserPromptSubmit": "BeforeModel"
}

new_hooks = {}
for claude_hook, actions in hooks.items():
    gemini_hook = mapping.get(claude_hook, claude_hook)
    
    if gemini_hook in new_hooks:
        new_hooks[gemini_hook].extend(actions)
    else:
        new_hooks[gemini_hook] = actions

settings["hooks"] = new_hooks

gemini_settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
print("Hook names successfully mapped!")