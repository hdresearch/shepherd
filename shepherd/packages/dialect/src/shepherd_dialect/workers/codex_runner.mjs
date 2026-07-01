import { createRequire } from "module";
import crypto from "crypto";
import fs from "node:fs";
import { pathToFileURL } from "url";
import path from "path";
import process from "process";

const SCHEMA_VERSION = "shepherd.provider_worker.v1";

function importSpecifier(specifier, workingDirectory) {
  if (
    specifier.startsWith("/") ||
    specifier.startsWith("./") ||
    specifier.startsWith("../") ||
    specifier.endsWith(".js") ||
    specifier.endsWith(".mjs")
  ) {
    return pathToFileURL(path.resolve(specifier)).href;
  }
  const requireFromWorkingDirectory = createRequire(path.join(workingDirectory, "package.json"));
  return pathToFileURL(requireFromWorkingDirectory.resolve(specifier)).href;
}

const payloadPath = process.argv[2];
if (!payloadPath) {
  throw new Error("missing Codex provider payload path");
}

function sourceCodexHome() {
  if (process.env.CODEX_HOME) {
    return process.env.CODEX_HOME;
  }
  const home = process.env.HOME;
  return home ? path.join(home, ".codex") : undefined;
}

function seedCodexHome(codexHome) {
  const source = sourceCodexHome();
  if (!source || path.resolve(source) === path.resolve(codexHome)) {
    return;
  }
  fs.mkdirSync(codexHome, { recursive: true });
  for (const name of ["auth.json", "config.toml"]) {
    const sourcePath = path.join(source, name);
    if (fs.existsSync(sourcePath)) {
      fs.copyFileSync(sourcePath, path.join(codexHome, name));
    }
  }
}

const payload = JSON.parse(fs.readFileSync(payloadPath, "utf8"));
const sdk = await import(importSpecifier(payload.sdkModule, payload.workingDirectory));
const Codex = sdk.Codex ?? sdk.default?.Codex ?? sdk.default;
if (!Codex) {
  throw new Error(`Codex SDK module ${payload.sdkModule} did not export Codex`);
}

if (payload.codexHome) {
  seedCodexHome(payload.codexHome);
}
const env = { ...process.env };
if (payload.codexHome) {
  env.CODEX_HOME = payload.codexHome;
}

const codex = new Codex({
  codexPathOverride: payload.codexPath || undefined,
  baseUrl: payload.baseUrl || undefined,
  env,
});
const threadOptions = {
  model: payload.model,
  workingDirectory: payload.workingDirectory,
  sandboxMode: payload.sandboxMode,
  approvalPolicy: payload.approvalPolicy,
  modelReasoningEffort: payload.reasoningEffort,
  networkAccessEnabled: payload.networkAccessEnabled,
  webSearchMode: payload.webSearchMode,
  skipGitRepoCheck: true,
};
const thread = payload.threadId
  ? codex.resumeThread(payload.threadId, threadOptions)
  : codex.startThread(threadOptions);
const options = payload.outputSchema ? { outputSchema: payload.outputSchema } : undefined;
const turn = await thread.run(payload.prompt, options);
const items = Array.isArray(turn.items) ? turn.items : [];
for (const [index, item] of items.entries()) {
  emitToolEvents(item, `codex-item-${index + 1}`);
}
const outputText = turn.finalResponse || "";
const usage = turn.usage || {};
if (outputText || Object.keys(usage).length > 0) {
  emit({
    record_type: "provider_event",
    kind: "model.call",
    model: payload.model,
    payload: {
      usage,
      item_count: items.length,
      ...redactedTextPayload(outputText, "output_text"),
    },
  });
}
if (outputText) {
  emit({
    record_type: "provider_event",
    kind: "model.turn",
    model: payload.model,
    payload: redactedTextPayload(outputText, "text"),
  });
}
emit({
  record_type: "provider_result",
  output_text: outputText,
  structured_output: objectOrEmpty(turn.structuredOutput ?? turn.structured_output ?? turn.output),
  session_id: thread.id,
  usage,
  metadata: {
    model: payload.model,
    item_count: items.length,
    sandbox_mode: payload.sandboxMode,
    network_access_enabled: Boolean(payload.networkAccessEnabled),
  },
});

function emitToolEvents(item, fallbackId) {
  const tool = codexToolFromItem(item, fallbackId);
  if (!tool) {
    return;
  }
  emit({
    record_type: "provider_event",
    kind: "tool.call.started",
    model: payload.model,
    tool_call_id: tool.tool_call_id,
    payload: {
      tool_name: tool.tool_name,
      params_digest: digestJsonable(tool.params),
    },
  });
  const completedPayload = {
    tool_name: tool.tool_name,
    success: tool.success,
    ...redactedTextPayload(tool.output, "output"),
  };
  emit({
    record_type: "provider_event",
    kind: "tool.call.completed",
    model: payload.model,
    tool_call_id: tool.tool_call_id,
    payload: completedPayload,
  });
}

function codexToolFromItem(item, fallbackId) {
  const itemType = item?.type;
  const toolCallId = String(item?.id || fallbackId);
  if (itemType === "command_execution") {
    const status = String(item.status || "");
    const exitCode = item.exit_code;
    return {
      tool_call_id: toolCallId,
      tool_name: "Bash",
      params: { command: String(item.command || "") },
      success: status === "completed" && (exitCode === undefined || exitCode === null || exitCode === 0),
      output: String(item.aggregated_output || ""),
    };
  }
  if (itemType === "mcp_tool_call") {
    const server = String(item.server || "mcp");
    const tool = String(item.tool || "tool");
    const error = item.error;
    return {
      tool_call_id: toolCallId,
      tool_name: `mcp__${server}__${tool}`,
      params: { arguments: item.arguments },
      success: item.status === "completed" && !error,
      output: jsonOutput(error ? error : item.result),
    };
  }
  if (itemType === "web_search") {
    return {
      tool_call_id: toolCallId,
      tool_name: "WebSearch",
      params: { query: String(item.query || "") },
      success: true,
      output: "",
    };
  }
  if (itemType === "file_change") {
    const changes = Array.isArray(item.changes) ? item.changes : [];
    return {
      tool_call_id: toolCallId,
      tool_name: "FileChange",
      params: { changes },
      success: item.status === "completed",
      output: jsonOutput({ changes, status: item.status }),
    };
  }
  return null;
}

function emit(record) {
  console.log(JSON.stringify({ schema_version: SCHEMA_VERSION, ...record }));
}

function digestJsonable(value) {
  return `sha256:${crypto.createHash("sha256").update(JSON.stringify(value ?? {}, objectKeySort)).digest("hex")}`;
}

function redactedTextPayload(value, field) {
  const text = String(value || "");
  return {
    [`${field}_digest`]: `sha256:${crypto.createHash("sha256").update(text).digest("hex")}`,
    [`${field}_length`]: text.length,
    [`${field}_excerpt`]: text.slice(-300),
  };
}

function objectOrEmpty(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function jsonOutput(value) {
  return JSON.stringify(value ?? null, objectKeySort);
}

function objectKeySort(key, value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return value;
  }
  return Object.keys(value)
    .sort()
    .reduce((out, childKey) => {
      out[childKey] = value[childKey];
      return out;
    }, {});
}
