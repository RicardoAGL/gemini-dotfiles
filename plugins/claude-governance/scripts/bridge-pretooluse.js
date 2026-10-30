#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync: nodeSpawnSync } = require("node:child_process");

const CLAUDE_HOOKS_DIR = path.join(os.homedir(), ".claude", "hooks");
const DEFAULT_HOOKS = Object.freeze({
  blockSecrets: path.join(CLAUDE_HOOKS_DIR, "pretooluse-block-secrets.js"),
  askGate: path.join(CLAUDE_HOOKS_DIR, "ask-gate.sh"),
  tokenGuard: path.join(CLAUDE_HOOKS_DIR, "pretooluse-token-guard.js"),
});
const CLAUDE_DECISIONS = new Set(["allow", "deny", "ask"]);

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function deny(reason) {
  return { decision: "deny", reason: String(reason || "Governance check failed") };
}

function isAbsoluteForPlatform(value, platform = process.platform) {
  const paths = platform === "win32" ? path.win32 : path.posix;
  return typeof value === "string" && value.length > 0 && !value.includes("\0") && paths.isAbsolute(value);
}

function pathIsWithin(candidate, parent, platform = process.platform) {
  const paths = platform === "win32" ? path.win32 : path.posix;
  const relative = paths.relative(paths.normalize(parent), paths.normalize(candidate));
  return relative === "" || (!relative.startsWith(`..${paths.sep}`) && relative !== ".." && !paths.isAbsolute(relative));
}

function requireAbsolutePath(value, fieldName, platform) {
  if (!isAbsoluteForPlatform(value, platform)) {
    throw new Error(`${fieldName} must be an absolute path`);
  }
  return value;
}

function mapToolCall(event, platform = process.platform) {
  if (!isPlainObject(event) || !isPlainObject(event.toolCall) || !isPlainObject(event.toolCall.args)) {
    throw new Error("Invalid Antigravity hook event schema");
  }

  const workspaces = event.workspacePaths;
  if (!Array.isArray(workspaces) || workspaces.length === 0 || workspaces.some((entry) => !isAbsoluteForPlatform(entry, platform))) {
    throw new Error("workspacePaths must contain absolute paths");
  }

  const { name, args } = event.toolCall;
  let toolName;
  let toolInput;
  let cwd = workspaces[0];

  switch (name) {
    case "view_file":
      toolName = "Read";
      toolInput = { file_path: requireAbsolutePath(args.AbsolutePath, "AbsolutePath", platform) };
      break;
    case "write_to_file":
      toolName = "Write";
      toolInput = {
        file_path: requireAbsolutePath(args.TargetFile, "TargetFile", platform),
        content: typeof args.CodeContent === "string" ? args.CodeContent : "",
      };
      break;
    case "replace_file_content":
      toolName = "Edit";
      toolInput = {
        file_path: requireAbsolutePath(args.TargetFile, "TargetFile", platform),
        old_string: typeof args.TargetContent === "string" ? args.TargetContent : "",
        new_string: typeof args.ReplacementContent === "string" ? args.ReplacementContent : "",
      };
      break;
    case "multi_replace_file_content":
      toolName = "Edit";
      toolInput = {
        file_path: requireAbsolutePath(args.TargetFile, "TargetFile", platform),
        edits: Array.isArray(args.ReplacementChunks) ? args.ReplacementChunks : [],
      };
      break;
    case "run_command":
      if (typeof args.CommandLine !== "string" || args.CommandLine.trim() === "" || args.CommandLine.includes("\0")) {
        throw new Error("CommandLine must be a non-empty string");
      }
      cwd = requireAbsolutePath(args.Cwd, "Cwd", platform);
      if (!workspaces.some((workspace) => pathIsWithin(cwd, workspace, platform))) {
        throw new Error("Cwd must stay within a declared workspace");
      }
      toolName = "Bash";
      toolInput = { command: args.CommandLine };
      break;
    default:
      throw new Error(`Unsupported governed tool: ${String(name)}`);
  }

  return { tool_name: toolName, tool_input: toolInput, cwd };
}

function validateGuardOutput(stdout) {
  if (typeof stdout !== "string" || stdout.trim() === "") {
    throw new Error("guard returned no JSON decision");
  }

  let parsed;
  try {
    parsed = JSON.parse(stdout.trim());
  } catch {
    throw new Error("guard returned malformed JSON");
  }
  if (!isPlainObject(parsed)) {
    throw new Error("guard output must be a JSON object");
  }
  if (!("hookSpecificOutput" in parsed)) {
    if (Object.keys(parsed).length !== 0) {
      throw new Error("guard output has an unsupported schema");
    }
    return { decision: "allow", reason: "" };
  }

  const output = parsed.hookSpecificOutput;
  if (!isPlainObject(output) || !CLAUDE_DECISIONS.has(output.permissionDecision)) {
    throw new Error("guard output has an invalid permission decision");
  }
  return {
    decision: output.permissionDecision,
    reason: typeof output.permissionDecisionReason === "string" ? output.permissionDecisionReason : "",
  };
}

function runRequiredHook(command, args, payload, options) {
  const result = options.spawnSync(command, args, {
    input: JSON.stringify(payload),
    encoding: "utf8",
    timeout: options.timeout,
    env: options.env,
  });
  if (!result || result.error) {
    throw new Error(result && result.error ? result.error.message : "guard process did not return a result");
  }
  if (result.signal) {
    throw new Error(`guard terminated by signal ${result.signal}`);
  }
  if (!Number.isInteger(result.status) || result.status !== 0) {
    throw new Error(`guard exited with status ${String(result.status)}`);
  }
  return validateGuardOutput(result.stdout);
}

function discoverBash(options = {}) {
  const platform = options.platform || process.platform;
  const env = options.env || process.env;
  const existsSync = options.existsSync || fs.existsSync;
  const spawn = options.spawnSync || nodeSpawnSync;
  const paths = platform === "win32" ? path.win32 : path.posix;
  const explicit = env.GEMINI_GOVERNANCE_BASH;

  if (explicit !== undefined) {
    if (!isAbsoluteForPlatform(explicit, platform) || !existsSync(explicit)) {
      throw new Error("GEMINI_GOVERNANCE_BASH must name an existing absolute path");
    }
    return explicit;
  }

  const candidates = platform === "win32"
    ? [
        env.ProgramFiles && paths.join(env.ProgramFiles, "Git", "bin", "bash.exe"),
        env.ProgramFiles && paths.join(env.ProgramFiles, "Git", "usr", "bin", "bash.exe"),
        env.LOCALAPPDATA && paths.join(env.LOCALAPPDATA, "Programs", "Git", "bin", "bash.exe"),
      ].filter(Boolean)
    : ["/bin/bash", "/usr/bin/bash"];
  const found = candidates.find((candidate) => existsSync(candidate));
  if (found) return found;

  const lookup = platform === "win32"
    ? spawn("where.exe", ["bash.exe"], { encoding: "utf8", timeout: 5000 })
    : spawn("which", ["bash"], { encoding: "utf8", timeout: 5000 });
  if (lookup && !lookup.error && lookup.status === 0 && !lookup.signal && lookup.stdout) {
    const candidate = lookup.stdout.split(/\r?\n/, 1)[0].trim();
    if (isAbsoluteForPlatform(candidate, platform) && existsSync(candidate)) return candidate;
  }
  throw new Error("A trusted Bash executable is required for the command governance guard");
}

function evaluatePreToolUse(event, dependencies = {}) {
  const spawn = dependencies.spawnSync || nodeSpawnSync;
  const hooks = { ...DEFAULT_HOOKS, ...(dependencies.hooks || {}) };
  let payload;
  try {
    payload = mapToolCall(event, dependencies.platform || process.platform);
  } catch (error) {
    return deny(`Governance input rejected: ${error.message}`);
  }

  const inheritedEnv = { ...process.env, ...(dependencies.env || {}) };
  delete inheritedEnv.BASH_ENV;

  try {
    const decisions = [
      runRequiredHook(process.execPath, [hooks.blockSecrets], payload, {
        spawnSync: spawn,
        timeout: 30000,
        env: inheritedEnv,
      }),
    ];

    if (payload.tool_name === "Bash") {
      const bash = dependencies.bash || discoverBash({
        platform: dependencies.platform,
        env: inheritedEnv,
        existsSync: dependencies.existsSync,
        spawnSync: spawn,
      });
      decisions.push(
        runRequiredHook(bash, [hooks.askGate], payload, {
          spawnSync: spawn,
          timeout: 180000,
          env: inheritedEnv,
        }),
        runRequiredHook(process.execPath, [hooks.tokenGuard], payload, {
          spawnSync: spawn,
          timeout: 30000,
          env: inheritedEnv,
        })
      );
    }

    const blocked = decisions.find((entry) => entry.decision === "deny");
    if (blocked) return deny(blocked.reason || "Blocked by a required governance guard");
    const asked = decisions.find((entry) => entry.decision === "ask");
    if (asked) return { decision: "force_ask", reason: asked.reason || "Governance approval required" };
    return { decision: "allow" };
  } catch (error) {
    return deny(`Required governance guard failed: ${error.message}`);
  }
}

async function main() {
  let rawInput = "";
  for await (const chunk of process.stdin) rawInput += chunk;

  let event;
  try {
    event = JSON.parse(rawInput);
  } catch {
    process.stdout.write(`${JSON.stringify(deny("Governance bridge received malformed JSON"))}\n`);
    return;
  }
  process.stdout.write(`${JSON.stringify(evaluatePreToolUse(event))}\n`);
}

if (require.main === module) {
  main().catch((error) => {
    process.stdout.write(`${JSON.stringify(deny(`Governance bridge error: ${error.message}`))}\n`);
  });
}

module.exports = {
  discoverBash,
  evaluatePreToolUse,
  mapToolCall,
  pathIsWithin,
  validateGuardOutput,
};
