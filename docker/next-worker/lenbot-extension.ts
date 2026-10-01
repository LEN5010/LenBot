import { request as httpRequest } from "node:http";
import { readFile } from "node:fs/promises";
import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const TASK_API_FILE = "/run/lenbot/task-api.json";
const TASK_API_BASE = "http://127.0.0.1:18181";
type BuiltinDataToolName = "recall_chat" | "memory" | "transcribe" | "account_browser";
type MCPToolName = `mcp__${string}`;
type DataToolName = BuiltinDataToolName | MCPToolName;
type DataTool = { name: DataToolName; description: string; parameters: Record<string, unknown> };
type TaskApiSettings = { token: string; timeoutMs: number; tools: DataTool[] };
const DATA_ROUTES = {recall_chat: "/task/recall-chat", memory: "/task/memory", transcribe: "/task/transcribe", account_browser: "/task/account-browser"} as const;
const DATA_LABELS = {recall_chat: "Recall chat", memory: "Memory", transcribe: "Transcribe scene audio", account_browser: "Account browser"} as const;

function isMCPName(value: string): value is MCPToolName {
  return /^mcp__[a-zA-Z0-9_-]{1,59}$/.test(value);
}

function result(text: string, details?: unknown) {
  return { content: [{ type: "text" as const, text }], details };
}

async function taskApi(): Promise<TaskApiSettings> {
  const raw = await readFile(TASK_API_FILE, "utf8");
  const value: unknown = JSON.parse(raw);
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${TASK_API_FILE}: expected a task API object`);
  }
  const settings = value as Record<string, unknown>;
  if (settings.base_url !== TASK_API_BASE || typeof settings.token !== "string" || !settings.token
    || typeof settings.timeout_seconds !== "number" || !Number.isFinite(settings.timeout_seconds)
    || settings.timeout_seconds <= 0) {
    throw new Error(`${TASK_API_FILE}: invalid loopback task API settings`);
  }
  if (!Array.isArray(settings.tools)) throw new Error(`${TASK_API_FILE}: tools must be an array`);
  const tools: DataTool[] = [];
  const names = new Set<DataToolName>();
  for (const [index, value] of settings.tools.entries()) {
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      throw new Error(`${TASK_API_FILE}: tools[${index}] must be an object`);
    }
    const tool = value as Record<string, unknown>;
    const name = tool.name;
    if (name !== "recall_chat" && name !== "memory" && name !== "transcribe" && name !== "account_browser"
      && !(typeof name === "string" && isMCPName(name))) {
      throw new Error(`${TASK_API_FILE}: invalid tools[${index}].name: ${JSON.stringify(name)}`);
    }
    if (names.has(name)) throw new Error(`${TASK_API_FILE}: duplicate tool name ${name}`);
    if (typeof tool.description !== "string" || !tool.description.trim()) {
      throw new Error(`${TASK_API_FILE}: tools[${index}].description must be nonblank`);
    }
    const parameters = tool.parameters;
    if (typeof parameters !== "object" || parameters === null || Array.isArray(parameters)
      || (parameters as Record<string, unknown>).type !== "object") {
      throw new Error(`${TASK_API_FILE}: tools[${index}].parameters must be an object schema`);
    }
    names.add(name);
    tools.push({ name, description: tool.description, parameters: parameters as Record<string, unknown> });
  }
  return { token: settings.token, timeoutMs: settings.timeout_seconds * 1000, tools };
}

async function taskPost(
  settings: TaskApiSettings,
  route: "/task/deliver-file" | "/task/network" | "/task/recall-chat" | "/task/memory" | "/task/transcribe" | "/task/account-browser" | "/task/mcp",
  operation: "deliver_file" | "network_status" | DataToolName,
  body: Record<string, unknown>,
  signal?: AbortSignal,
) {
  const humanWait = operation === "account_browser" && body.method === "request_help";
  const encoded = JSON.stringify(body);
  const headers = { Authorization: `Bearer ${settings.token}`, "Content-Type": "application/json" };
  let raw: string, status: number;
  if (humanWait) {
    // Human time is bounded by the host; fetch's built-in header timeout is not an input deadline.
    const response = await new Promise<{ raw: string; status: number }>((resolve, reject) => {
      const request = httpRequest(`${TASK_API_BASE}${route}`, {
        method: "POST", headers: { ...headers, "Content-Length": Buffer.byteLength(encoded) }, signal,
      }, response => {
        if (response.statusCode === undefined) { response.destroy(); reject(new Error("Task bridge omitted HTTP status")); return; }
        const status = response.statusCode;
        response.setEncoding("utf8");
        let raw = "";
        response.on("data", chunk => { raw += chunk; });
        response.on("error", reject);
        response.on("end", () => resolve({ raw, status }));
      });
      request.on("error", reject);
      request.end(encoded);
    });
    ({ raw, status } = response);
  } else {
    const timeout = AbortSignal.timeout(Math.ceil(settings.timeoutMs));
    const response = await fetch(`${TASK_API_BASE}${route}`, {
      method: "POST", headers, body: encoded,
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout, redirect: "error",
    });
    raw = await response.text(); status = response.status;
  }
  if (status < 200 || status >= 300) throw new Error(`${operation} HTTP ${status}: ${raw}`);
  let payload: unknown;
  try { payload = JSON.parse(raw); }
  catch (error) { throw new Error(`${operation} invalid JSON: ${String(error)}; raw=${raw}`); }
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new Error(`${operation} expected JSON object; raw=${raw}`);
  }
  return { raw, payload: payload as Record<string, unknown> };
}

export default async function lenbotExtension(pi: ExtensionAPI) {
  const settings = await taskApi();
  pi.registerTool({
    name: "report_progress",
    executionMode: "sequential",
    label: "Report progress",
    description: "Record real task progress in this Pi session; this does not send a message to the requester.",
    parameters: Type.Object({ text: Type.String({ description: "Actual progress to record" }) }),
    async execute(_id, { text }) {
      pi.appendEntry("lenbot_progress", { text });
      return result("已记录到本次会话；未声称已发给请求人。", { recorded: true, text });
    },
  });

  pi.registerTool({
    name: "ask_requester",
    executionMode: "sequential",
    label: "Ask requester",
    description: "Ask the requester one question through Pi's native interactive UI.",
    parameters: Type.Object({ question: Type.String({ description: "Question for the requester" }) }),
    async execute(_id, { question }, signal, _update, ctx) {
      if (!ctx.hasUI) throw new Error("Requester UI is unavailable");
      const answer = await ctx.ui.input(question, undefined, { signal });
      return answer === undefined
        ? result("请求人未回答（取消或超时）。", { answered: false })
        : result(`收到补充信息：${answer}`, { answered: true, answer });
    },
  });

  pi.registerTool({
    name: "confirm_action",
    executionMode: "sequential",
    label: "Confirm action",
    description: "Request explicit approval through Pi's native interactive UI; cancellation is not approval.",
    parameters: Type.Object({ description: Type.String({ description: "Action requiring approval" }) }),
    async execute(_id, { description }, signal, _update, ctx) {
      if (!ctx.hasUI) throw new Error("Requester UI is unavailable");
      const approved = await ctx.ui.confirm("确认执行？", description, { signal });
      return result(approved ? "当前操作已获明确同意。" : "当前操作未获同意（取消或超时）；不得执行。", { approved });
    },
  });

  pi.registerTool({
    name: "deliver_file",
    executionMode: "sequential",
    label: "Deliver file",
    description: "Submit a local file to the task-only host endpoint; registration is not QQ upload confirmation.",
    parameters: Type.Object({
      path: Type.String({ description: "Existing local file path" }),
      name: Type.String({ description: "Delivery filename" }),
      note: Type.String({ description: "Delivery note" }),
    }),
    async execute(_id, { path, name, note }, signal) {
      const { raw, payload } = await taskPost(settings, "/task/deliver-file", "deliver_file", { path, name, note }, signal);
      return result(`宿主原始结果：${raw}\n文件复制登记与 QQ 上传是不同状态；以宿主实际返回字段为准。`, payload);
    },
  });

  pi.registerTool({
    name: "network_status",
    executionMode: "sequential",
    label: "Network status",
    description: "Read the host's known task and scene-today egress usage, limits, and last recorded network error. This does not probe connectivity or change limits; enabled does not mean connected.",
    parameters: Type.Object({}),
    async execute(_id, _args, signal) {
      const { raw, payload } = await taskPost(settings, "/task/network", "network_status", {}, signal);
      return result(raw, payload);
    },
  });

  for (const tool of settings.tools) {
    const route = isMCPName(tool.name) ? "/task/mcp" : DATA_ROUTES[tool.name];
    pi.registerTool({
      name: tool.name,
      executionMode: "sequential",
      label: isMCPName(tool.name) ? tool.name : DATA_LABELS[tool.name],
      description: tool.description,
      parameters: Type.Unsafe<Record<string, unknown>>(tool.parameters),
      async execute(_id, args, signal) {
        const body = isMCPName(tool.name) ? {name: tool.name, arguments: args} : args;
        const { raw, payload } = await taskPost(settings, route, tool.name, body, signal);
        if (typeof payload.content !== "string") {
          throw new Error(`${tool.name} expected a string content; raw=${raw}`);
        }
        if (tool.name === "account_browser" && payload.image !== undefined) {
          const image = payload.image as {data?: unknown; mimeType?: unknown};
          if (typeof image.data !== "string" || image.mimeType !== "image/png") throw new Error(`Invalid browser image: ${raw.slice(0,500)}`);
          return {content:[{type:"text" as const, text:payload.content}, {type:"image" as const, data:image.data, mimeType:image.mimeType}], details:{content:payload.content}};
        }
        return result(payload.content, payload);
      },
    });
  }

  pi.on("session_before_compact", (event) => {
    if (event.willRetry === true) return { cancel: true };
  });
}
