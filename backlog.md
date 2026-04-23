# Backlog

Local fallback tracker for `gemini-dotfiles`.

## In progress

### [dotfiles] Complete Gemini CLI parity rollout
- Status: In progress
- Branch: `main`
- Tags: `gemini`, `dotfiles`, `migration`, `cross-machine`
- What: Mirror the Claude-first baseline into Gemini CLI and deploy it consistently to M5, M2, and WinPc.
- Context: Gemini CLI is now installed on all three machines, the global `GEMINI.md` baseline is generated from Claude, and WinPc uses a concrete `gem.cmd` wrapper instead of relying only on profile loading.
- Done when: all three machines have a working Gemini entrypoint, managed `~/.gemini` state, and the repo includes enough notes for parallel follow-up work.

## To do

### [dotfiles] Add richer operator docs for Gemini project onboarding
- Status: To do
- Branch: `main`
- Tags: `docs`, `gemini`, `onboarding`
- What: Expand the repo notes around `use-project` and project-local `GEMINI.md` wrapper generation.
- Context: The bootstrap/install path is documented, but the repo-local onboarding path can still use examples from real Claude-first repos.
- Done when: a new operator can onboard a Claude-first repo into Gemini without needing chat context.

