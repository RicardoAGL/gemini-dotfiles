#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");
const path = require("path");
const os = require("os");

const CLAUDE_HOOKS_DIR = path.join(os.homedir(), ".claude", "hooks");
const DOTFILES_SYNC_START = path.join(CLAUDE_HOOKS_DIR, "dotfiles-sync-start.js");
const KANBAN_PROBE = path.join(CLAUDE_HOOKS_DIR, "kanban-board-probe.js");
const MEMEX_SYNC_START = path.join(CLAUDE_HOOKS_DIR, "memex-sync-session-start.js");

function runSubHook(scriptPath, inputPayload, timeoutMs = 25000) {
  try {
    const res = spawnSync("node", [scriptPath], {
      input: JSON.stringify(inputPayload),
      encoding: "utf8",
      timeout: timeoutMs,
    });
    if (!res.stdout) return null;
    const parsed = JSON.parse(res.stdout.trim());
    return (
      parsed.additionalContext ||
      (parsed.hookSpecificOutput && parsed.hookSpecificOutput.additionalContext) ||
      null
    );
  } catch {
    return null;
  }
}

async function main() {
  let rawInput = "";
  for await (const chunk of process.stdin) {
    rawInput += chunk;
  }

  let agyEvent = {};
  try {
    agyEvent = JSON.parse(rawInput);
  } catch {
    // proceed with empty
  }

  const contexts = [];

  const dotfilesContext = runSubHook(DOTFILES_SYNC_START, agyEvent, 20000);
  if (dotfilesContext) contexts.push(dotfilesContext);

  const memexContext = runSubHook(MEMEX_SYNC_START, agyEvent, 15000);
  if (memexContext) contexts.push(memexContext);

  const kanbanContext = runSubHook(KANBAN_PROBE, agyEvent, 25000);
  if (kanbanContext) contexts.push(kanbanContext);

  if (contexts.length === 0) {
    process.stdout.write("{}\n");
    return;
  }

  const fullMessage = contexts.join("\n\n");
  const response = {
    injectSteps: [
      {
        systemMessage: {
          systemMessage: fullMessage,
        },
      },
    ],
  };

  process.stdout.write(JSON.stringify(response) + "\n");
}

main().catch(() => {
  process.stdout.write("{}\n");
});
