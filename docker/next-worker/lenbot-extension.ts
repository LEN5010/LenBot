import { readFile } from "node:fs/promises";
import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const TASK_API_FILE = "/run/lenbot/task-api.json";
const TASK_API_BASE = "http://127.0.0.1:18181";

function result(text: string, details?: unknown) {
  return { content: [{ type: "text" as const, text }], details };
}

async function taskApi() {
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
  return { token: settings.token, timeoutMs: settings.timeout_seconds * 1000 };
}

async function taskPost(
  route: "/task/deliver-file" | "/task/network",
  operation: "deliver_file" | "network_status",
  body: Record<string, unknown>,
  signal?: AbortSignal,
) {
  const { token, timeoutMs } = await taskApi();
  const timeout = AbortSignal.timeout(Math.ceil(timeoutMs));
  const response = await fetch(`${TASK_API_BASE}${route}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    redirect: "error",
  });
  const raw = await response.text();
  if (!response.ok) throw new Error(`${operation} HTTP ${response.status}: ${raw}`);
  let payload: unknown;
  try { payload = JSON.parse(raw); }
  catch (error) { throw new Error(`${operation} invalid JSON: ${String(error)}; raw=${raw}`); }
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new Error(`${operation} expected JSON object; raw=${raw}`);
  }
  return { raw, payload };
}

export default function lenbotExtension(pi: ExtensionAPI) {
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
      const { raw, payload } = await taskPost("/task/deliver-file", "deliver_file", { path, name, note }, signal);
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
      const { raw, payload } = await taskPost("/task/network", "network_status", {}, signal);
      return result(raw, payload);
    },
  });

  pi.on("session_before_compact", (event) => {
    if (event.willRetry === true) return { cancel: true };
  });
}
