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
- Validated Claude skill snapshots at `~/.gemini/skills/<skill-name>/`
- Repo onboarding helper for Claude-first projects
- Shell alias/profile wiring for `gem`
- Strictly validated and attested `claude-governance` plugin installation
- Governed `agyx` wrapper exposure only after the plugin is enabled and current
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

If the sibling repo `~/Desktop/github/infisical-safe-wrapper` exists, bootstrap installs the shared `infisical-safe-wrapper` command into the user bin directory and ensures that directory is on PATH. Installation fails closed unless the sibling checkout is the expected repository, is clean on published `main`, and exposes tracked non-redirecting wrapper entrypoints. The installed launcher pins that reviewed commit and repeats the provenance checks before every invocation; later checkout drift is refused rather than executed.

`source-map.local.json` may relocate the sibling checkout only by changing `integrations.infisicalWrapper.repoPath` to another single repository-name segment under the shared workspace. Absolute paths, traversal, separators, control characters, quotes, symlinks, and Windows reparse redirects are rejected. The expected Git remote remains canonical in the tracked `source-map.json` and cannot be weakened by the local override.

Bootstrap requires Python 3, Node.js, and a working `agy` command. On Windows, command governance also requires Git Bash. Set `GEMINI_GOVERNANCE_BASH` to an existing absolute Bash executable when Git Bash is installed outside its standard locations.

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
- Reviewed Claude skills -> complete, validated directories under `~/.gemini/skills`
- Enabled Claude plugins -> real `skills/` packages, including their `scripts/`, `references/`, and `assets/`; command or agent documents are used only when they are valid standalone skills
- The sibling `claude-dotfiles/skills` checkout is preferred only when its repository is clean, on `main`, and exactly matches `origin/main`; deployed `~/.claude/skills` is a separately identified, content-attested fallback when that checkout is unavailable
- `.claude-sync-manifest.json` schema v2 records sync ownership, the verified source revision or fallback attestation, plugin version, and content hash
- One owner-recorded exclusive lock covers discovery, staging, activation, and manifest commit; a second process cannot recover or mutate an active transaction. Windows ownership checks use read-only process handles and fail closed without calling `os.kill`.
- Every changed package is staged before the canonical source is revalidated; one journaled transaction then activates the set, and a failed activation or manifest write rolls everything back
- Recovery verifies the current snapshot and manifest against journaled pre/post hashes before overwriting anything; unexpected state is preserved for manual recovery
- Existing Gemini auth material under `~/.gemini` is preserved
- Existing user settings are preserved and merged with the managed defaults

## Managed Defaults

- `context.fileName` remains `GEMINI.md`
- `general.defaultApprovalMode` stays `default`
- `privacy.usageStatisticsEnabled` is set to `false`
- `security.environmentVariableRedaction.enabled` is set to `true`
- `gem` shell shortcut runs `gemini --approval-mode=yolo`

## Governance Plugin

The plugin follows Antigravity's current [hook contract](https://antigravity.google/docs/hooks) and [plugin lifecycle](https://antigravity.google/docs/plugins). Its `PreToolUse` hook covers `view_file`, `write_to_file`, `replace_file_content`, `multi_replace_file_content`, and `run_command`. File paths are mapped from the documented `AbsolutePath` and `TargetFile` fields; command working directories come only from a validated absolute `args.Cwd` inside a declared workspace.

Secret protection is required for every governed tool. Commands additionally require the destructive-command gate and token guard. Missing guards, process failures, timeouts, signals, non-zero exits, malformed JSON, or invalid decision schemas deny the tool call. Session context and synchronization hooks remain best-effort because they do not authorize tool execution.

`agyx` deliberately requests Antigravity's dangerous permission-bypass mode. Bootstrap therefore removes any previously managed `agyx` entry first, validates the plugin with `agy plugin validate --strict`, installs and enables it, and compares Antigravity's installed copy byte-for-byte with this repository before restoring the wrapper. The wrapper repeats that enabled/current attestation every time it runs; if governance cannot be proven, `agyx` fails without launching Antigravity. Bash and PowerShell bootstraps enforce the same sequence.

This control reduces accidental or agent-driven bypass risk but does not make `--dangerously-skip-permissions` intrinsically safe. A user who can alter the local account, executable search path, canonical Claude hooks, or this repository can still subvert local controls. Keep those dependencies reviewed and access-controlled.

## Safety Rules

- No Claude files are deleted.
- Existing Gemini auth files are not touched.
- Existing Gemini settings are merged, not replaced wholesale.
- Unmanaged entries under `~/.gemini/skills` are never overwritten or removed, including same-name collisions.
- Ownership is never inferred from matching content. Only manifest-owned snapshots and exact legacy repository symlinks are reconciled.
- Stale owned snapshots are moved to timestamped batches under `~/.gemini/skills/.claude-sync-quarantine/` with reason and source-hash metadata; they are not permanently deleted.
- Plugin and marketplace identifiers are validated before cache lookup, and symlinked or out-of-cache packages are rejected.
- Configured target/cache paths retain their lexical identity and reject symlinks, Windows junctions, and other reparse points in every existing path component before resolved containment checks.
- Plugin cache versions use parsed numeric version ordering with a deterministic lexical fallback; filesystem modification time is never treated as version authority.
- Invalid skill frontmatter, modified managed snapshots, missing Python, and other parity failures stop bootstrap with a non-zero exit.
- YOLO mode is only enabled through the shell shortcut, not forced into persistent settings.
- `agyx` is omitted or fails closed unless the governance plugin is enabled and exactly matches the reviewed repository copy.

## Files

- `source-map.json`
- `scripts/install-gemini-cli.sh`
- `scripts/install-gemini-cli.ps1`
- `scripts/bootstrap.sh`
- `scripts/bootstrap.ps1`
- `scripts/sync-claude-plugins.py`
- `scripts/install-governance-plugin.py`
- `plugins/claude-governance/`
- both bootstrap scripts install the same pinned, provenance-checking `infisical-safe-wrapper` runtime when a clean published sibling checkout is available
- `scripts/use-project.sh`
- `scripts/use-project.ps1`
