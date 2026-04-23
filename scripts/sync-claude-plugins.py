#!/usr/bin/env python3
import json
import os
import shutil
import re
from pathlib import Path

CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
CLAUDE_PLUGINS_CACHE = Path.home() / ".claude" / "plugins" / "cache"
GEMINI_SKILLS_DIR = Path.home() / ".gemini" / "skills"

def get_enabled_plugins():
    if not CLAUDE_SETTINGS_PATH.exists():
        return []
    
    try:
        settings = json.loads(CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8"))
        plugins_dict = settings.get("enabledPlugins", {})
        # Return plugins that are explicitly true
        return [k for k, v in plugins_dict.items() if v]
    except Exception as e:
        print(f"Error reading settings: {e}")
        return []

def extract_skills_from_plugin(plugin_dir, plugin_name):
    # Find the latest version folder or unknown
    subdirs = [d for d in plugin_dir.iterdir() if d.is_dir()]
    if not subdirs:
        return
    
    # Process all version subdirs
    for version_dir in subdirs:
        # Check agents/, commands/, skills/
        targets = ["agents", "commands", "skills"]
        
        for target in targets:
            target_dir = version_dir / target
            if not target_dir.exists():
                continue
                
            for file_path in target_dir.rglob("*.md"):
                if file_path.name.lower() == "readme.md":
                    continue
                    
                skill_name = file_path.stem
                if skill_name.lower() == "skill":
                    skill_name = file_path.parent.name
                
                # Exclude reference and template files deep in the directory structure
                if "references" in file_path.parts or "templates" in file_path.parts:
                    continue
                    
                print(f"  Found '{target}' item: {skill_name}")
                
                content = file_path.read_text(encoding="utf-8")
                
                # Write to gemini skills dir
                gemini_skill_dir = GEMINI_SKILLS_DIR / skill_name
                gemini_skill_dir.mkdir(parents=True, exist_ok=True)
                
                gemini_skill_path = gemini_skill_dir / "SKILL.md"
                gemini_skill_path.write_text(content, encoding="utf-8")
                
                print(f"    -> Exported to {gemini_skill_path}")

def main():
    print("Syncing enabled Claude plugins to Gemini skills...")
    enabled = get_enabled_plugins()
    if not enabled:
        print("No enabled plugins found.")
        return
        
    GEMINI_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        
    for plugin_id in enabled:
        plugin_name = plugin_id.split('@')[0]
        
        found = False
        for marketplace_dir in CLAUDE_PLUGINS_CACHE.iterdir():
            if not marketplace_dir.is_dir():
                continue
                
            plugin_dir = marketplace_dir / plugin_name
            if plugin_dir.exists():
                print(f"Processing plugin: {plugin_name}")
                extract_skills_from_plugin(plugin_dir, plugin_name)
                found = True
                
        if not found:
            print(f"Warning: Cache directory for plugin '{plugin_name}' not found.")

if __name__ == "__main__":
    main()
