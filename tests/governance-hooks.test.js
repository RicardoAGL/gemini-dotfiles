"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const root = path.resolve(__dirname, "..");
const bridgePath = path.join(
  root,
  "plugins",
  "claude-governance",
  "scripts",
  "bridge-pretooluse.js"
);
const stopPath = path.join(
  root,
  "plugins",
  "claude-governance",
  "scripts",
  "bridge-stop.js"
);
const { evaluatePreToolUse, discoverBash } = require(bridgePath);

const workspace = path.resolve(root, "fixture-workspace");

function event(name, args) {
  return {
    conversationId: "00000000-0000-0000-0000-000000000001",
    workspacePaths: [workspace],
    toolCall: { name, args },
    stepIdx: 1,
  };
}

function allowingSpawn(calls) {
  return (command, args, options) => {
    calls.push({ command, args, options });
    return { status: 0, signal: null, stdout: "{}\n", stderr: "" };
  };
}

test("hooks matcher uses the current official tool names", () => {
  const hooks = JSON.parse(
    fs.readFileSync(
      path.join(root, "plugins", "claude-governance", "hooks.json"),
      "utf8"
    )
  );
  assert.equal(
    hooks["claude-governance"].PreToolUse[0].matcher,
    "run_command|view_file|write_to_file|replace_file_content|multi_replace_file_content"
  );
});

test("official file tools map AbsolutePath and TargetFile into Claude guard payloads", () => {
  const cases = [
    ["view_file", { AbsolutePath: path.join(workspace, "a.txt") }, "Read"],
    ["write_to_file", { TargetFile: path.join(workspace, "b.txt"), CodeContent: "x" }, "Write"],
    [
      "replace_file_content",
      { TargetFile: path.join(workspace, "c.txt"), TargetContent: "a", ReplacementContent: "b" },
      "Edit",
    ],
    [
      "multi_replace_file_content",
      { TargetFile: path.join(workspace, "d.txt"), ReplacementChunks: [] },
      "Edit",
    ],
  ];
  for (const [tool, args, expectedClaudeTool] of cases) {
    const calls = [];
    const result = evaluatePreToolUse(event(tool, args), {
      spawnSync: allowingSpawn(calls),
      bash: "/bin/bash",
    });
    assert.equal(result.decision, "allow", tool);
    assert.equal(calls.length, 1, tool);
    const payload = JSON.parse(calls[0].options.input);
    assert.equal(payload.tool_name, expectedClaudeTool, tool);
    assert.equal(payload.tool_input.file_path, args.AbsolutePath || args.TargetFile, tool);
  }
});

test("run_command uses validated args.Cwd and invokes all required guards", () => {
  const calls = [];
  const cwd = path.join(workspace, "subdir");
  const result = evaluatePreToolUse(
    event("run_command", { CommandLine: "npm test", Cwd: cwd }),
    { spawnSync: allowingSpawn(calls), bash: "/portable/bash" }
  );
  assert.equal(result.decision, "allow");
  assert.equal(calls.length, 3);
  const payload = JSON.parse(calls[0].options.input);
  assert.equal(payload.tool_name, "Bash");
  assert.equal(payload.tool_input.command, "npm test");
  assert.equal(payload.cwd, cwd);
  assert.equal(calls[1].command, "/portable/bash");
});

test("run_command rejects a missing, relative, or out-of-workspace Cwd", () => {
  for (const cwd of [undefined, "relative", path.resolve(workspace, "..", "escape")]) {
    const result = evaluatePreToolUse(
      event("run_command", { CommandLine: "npm test", Cwd: cwd }),
      { spawnSync: allowingSpawn([]), bash: "/bin/bash" }
    );
    assert.equal(result.decision, "deny");
  }
});

test("required guards fail closed for process and schema failures", () => {
  const failures = [
    { error: Object.assign(new Error("missing"), { code: "ENOENT" }), status: null, stdout: "", stderr: "" },
    { error: Object.assign(new Error("timeout"), { code: "ETIMEDOUT" }), status: null, stdout: "", stderr: "" },
    { status: null, signal: "SIGTERM", stdout: "", stderr: "" },
    { status: 7, signal: null, stdout: "{}", stderr: "failed" },
    { status: 0, signal: null, stdout: "not json", stderr: "" },
    { status: 0, signal: null, stdout: "[]", stderr: "" },
    {
      status: 0,
      signal: null,
      stdout: JSON.stringify({ hookSpecificOutput: { permissionDecision: "unknown" } }),
      stderr: "",
    },
  ];
  for (const failure of failures) {
    const result = evaluatePreToolUse(
      event("view_file", { AbsolutePath: path.join(workspace, "a.txt") }),
      { spawnSync: () => failure, bash: "/bin/bash" }
    );
    assert.equal(result.decision, "deny");
  }
});

test("each required run_command guard fails closed independently", () => {
  const blocked = {
    status: 0,
    signal: null,
    stdout: JSON.stringify({ hookSpecificOutput: { permissionDecision: "deny", permissionDecisionReason: "blocked" } }),
    stderr: "",
  };
  for (const failingGuard of [0, 1, 2]) {
    let call = 0;
    const result = evaluatePreToolUse(
      event("run_command", { CommandLine: "npm test", Cwd: workspace }),
      {
        bash: "/portable/bash",
        spawnSync: () => (call++ === failingGuard ? blocked : {
          status: 0,
          signal: null,
          stdout: "{}",
          stderr: "",
        }),
      }
    );
    assert.equal(result.decision, "deny", `guard ${failingGuard}`);
  }
});

test("run_command fails closed when no trusted Bash can be discovered", () => {
  const result = evaluatePreToolUse(
    event("run_command", { CommandLine: "npm test", Cwd: workspace }),
    {
      platform: "linux",
      existsSync: () => false,
      spawnSync: (command) => command === process.execPath
        ? { status: 0, signal: null, stdout: "{}", stderr: "" }
        : { status: 1, signal: null, stdout: "", stderr: "" },
    }
  );
  assert.equal(result.decision, "deny");
});

test("Windows Bash discovery accepts an explicit absolute executable", () => {
  const candidate = "C:\\Program Files\\Git\\bin\\bash.exe";
  const discovered = discoverBash({
    platform: "win32",
    env: { GEMINI_GOVERNANCE_BASH: candidate },
    existsSync: (value) => value === candidate,
    spawnSync: () => ({ status: 1, stdout: "" }),
  });
  assert.equal(discovered, candidate);
});

test("Stop always emits a required non-continue decision", () => {
  const result = spawnSync(process.execPath, [stopPath], {
    input: JSON.stringify({ executionNum: 1, fullyIdle: true }),
    encoding: "utf8",
    env: { ...process.env, HOME: path.join(root, "tests", "missing-home") },
  });
  assert.equal(result.status, 0);
  const output = JSON.parse(result.stdout);
  assert.equal(typeof output.decision, "string");
  assert.notEqual(output.decision, "continue");
});
