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

## Files

- `source-map.json`
- `scripts/install-gemini-cli.sh`
- `scripts/install-gemini-cli.ps1`
- `scripts/bootstrap.sh`
- `scripts/bootstrap.ps1`
- `scripts/sync-claude-plugins.py`
- both bootstrap scripts install the same pinned, provenance-checking `infisical-safe-wrapper` runtime when a clean published sibling checkout is available
- `scripts/use-project.sh`
- `scripts/use-project.ps1`
