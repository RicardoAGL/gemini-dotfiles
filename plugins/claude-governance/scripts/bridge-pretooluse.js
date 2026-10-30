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

function requireDirectory(value, fieldName, platform, fileSystem) {
  const lexical = requireAbsolutePath(value, fieldName, platform);
  let info;
  let physical;
  try {
    info = fileSystem.statSync(lexical);
    physical = fileSystem.realpathSync(lexical);
  } catch (error) {
    throw new Error(`${fieldName} must be an existing directory: ${error.message}`);
  }
  if (!info || typeof info.isDirectory !== "function" || !info.isDirectory()) {
    throw new Error(`${fieldName} must be an existing directory`);
  }
  if (!isAbsoluteForPlatform(physical, platform)) {
    throw new Error(`${fieldName} did not resolve to an absolute directory`);
  }
  const paths = platform === "win32" ? path.win32 : path.posix;
  return { lexical: paths.normalize(lexical), physical: paths.normalize(physical) };
}

function validateWorkspaces(event, platform, fileSystem) {
  if (!Array.isArray(event.workspacePaths) || event.workspacePaths.length === 0) {
    throw new Error("workspacePaths must contain existing absolute directories");
  }
  return event.workspacePaths.map((workspace, index) =>
    requireDirectory(workspace, `workspacePaths[${index}]`, platform, fileSystem)
  );
}

function requireContainedDirectory(value, fieldName, workspaces, platform, fileSystem) {
  const directory = requireDirectory(value, fieldName, platform, fileSystem);
  const contained = workspaces.some(
    (workspace) =>
      pathIsWithin(directory.lexical, workspace.lexical, platform) &&
      pathIsWithin(directory.physical, workspace.physical, platform)
  );
  if (!contained) {
    throw new Error(`${fieldName} must stay physically within a declared workspace`);
  }
  return directory;
}

function classifyToolCall(event, options = {}) {
  const platform = options.platform || process.platform;
  const fileSystem = options.fileSystem || fs;
  if (!isPlainObject(event) || !isPlainObject(event.toolCall) || !isPlainObject(event.toolCall.args)) {
    throw new Error("Invalid Antigravity hook event schema");
  }

  const workspaces = validateWorkspaces(event, platform, fileSystem);

  const { name, args } = event.toolCall;
  let toolName;
  let toolInput;
  let cwd = workspaces[0].physical;

  switch (name) {
    case "view_file":
      toolName = "Read";
      toolInput = { file_path: requireAbsolutePath(args.AbsolutePath, "AbsolutePath", platform) };
      break;
    case "list_dir":
      requireContainedDirectory(
        args.DirectoryPath,
        "DirectoryPath",
        workspaces,
        platform,
        fileSystem
      );
      return { kind: "force_ask", reason: "Directory enumeration requires approval" };
    case "find_by_name":
      if (typeof args.Pattern !== "string" || args.Pattern.length === 0 || args.Pattern.includes("\0")) {
        throw new Error("Pattern must be a non-empty string");
      }
      toolName = "Glob";
      toolInput = {
        path: requireAbsolutePath(args.SearchDirectory, "SearchDirectory", platform),
        pattern: args.Pattern,
      };
      break;
    case "grep_search":
      if (typeof args.Query !== "string" || args.Query.length === 0 || args.Query.includes("\0")) {
        throw new Error("Query must be a non-empty string");
      }
      toolName = "Grep";
      toolInput = {
        path: requireAbsolutePath(args.SearchPath, "SearchPath", platform),
        pattern: args.Query,
      };
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
      {
        const commandDirectory = requireContainedDirectory(
          args.Cwd,
          "Cwd",
          workspaces,
          platform,
          fileSystem
        );
        cwd = commandDirectory.physical;
      }
      toolName = "Bash";
      toolInput = { command: args.CommandLine };
      break;
    case "manage_task":
      if (args.Action === "list" || args.Action === "status") return { kind: "allow" };
      if (args.Action === "kill" || args.Action === "send_input") {
        return { kind: "force_ask", reason: `Background task ${args.Action} requires approval` };
      }
      throw new Error("Unsupported manage_task action");
    case "manage_subagents":
      if (args.Action === "list") return { kind: "allow" };
      if (args.Action === "kill" || args.Action === "kill_all") {
        return { kind: "force_ask", reason: `Subagent ${args.Action} requires approval` };
      }
      throw new Error("Unsupported manage_subagents action");
    case "list_permissions":
      return { kind: "allow" };
    case "ask_question":
      if (!Array.isArray(args.questions)) throw new Error("ask_question questions must be an array");
      return { kind: "allow" };
    case "search_web":
    case "read_url_content":
      return { kind: "force_ask", reason: `${name} network access requires approval` };
    case "schedule":
      return { kind: "force_ask", reason: "Autonomous scheduling requires approval" };
    case "ask_permission":
      return { kind: "force_ask", reason: "Permission expansion requires approval" };
    case "invoke_subagent":
    case "define_subagent":
    case "send_message":
      return { kind: "force_ask", reason: `${name} agent action requires approval` };
    case "generate_image":
      return { kind: "force_ask", reason: "Media generation requires approval" };
    default:
      throw new Error(`Unknown or unsupported governed tool: ${String(name)}`);
  }

  return { kind: "guard", payload: { tool_name: toolName, tool_input: toolInput, cwd } };
}

function mapToolCall(event, platform = process.platform, fileSystem = fs) {
  const classification = classifyToolCall(event, { platform, fileSystem });
  if (classification.kind !== "guard") {
    throw new Error("Tool does not map to an executable Claude guard payload");
  }
  return classification.payload;
}

function validateGuardOutput(stdout) {
  if (typeof stdout !== "string") {
    throw new Error("guard output must be text");
  }
  if (stdout.trim() === "") {
    return { decision: "allow", reason: "" };
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
  let classification;
  try {
    classification = classifyToolCall(event, {
      platform: dependencies.platform || process.platform,
      fileSystem: dependencies.fileSystem || fs,
    });
  } catch (error) {
    return deny(`Governance input rejected: ${error.message}`);
  }
  if (classification.kind === "allow") return { decision: "allow" };
  if (classification.kind === "force_ask") {
    return { decision: "force_ask", reason: classification.reason };
  }
  const payload = classification.payload;

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
  classifyToolCall,
  discoverBash,
  evaluatePreToolUse,
  mapToolCall,
  pathIsWithin,
  validateGuardOutput,
};
