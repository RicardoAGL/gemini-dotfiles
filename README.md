# Gemini Dotfiles

Gemini CLI mirror for the Claude-first setup used in the shared workspace.

Claude remains the canonical source of truth. This repo translates the portable parts of the Claude setup into Gemini CLI's native primitives:

- global `~/.gemini/GEMINI.md`
- global `~/.gemini/settings.json`
- repo-local `GEMINI.md` wrappers when a project is still Claude-first
- shell shortcut `gem` -> `gemini --approval-mode=yolo`

## What This Project Manages

- Gemini CLI installation through npm
- Global Gemini memory file at `~/.gemini/GEMINI.md`
- Global Gemini settings at `~/.gemini/settings.json`
- Repo onboarding helper for Claude-first projects
- Shell alias/profile wiring for `gem`
- Shared `infisical-safe-wrapper` command installation when the sibling repo is present

## Canonical Sources

- sibling `claude-dotfiles` repo
- sibling `xebia-ai-security-baseline` repo
- sibling `claude-token-guard` repo

## Usage

Install Gemini CLI:

```bash
cd /path/to/gemini-dotfiles
bash scripts/install-gemini-cli.sh
```

```powershell
powershell -ExecutionPolicy Bypass -File C:\github\gemini-dotfiles\scripts\install-gemini-cli.ps1
```

Bootstrap this machine:

```bash
cd /path/to/gemini-dotfiles
bash scripts/bootstrap.sh
```

```powershell
powershell -ExecutionPolicy Bypass -File C:\github\gemini-dotfiles\scripts\bootstrap.ps1
```

If the sibling repo `~/Desktop/github/infisical-safe-wrapper` exists, bootstrap also installs the shared `infisical-safe-wrapper` command on PATH so Gemini CLI can call the same secret-safe runtime as the other agents.

Prepare a repo the first time Gemini CLI should work there:

```bash
cd /path/to/gemini-dotfiles
bash scripts/use-project.sh /path/to/some-repo
```

```powershell
powershell -ExecutionPolicy Bypass -File C:\github\gemini-dotfiles\scripts\use-project.ps1 -ProjectRoot C:\github\some-repo
```

Run the local validation baseline:

```bash
cd /path/to/gemini-dotfiles
bash scripts/ci/local-checks.sh
```

```powershell
powershell -ExecutionPolicy Bypass -File C:\github\gemini-dotfiles\scripts\ci\local-checks.ps1
```

## Mirroring Strategy

- Global Claude rules -> global Gemini `GEMINI.md`: generated translation
- Claude-first repos -> repo-local `GEMINI.md`: generated wrapper that imports `AGENTS.md` and/or `CLAUDE.md`
- Existing Gemini auth material under `~/.gemini` is preserved
- Existing user settings are preserved and merged with the managed defaults

## Managed Defaults

- `context.fileName` remains `GEMINI.md`
- `general.defaultApprovalMode` stays `default`
- `privacy.usageStatisticsEnabled` is set to `false`
- `security.environmentVariableRedaction.enabled` is set to `true`
- `gem` shell shortcut runs `gemini --approval-mode=yolo`

## Safety Rules

- No Claude files are deleted.
- Existing Gemini auth files are not touched.
- Existing Gemini settings are merged, not replaced wholesale.
- YOLO mode is only enabled through the shell shortcut, not forced into persistent settings.

## Files

- `source-map.json`
- `scripts/install-gemini-cli.sh`
- `scripts/install-gemini-cli.ps1`
- `scripts/bootstrap.sh`
- `scripts/bootstrap.ps1`
- both bootstrap scripts also install the shared `infisical-safe-wrapper` wrapper when available
- `scripts/use-project.sh`
- `scripts/use-project.ps1`
