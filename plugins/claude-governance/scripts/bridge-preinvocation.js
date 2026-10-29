#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");
const path = require("path");
const os = require("os");

const CLAUDE_HOOKS_DIR = path.join(os.homedir(), ".claude", "hooks");
const DBT_WOW_SCRIPT = path.join(CLAUDE_HOOKS_DIR, "dbt-wow-user-prompt-submit.js");

async function main() {
  let rawInput = "";
  for await (const chunk of process.stdin) {
    rawInput += chunk;
  }

  let agyEvent = {};
  try {
    agyEvent = JSON.parse(rawInput);
  } catch {
    // proceed
  }

  const now = new Date();
  // Formats to match Claude's: Current datetime: %Y-%m-%d %H:%M %Z (%A, ISO %G-W%V)
  const dateResult = spawnSync("date", ["+Current datetime: %Y-%m-%d %H:%M %Z (%A, ISO %G-W%V)"], {
    encoding: "utf8",
  });
  const dateStr = dateResult.stdout ? dateResult.stdout.trim() : `Current datetime: ${now.toISOString()}`;

  const messages = [dateStr];

  // Optional dbt-wow project nudge if in dbt directory
  try {
    const cwd = (agyEvent.workspacePaths && agyEvent.workspacePaths[0]) || process.cwd();
    const dbtRes = spawnSync("node", [DBT_WOW_SCRIPT], {
      cwd,
      encoding: "utf8",
      timeout: 5000,
    });
    if (dbtRes.stdout && dbtRes.stdout.trim()) {
      messages.push(dbtRes.stdout.trim());
    }
  } catch {}

  const response = {
    injectSteps: [
      {
        ephemeralMessage: messages.join("\n\n"),
      },
    ],
  };

  process.stdout.write(JSON.stringify(response) + "\n");
}

main().catch(() => {
  process.stdout.write("{}\n");
});
