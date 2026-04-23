# Gemini Dotfiles Backlog

Project tracking for the Gemini CLI multi-agent rollout.

---

## Phase 1: Local Parity (M5 Machine)
Get the primary machine fully aligned with Claude Code capabilities.

### Milestone 1.1: Core Configuration
- [x] Bootstrapped global `GEMINI.md` baseline
- [x] Migrated hooks (`SessionStart`, `PostToolUse`, etc.)
- [x] Configured global MCP servers (`playwright`, `vibe-kanban`, `nexus`)
- [x] Enabled Agent Skills (1:1 mapping from Claude)

### Milestone 1.2: Plugin Migration
- [ ] **Create plugin synchronization script (`scripts/sync-claude-plugins.py`)**
  - **Status:** To Do
  - **Goal:** Read `~/.claude/plugins/cache/`, parse Markdown/YAML from `agents/`, `commands/`, `skills/`, and generate native `SKILL.md` files in `~/.gemini/skills/`.
- [ ] **Map Claude Hook Events to Gemini**
  - **Status:** To Do
  - **Goal:** Update the migrated `~/.gemini/settings.json` hooks to use Gemini's event lifecycle names instead of Claude's (`PostToolUse` -> Gemini native equivalent).

---

## Phase 2: Cross-Machine Rollout
Deploy the local parity baseline to M2 (homelab) and WinPC.

### Milestone 2.1: M2 Homelab Deployment
- [ ] **Connect via SSH `m2`**
  - **Status:** To Do
- [ ] **Deploy dotfiles & Bootstrap**
  - **Status:** To Do
  - **Goal:** Clone/pull `gemini-dotfiles` and run `bootstrap.sh`.
- [ ] **Sync Plugins & Hooks**
  - **Status:** To Do
  - **Goal:** Run `sync-claude-plugins.py` and ensure hook names are correctly mapped.
- [ ] **Verify Nexus Communication**
  - **Status:** To Do
  - **Goal:** Confirm `gemini mcp` shows `nexus` as connected and test cross-machine messaging.

### Milestone 2.2: WinPC Deployment
- [ ] **Connect via SSH `winpc`**
  - **Status:** To Do
- [ ] **Deploy dotfiles & Bootstrap**
  - **Status:** To Do
  - **Goal:** Clone/pull `gemini-dotfiles` and run `bootstrap.ps1`.
- [ ] **Sync Plugins & Hooks**
  - **Status:** To Do
- [ ] **Verify Nexus Communication**
  - **Status:** To Do

