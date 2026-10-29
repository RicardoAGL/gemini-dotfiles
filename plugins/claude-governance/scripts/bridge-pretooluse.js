#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");
const path = require("path");
const os = require("os");

const CLAUDE_HOOKS_DIR = path.join(os.homedir(), ".claude", "hooks");
const BLOCK_SECRETS_SCRIPT = path.join(CLAUDE_HOOKS_DIR, "pretooluse-block-secrets.js");
const ASK_GATE_SCRIPT = path.join(CLAUDE_HOOKS_DIR, "ask-gate.sh");
const TOKEN_GUARD_SCRIPT = path.join(CLAUDE_HOOKS_DIR, "pretooluse-token-guard.js");

function deny(reason) {
  process.stdout.write(JSON.stringify({ decision: "deny", reason }) + "\n");
  process.exit(0);
}

function allow() {
  process.stdout.write(JSON.stringify({ decision: "allow" }) + "\n");
  process.exit(0);
}

function runHook(cmd, args, inputPayload, options = {}) {
  const result = spawnSync(cmd, args, {
    input: JSON.stringify(inputPayload),
    encoding: "utf8",
    timeout: options.timeout || 30000,
    env: { ...process.env, ...(options.env || {}) },
  });

  let parsed = null;
  if (result.stdout) {
    try {
      parsed = JSON.parse(result.stdout.trim());
    } catch {
      // stdout may not be json
    }
  }

  return {
    status: result.status,
    stdout: result.stdout || "",
    stderr: result.stderr || "",
    parsed,
  };
}

async function main() {
  let rawInput = "";
  for await (const chunk of process.stdin) {
    rawInput += chunk;
  }

  let agyEvent;
  try {
    agyEvent = JSON.parse(rawInput);
  } catch (err) {
    deny("Governance bridge error: unparseable stdin JSON");
  }

  const toolCall = agyEvent.toolCall || {};
  const toolName = toolCall.name || "";
  const args = toolCall.args || {};
  const cwd = (agyEvent.workspacePaths && agyEvent.workspacePaths[0]) || process.cwd();

  // Map Antigravity tools to Claude Code equivalents
  let claudeToolName = "";
  let claudeToolInput = {};

  if (toolName === "read_file") {
    claudeToolName = "Read";
    claudeToolInput = { file_path: args.Path || "" };
  } else if (toolName === "edit_file") {
    claudeToolName = "Edit";
    claudeToolInput = { file_path: args.Path || "" };
  } else if (toolName === "write_file") {
    claudeToolName = "Write";
    claudeToolInput = { file_path: args.Path || "" };
  } else if (toolName === "run_command") {
    claudeToolName = "Bash";
    claudeToolInput = { command: args.CommandLine || "" };
  } else {
    // Other Antigravity tools not governed by these rules
    allow();
  }

  const claudePayload = {
    tool_name: claudeToolName,
    tool_input: claudeToolInput,
    cwd,
  };

  // 1. Block Secrets Hook (checks Read/Edit/Write/Bash for secret paths / leaks)
  const secretsRes = runHook("node", [BLOCK_SECRETS_SCRIPT], claudePayload);
  if (secretsRes.parsed && secretsRes.parsed.hookSpecificOutput) {
    const hso = secretsRes.parsed.hookSpecificOutput;
    if (hso.permissionDecision === "deny") {
      deny(hso.permissionDecisionReason || "Blocked by security baseline (secrets protection)");
    }
  }

  // 2. For Bash (run_command), run ask-gate.sh and token-guard
  if (claudeToolName === "Bash") {
    // 2a. ask-gate.sh (destructive command gating)
    const askGateRes = runHook(
      "/bin/bash",
      [ASK_GATE_SCRIPT],
      claudePayload,
      {
        timeout: 180000,
        env: { BASH_ENV: undefined },
      }
    );

    if (askGateRes.parsed && askGateRes.parsed.hookSpecificOutput) {
      const hso = askGateRes.parsed.hookSpecificOutput;
      if (hso.permissionDecision === "deny") {
        deny(hso.permissionDecisionReason || "Blocked by ask-gate (destructive command protection)");
      }
    } else if (askGateRes.status !== 0) {
      deny(askGateRes.stderr.trim() || "Blocked by ask-gate (command gating failed or canceled)");
    }

    // 2b. token-guard.sh
    const tokenGuardRes = runHook("node", [TOKEN_GUARD_SCRIPT], claudePayload);
    if (tokenGuardRes.parsed && tokenGuardRes.parsed.hookSpecificOutput) {
      const hso = tokenGuardRes.parsed.hookSpecificOutput;
      if (hso.permissionDecision === "deny") {
        deny(hso.permissionDecisionReason || "Blocked by token guard");
      }
    }
  }

  allow();
}

main().catch((err) => {
  deny("Governance bridge error: " + (err && err.message));
});
