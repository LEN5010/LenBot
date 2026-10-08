"""Text-context estimates and complete native tool-group compaction boundaries."""

from dataclasses import dataclass
from math import ceil
from string import Template

from ..storage.store import encode
from ..prompt_files import read_prompt


Entry = tuple[int, dict]


class ContextBudgetError(ValueError):
    pass


def estimate_request(messages: list[dict], tools: list[dict], output_tokens: int) -> int:
    # A UTF-8 / 3 heuristic, not a provider tokenizer or a billing measurement.
    payload = encode({"messages": messages, "tools": tools})
    return ceil(len(payload.encode("utf-8")) / 3) + output_tokens


def estimate_content(content: str) -> int:
    """Extra JSON content bytes, excluding the empty string's two quotes."""
    return ceil((len(encode(content).encode("utf-8")) - 2) / 3)


def estimate_text_request(messages: list[dict], tools: list[dict], output_tokens: int) -> int:
    """Estimate text only; image token cost remains unknown, not base64 text length."""
    text_messages = [
        {**message, "content": [block for block in message["content"] if block["type"] != "image_url"]}
        if isinstance(message["content"], list) else message
        for message in messages
    ]
    return estimate_request(text_messages, tools, output_tokens)


def project_history(recap: str | None, entries: list[Entry]) -> list[dict]:
    messages = [] if recap is None else [{"role": "user", "content": Template(
        read_prompt("next_recap_context.md")
    ).substitute(recap=recap)}]
    return messages + [message for _, message in entries]


def complete_boundaries(entries: list[Entry]) -> list[int]:
    """Return cut indexes that never separate a native call from its results."""
    pending: set[str] = set()
    boundaries = []
    for index, (_, message) in enumerate(entries, 1):
        if message["role"] == "assistant":
            calls = message.get("tool_calls")
            if calls is not None:
                pending.update(call["id"] for call in calls)
        elif message["role"] == "tool":
            pending.remove(message["tool_call_id"])
        if not pending:
            boundaries.append(index)
    if pending:
        raise ContextBudgetError("当前会话存在尚未取得结果的工具组，不能压缩")
    return boundaries


@dataclass(frozen=True)
class CompactionPlan:
    through: int
    summary_budget_tokens: int
    request_messages: list[dict]


def plan_compaction(entries: list[Entry], *, system: dict, state: dict,
                    tools: list[dict], output_tokens: int, trigger_tokens: int,
                    keep_recent_tokens: int, summary_output_tokens: int,
                    recap: str | None, summary_template: str, window_tokens: int,
                    source_entries: list[Entry] | None = None,
                    token_scale: float = 1.0) -> CompactionPlan:
    boundaries = [cut for cut in complete_boundaries(entries) if cut < len(entries)]
    if not boundaries:
        required = estimate_request([system] + project_history(recap, entries) + [state], tools, output_tokens)
        raise ContextBudgetError(
            f"没有可压缩的旧完整对话段；含 system、tools、参考与输出 {output_tokens} 的请求估算 "
            f"{required} token，压缩阈值 {trigger_tokens}、窗口 {window_tokens}；原文与工具组未拆分")
    # Start from the newest complete units, not an arbitrary message count.
    sizes = [ceil(token_scale * len(encode(message).encode('utf-8')) / 3) for _, message in entries]
    recent = 0
    preferred = len(entries)
    for index in range(len(entries) - 1, -1, -1):
        recent += sizes[index]
        if recent > keep_recent_tokens:
            break
        preferred = index
    start = min((cut for cut in boundaries if cut >= preferred), default=boundaries[-1])
    for cut in boundaries:
        if cut < start:
            continue
        kept = entries[cut:]
        without_recap = [system] + project_history("", kept) + [state]
        available = min(trigger_tokens, window_tokens - output_tokens) - ceil(
            token_scale * estimate_request(without_recap, tools, 0))
        if available >= summary_output_tokens:
            break
    else:
        raise ContextBudgetError(
            f"system、tools、参考与最新完整单位（含输出 {output_tokens}）估算 "
            f"{trigger_tokens - available} token，加回想预留 {summary_output_tokens} "
            f"超过压缩阈值 {trigger_tokens}（窗口 {window_tokens}）；原文未裁剪")

    prompt = Template(summary_template).substitute(summary_budget_tokens=available)

    source = entries[:cut] if source_entries is None else [
        entry for entry in source_entries if entry[0] <= entries[cut - 1][0]]
    request = [{"role": "system", "content": prompt},
               {"role": "user", "content": recap_source(recap, source)}]
    required = estimate_request(request, [], summary_output_tokens)
    if required > window_tokens:
        raise ContextBudgetError(
            f"待压缩段的回想请求估算 {required} token，超过配置窗口 {window_tokens}；"
            "原文与工具组未拆分，未调用模型")
    return CompactionPlan(entries[cut - 1][0], available, request)


def recap_source(recap: str | None, entries: list[Entry]) -> str:
    """Project readable facts; opaque provider continuation fields are not facts."""
    transcript = []
    for _, message in entries:
        item = {"role": message["role"], "content": message["content"]}
        if message["role"] == "assistant" and message.get("tool_calls") is not None:
            item["tool_calls"] = [
                {"id": call["id"], "name": call["function"]["name"],
                 "arguments": call["function"]["arguments"]}
                for call in message["tool_calls"]
            ]
        elif message["role"] == "tool":
            item["tool_call_id"] = message["tool_call_id"]
        transcript.append(item)
    return encode({"已有回想": recap, "新增旧会话": transcript})
