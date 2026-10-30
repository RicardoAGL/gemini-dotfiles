"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const root = path.resolve(__dirname, "..");
const bridgePath = path.join(root, "plugins", "claude-governance", "scripts", "bridge-pretooluse.js");
const stopPath = path.join(root, "plugins", "claude-governance", "scripts", "bridge-stop.js");
const { evaluatePreToolUse, discoverBash } = require(bridgePath);

function event(name, args, workspacePaths = [root]) {
  return {
    conversationId: "00000000-0000-0000-0000-000000000001",
    workspacePaths,
    toolCall: { name, args },
    stepIdx: 1,
  };
}

function processResult(stdout = "{}\n") {
  return { status: 0, signal: null, stdout, stderr: "" };
}

function allowingSpawn(calls, stdout = "{}\n") {
  return (command, args, options) => {
    calls.push({ command, args, options });
    return processResult(stdout);
  };
}

test("PreToolUse matcher covers every tool so unknown tools cannot bypass governance", () => {
  const hooks = JSON.parse(
    fs.readFileSync(path.join(root, "plugins", "claude-governance", "hooks.json"), "utf8")
  );
  assert.equal(hooks["claude-governance"].PreToolUse[0].matcher, "*");
});

test("read search tools preserve distinct Claude tool types and descendant-sensitive fields", () => {
  const cases = [
    [
      "view_file",
      { AbsolutePath: path.join(root, "README.md") },
      "Read",
      { file_path: path.join(root, "README.md") },
    ],
    [
      "find_by_name",
      { SearchDirectory: root, Pattern: "**/*.md" },
      "Glob",
      { path: root, pattern: "**/*.md" },
    ],
    [
      "grep_search",
      { SearchPath: root, Query: "secret|token" },
      "Grep",
      { path: root, pattern: "secret|token" },
    ],
  ];
  for (const [tool, args, expectedTool, expectedInput] of cases) {
    const calls = [];
    const result = evaluatePreToolUse(event(tool, args), { spawnSync: allowingSpawn(calls) });
    assert.equal(result.decision, "allow", tool);
    assert.equal(calls.length, 1, tool);
    const payload = JSON.parse(calls[0].options.input);
    assert.equal(payload.tool_name, expectedTool, tool);
    assert.deepEqual(payload.tool_input, expectedInput, tool);
  }
});

test("list_dir forces approval only after physical workspace containment", (t) => {
  const allowed = evaluatePreToolUse(event("list_dir", { DirectoryPath: path.join(root, "tests") }), {
    spawnSync: () => assert.fail("list_dir must not run a plain directory guard"),
  });
  assert.equal(allowed.decision, "force_ask");

  for (const directory of [path.resolve(root, ".."), path.join(root, "missing-directory")]) {
    const denied = evaluatePreToolUse(event("list_dir", { DirectoryPath: directory }));
    assert.equal(denied.decision, "deny", directory);
  }

  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "governance-list-"));
  t.after(() => fs.rmSync(temporary, { recursive: true, force: true }));
  const workspace = path.join(temporary, "workspace");
  const outside = path.join(temporary, "outside");
  const redirect = path.join(workspace, "redirect");
  fs.mkdirSync(workspace);
  fs.mkdirSync(outside);
  try {
    fs.symlinkSync(outside, redirect, process.platform === "win32" ? "junction" : "dir");
  } catch (error) {
    t.skip(`directory redirect unavailable: ${error.message}`);
    return;
  }
  const escaped = evaluatePreToolUse(
    event("list_dir", { DirectoryPath: redirect }, [workspace])
  );
  assert.equal(escaped.decision, "deny");
  assert.match(escaped.reason, /physical|workspace/i);
});

test("all documented write tools pass TargetFile through the write secret guard", () => {
  const cases = [
    ["write_to_file", { TargetFile: path.join(root, "new.txt"), CodeContent: "x" }, "Write"],
    [
      "replace_file_content",
      { TargetFile: path.join(root, "README.md"), TargetContent: "a", ReplacementContent: "b" },
      "Edit",
    ],
    [
      "multi_replace_file_content",
      { TargetFile: path.join(root, "README.md"), ReplacementChunks: [] },
      "Edit",
    ],
  ];
  for (const [tool, args, expectedClaudeTool] of cases) {
    const calls = [];
    const result = evaluatePreToolUse(event(tool, args), { spawnSync: allowingSpawn(calls) });
    assert.equal(result.decision, "allow", tool);
    assert.equal(calls.length, 1, tool);
    const payload = JSON.parse(calls[0].options.input);
    assert.equal(payload.tool_name, expectedClaudeTool, tool);
    assert.equal(payload.tool_input.file_path, args.TargetFile, tool);
  }
});

test("run_command uses existing physical Cwd and invokes all required guards", () => {
  const calls = [];
  const cwd = path.join(root, "tests");
  const result = evaluatePreToolUse(
    event("run_command", { CommandLine: "npm test", Cwd: cwd }),
    { spawnSync: allowingSpawn(calls), bash: "/portable/bash" }
  );
  assert.equal(result.decision, "allow");
  assert.equal(calls.length, 3);
  const payload = JSON.parse(calls[0].options.input);
  assert.equal(payload.tool_name, "Bash");
  assert.equal(payload.tool_input.command, "npm test");
  assert.equal(payload.cwd, fs.realpathSync(cwd));
  assert.equal(calls[1].command, "/portable/bash");
});

test("run_command rejects missing, relative, nonexistent, and out-of-workspace directories", () => {
  const outside = path.resolve(root, "..");
  const missing = path.join(root, "tests", "does-not-exist");
  const cases = [
    [undefined, [root]],
    ["relative", [root]],
    [missing, [root]],
    [outside, [root]],
    [path.join(root, "tests"), [missing]],
  ];
  for (const [cwd, workspaces] of cases) {
    const result = evaluatePreToolUse(
      event("run_command", { CommandLine: "npm test", Cwd: cwd }, workspaces),
      { spawnSync: allowingSpawn([]), bash: "/bin/bash" }
    );
    assert.equal(result.decision, "deny", String(cwd));
  }
});

test("run_command rejects a physical symlink or junction escape", (t) => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "governance-cwd-"));
  t.after(() => fs.rmSync(temporary, { recursive: true, force: true }));
  const workspace = path.join(temporary, "workspace");
  const outside = path.join(temporary, "outside");
  const redirect = path.join(workspace, "redirect");
  fs.mkdirSync(workspace);
  fs.mkdirSync(outside);
  try {
    fs.symlinkSync(outside, redirect, process.platform === "win32" ? "junction" : "dir");
  } catch (error) {
    t.skip(`directory redirect unavailable: ${error.message}`);
    return;
  }
  const result = evaluatePreToolUse(
    event("run_command", { CommandLine: "pwd", Cwd: redirect }, [workspace]),
    { spawnSync: allowingSpawn([]), bash: "/bin/bash" }
  );
  assert.equal(result.decision, "deny");
  assert.match(result.reason, /physical|workspace/i);
});

test("mocked Windows junction escape is rejected by physical containment", () => {
  const fileSystem = {
    statSync: () => ({ isDirectory: () => true }),
    realpathSync: (value) => ({
      "C:\\workspace": "C:\\workspace",
      "C:\\workspace\\junction": "C:\\outside",
    })[value],
  };
  const result = evaluatePreToolUse(
    event(
      "run_command",
      { CommandLine: "dir", Cwd: "C:\\workspace\\junction" },
      ["C:\\workspace"]
    ),
    {
      platform: "win32",
      fileSystem,
      spawnSync: allowingSpawn([]),
      bash: "C:\\Git\\bin\\bash.exe",
    }
  );
  assert.equal(result.decision, "deny");
  assert.match(result.reason, /physical|workspace/i);
});

test("only explicitly safe documented operations bypass executable guards", () => {
  const safe = [
    ["manage_task", { Action: "list" }],
    ["manage_task", { Action: "status", TaskId: "1" }],
    ["list_permissions", {}],
    ["manage_subagents", { Action: "list" }],
    ["ask_question", { questions: [] }],
  ];
  for (const [tool, args] of safe) {
    const result = evaluatePreToolUse(event(tool, args), {
      spawnSync: () => assert.fail(`${tool} unexpectedly invoked a process`),
    });
    assert.deepEqual(result, { decision: "allow" }, tool);
  }
});

test("network, media, permission, subagent, and autonomous mutations force approval", () => {
  const sensitive = [
    ["search_web", { query: "security" }],
    ["read_url_content", { Url: "https://example.invalid" }],
    ["manage_task", { Action: "kill", TaskId: "1" }],
    ["manage_task", { Action: "send_input", TaskId: "1", Input: "yes" }],
    ["schedule", { DurationSeconds: 60, Prompt: "continue" }],
    ["ask_permission", { Action: "read", Target: "/tmp", Reason: "test" }],
    ["invoke_subagent", { Subagents: [] }],
    ["define_subagent", { name: "agent", description: "test", system_prompt: "test" }],
    ["send_message", { Recipient: "agent", Message: "test" }],
    ["manage_subagents", { Action: "kill", ConversationIds: ["1"] }],
    ["manage_subagents", { Action: "kill_all" }],
    ["generate_image", { Prompt: "test", ImageName: "test.png" }],
  ];
  for (const [tool, args] of sensitive) {
    const result = evaluatePreToolUse(event(tool, args), {
      spawnSync: () => assert.fail(`${tool} unexpectedly invoked a process`),
    });
    assert.equal(result.decision, "force_ask", tool);
  }
});

test("unknown tools and invalid enumerated actions deny by default", () => {
  for (const [tool, args] of [
    ["future_tool", {}],
    ["manage_task", { Action: "future" }],
    ["manage_subagents", { Action: "future" }],
  ]) {
    const result = evaluatePreToolUse(event(tool, args), { spawnSync: allowingSpawn([]) });
    assert.equal(result.decision, "deny", tool);
  }
});

test("clean exit zero with empty stdout is allow, but nonempty invalid output denies", () => {
  const allowed = evaluatePreToolUse(
    event("view_file", { AbsolutePath: path.join(root, "README.md") }),
    { spawnSync: allowingSpawn([], "") }
  );
  assert.equal(allowed.decision, "allow");

  for (const stdout of ["not json", "[]", '{"unexpected":true}']) {
    const denied = evaluatePreToolUse(
      event("view_file", { AbsolutePath: path.join(root, "README.md") }),
      { spawnSync: () => processResult(stdout) }
    );
    assert.equal(denied.decision, "deny", stdout);
  }
});

test("required guards fail closed for process and decision failures", () => {
  const failures = [
    { error: Object.assign(new Error("missing"), { code: "ENOENT" }), status: null, stdout: "", stderr: "" },
    { error: Object.assign(new Error("timeout"), { code: "ETIMEDOUT" }), status: null, stdout: "", stderr: "" },
    { status: null, signal: "SIGTERM", stdout: "", stderr: "" },
    { status: 7, signal: null, stdout: "", stderr: "failed" },
    {
      status: 0,
      signal: null,
      stdout: JSON.stringify({ hookSpecificOutput: { permissionDecision: "unknown" } }),
      stderr: "",
    },
  ];
  for (const failure of failures) {
    const result = evaluatePreToolUse(
      event("view_file", { AbsolutePath: path.join(root, "README.md") }),
      { spawnSync: () => failure }
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
      event("run_command", { CommandLine: "npm test", Cwd: path.join(root, "tests") }),
      {
        bash: "/portable/bash",
        spawnSync: () => (call++ === failingGuard ? blocked : processResult()),
      }
    );
    assert.equal(result.decision, "deny", `guard ${failingGuard}`);
  }
});

test("run_command fails closed when no trusted Bash can be discovered", () => {
  const result = evaluatePreToolUse(
    event("run_command", { CommandLine: "npm test", Cwd: path.join(root, "tests") }),
    {
      platform: "linux",
      existsSync: () => false,
      spawnSync: (command) => command === process.execPath ? processResult() : { status: 1, signal: null, stdout: "", stderr: "" },
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
