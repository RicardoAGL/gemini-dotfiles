#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");
const path = require("path");
const os = require("os");

const CLAUDE_HOOKS_DIR = path.join(os.homedir(), ".claude", "hooks");
const DOTFILES_SYNC_STOP = path.join(CLAUDE_HOOKS_DIR, "dotfiles-sync-stop.js");
const MEMEX_SYNC_STOP = path.join(CLAUDE_HOOKS_DIR, "memex-sync-stop.js");

async function main() {
  let rawInput = "";
  for await (const chunk of process.stdin) {
    rawInput += chunk;
  }

  let agyEvent = {};
  try {
    agyEvent = JSON.parse(rawInput);
  } catch {}

  // Run stop syncs
  try {
    spawnSync("node", [DOTFILES_SYNC_STOP], {
      input: JSON.stringify(agyEvent),
      encoding: "utf8",
      timeout: 15000,
    });
  } catch {}

  try {
    spawnSync("node", [MEMEX_SYNC_STOP], {
      input: JSON.stringify(agyEvent),
      encoding: "utf8",
      timeout: 15000,
    });
  } catch {}

  // Allow stop
  process.stdout.write("{}\n");
}

main().catch(() => {
  process.stdout.write("{}\n");
});
