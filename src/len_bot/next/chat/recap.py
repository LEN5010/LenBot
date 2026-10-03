"""Text-context estimates and complete native tool-group compaction boundaries."""

from dataclasses import dataclass
from math import ceil
from pathlib import Path
from string import Template

from ..storage.store import encode


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
        (Path(__file__).resolve().parents[2] / "prompts" / "next_recap_context.md").read_text()
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
        if not pending and (index == len(entries) or entries[index][1]["role"] == "user"):
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
                    keep_recent_entries: int, summary_output_tokens: int,
                    recap: str | None, summary_template: str, window_tokens: int) -> CompactionPlan:
    boundaries = [cut for cut in complete_boundaries(entries) if cut < len(entries)]
    if not boundaries:
        raise ContextBudgetError("没有可压缩的旧完整对话段；当前消息或单个工具组超过输入预算")
    preferred = len(entries) - keep_recent_entries
    start = max((cut for cut in boundaries if cut <= preferred), default=boundaries[0])
    for desired_index, cut in enumerate(boundaries):
        if cut < start:
            continue
        kept = entries[cut:]
        without_recap = [system] + project_history("", kept) + [state]
        available = trigger_tokens - estimate_request(without_recap, tools, output_tokens)
        if available >= summary_output_tokens:
            break
    else:
        raise ContextBudgetError("系统、工具与最新完整对话段没有给回想留下预算；原文未裁剪")

    prompt = Template(summary_template).substitute(summary_budget_tokens=available)

    def request_at(cut: int) -> list[dict]:
        return [{"role": "system", "content": prompt},
                {"role": "user", "content": recap_source(recap, entries[:cut])}]

    # Prefix size grows monotonically. Search complete boundaries, not raw tokens.
    low, high = 0, desired_index
    selected = None
    while low <= high:
        middle = (low + high) // 2
        cut = boundaries[middle]
        request = request_at(cut)
        if estimate_request(request, [], summary_output_tokens) <= window_tokens:
            selected = CompactionPlan(entries[cut - 1][0], available, request)
            low = middle + 1
        else:
            high = middle - 1
    if selected is None:
        required = estimate_request(request_at(boundaries[0]), [], summary_output_tokens)
        raise ContextBudgetError(
            f"最早完整旧段的回想请求估算 {required} token，超过配置窗口 {window_tokens}；原文与工具组未拆分")
    return selected


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
