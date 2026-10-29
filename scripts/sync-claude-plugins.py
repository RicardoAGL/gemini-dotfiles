#!/usr/bin/env python3
"""
Sync Claude Skills and Plugins to Gemini / Antigravity.

Mirrors:
1. Canonical dotfiles skills (from claude-dotfiles/skills or ~/.claude/skills) via symlinks
   (falling back to copy on platforms where symlinks are restricted).
2. Enabled Claude plugin skills (from ~/.claude/plugins/cache).
"""
import json
import os
import shutil
import sys
from pathlib import Path

CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
CLAUDE_PLUGINS_CACHE = Path.home() / ".claude" / "plugins" / "cache"
CLAUDE_USER_SKILLS = Path.home() / ".claude" / "skills"
GEMINI_SKILLS_DIR = Path.home() / ".gemini" / "skills"

def get_canonical_skills_dirs():
    """Find directories containing canonical skills (claude-dotfiles, ~/.claude/skills)."""
    candidates = []
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    source_map_path = repo_root / "source-map.json"

    if source_map_path.exists():
        try:
            sm = json.loads(source_map_path.read_text(encoding="utf-8"))
            claude_repo_name = sm.get("canonical", {}).get("claudeDotfilesRepo", "claude-dotfiles")
            repo_skills = repo_root.parent / claude_repo_name / "skills"
            if repo_skills.exists() and repo_skills.is_dir():
                candidates.append(repo_skills)
        except Exception as e:
            print(f"Warning reading source-map.json: {e}")

    # Standard workspace locations
    for parent in [
        Path.home() / "Desktop" / "github",
        Path.home() / "desktop" / "github",
        Path("C:/github"),
    ]:
        repo_skills = parent / "claude-dotfiles" / "skills"
        if repo_skills.exists() and repo_skills.is_dir() and repo_skills not in candidates:
            candidates.append(repo_skills)

    # Runtime fallback
    if CLAUDE_USER_SKILLS.exists() and CLAUDE_USER_SKILLS.is_dir() and CLAUDE_USER_SKILLS not in candidates:
        candidates.append(CLAUDE_USER_SKILLS)

    return candidates

def get_enabled_plugins():
    if not CLAUDE_SETTINGS_PATH.exists():
        return []

    try:
        settings = json.loads(CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8"))
        plugins_dict = settings.get("enabledPlugins", {})
        return [k for k, v in plugins_dict.items() if v]
    except Exception as e:
        print(f"Error reading Claude settings: {e}")
        return []

def link_or_copy_skill(src_dir: Path, target_dir: Path) -> str:
    """Link source skill directory to target, falling back to copy."""
    resolved_src = src_dir.resolve()
    if not resolved_src.exists():
        return "missing_source"

    if target_dir.is_symlink():
        try:
            if target_dir.resolve() == resolved_src:
                return "already_linked"
            target_dir.unlink()
        except OSError:
            target_dir.unlink()
    elif target_dir.exists():
        if target_dir.is_file():
            target_dir.unlink()
        elif target_dir.is_dir():
            shutil.rmtree(target_dir)

    try:
        target_dir.symlink_to(resolved_src, target_is_directory=True)
        return "linked"
    except (OSError, NotImplementedError):
        shutil.copytree(resolved_src, target_dir)
        return "copied"

def sync_canonical_skills():
    """Sync canonical skills into GEMINI_SKILLS_DIR."""
    canonical_dirs = get_canonical_skills_dirs()
    if not canonical_dirs:
        print("No canonical skills directories found.")
        return 0

    GEMINI_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    synced_count = 0
    seen_skills = set()

    for base_dir in canonical_dirs:
        print(f"Scanning canonical skills in: {base_dir}")
        for entry in base_dir.iterdir():
            if entry.name.startswith("."):
                continue

            resolved_entry = entry.resolve()
            if not resolved_entry.is_dir():
                continue

            # Verify it is a skill (contains SKILL.md or skill.md)
            has_skill_md = (
                (resolved_entry / "SKILL.md").exists()
                or (resolved_entry / "skill.md").exists()
            )
            if not has_skill_md:
                continue

            skill_name = entry.name
            if skill_name in seen_skills:
                continue

            target_path = GEMINI_SKILLS_DIR / skill_name
            status = link_or_copy_skill(entry, target_path)
            seen_skills.add(skill_name)
            synced_count += 1
            if status in ("linked", "copied"):
                print(f"  {status}: {skill_name}")

    print(f"Canonical skills synced: {synced_count}")
    return synced_count

def extract_skills_from_plugin(plugin_dir, plugin_name):
    """Extract skills from a Claude plugin cache directory."""
    subdirs = [d for d in plugin_dir.iterdir() if d.is_dir()]
    if not subdirs:
        return 0

    extracted = 0
    for version_dir in subdirs:
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

                if "references" in file_path.parts or "templates" in file_path.parts:
                    continue

                # Don't overwrite canonical skills with plugin cache
                gemini_skill_dir = GEMINI_SKILLS_DIR / skill_name
                if gemini_skill_dir.exists() and (gemini_skill_dir.is_symlink() or (gemini_skill_dir / "SKILL.md").exists()):
                    continue

                content = file_path.read_text(encoding="utf-8")
                gemini_skill_dir.mkdir(parents=True, exist_ok=True)
                gemini_skill_path = gemini_skill_dir / "SKILL.md"
                gemini_skill_path.write_text(content, encoding="utf-8")
                extracted += 1
                print(f"  Plugin exported: {skill_name}")

    return extracted

def sync_plugin_skills():
    """Sync enabled Claude plugins into GEMINI_SKILLS_DIR."""
    if not CLAUDE_PLUGINS_CACHE.exists():
        return 0

    enabled = get_enabled_plugins()
    if not enabled:
        return 0

    print("Syncing enabled Claude plugins to Gemini skills...")
    total_plugins = 0
    for plugin_id in enabled:
        plugin_name = plugin_id.split('@')[0]
        found = False
        for marketplace_dir in CLAUDE_PLUGINS_CACHE.iterdir():
            if not marketplace_dir.is_dir():
                continue

            plugin_dir = marketplace_dir / plugin_name
            if plugin_dir.exists():
                print(f"Processing plugin: {plugin_name}")
                total_plugins += extract_skills_from_plugin(plugin_dir, plugin_name)
                found = True

        if not found:
            print(f"Notice: Cache directory for plugin '{plugin_name}' not found.")

    return total_plugins

def main():
    print("=== Syncing Skills & Plugins to Gemini/Antigravity ===")
    canonical_count = sync_canonical_skills()
    plugin_count = sync_plugin_skills()

    total = len([
        d for d in GEMINI_SKILLS_DIR.iterdir()
        if d.is_dir() and ((d / "SKILL.md").exists() or (d / "skill.md").exists())
    ])
    print(f"Done. Canonical: {canonical_count}, Plugins: {plugin_count}, Total active skills: {total}")

if __name__ == "__main__":
    main()
