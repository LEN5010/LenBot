"""Text-context estimates and complete native tool-group compaction boundaries."""

from dataclasses import dataclass
from math import ceil

from .store import encode


Entry = tuple[int, dict]


class ContextBudgetError(ValueError):
    pass


def estimate_request(messages: list[dict], tools: list[dict], output_tokens: int) -> int:
    # A UTF-8 / 3 heuristic, not a provider tokenizer or a billing measurement.
    payload = encode({"messages": messages, "tools": tools})
    return ceil(len(payload.encode("utf-8")) / 3) + output_tokens


def project_history(recap: str | None, entries: list[Entry]) -> list[dict]:
    messages = [] if recap is None else [{"role": "user", "content": recap}]
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
    source: list[Entry]
    kept: list[Entry]
    summary_budget_tokens: int


def plan_compaction(entries: list[Entry], *, system: dict, state: dict,
                    tools: list[dict], output_tokens: int, trigger_tokens: int,
                    keep_recent_entries: int, summary_output_tokens: int) -> CompactionPlan:
    boundaries = [cut for cut in complete_boundaries(entries) if cut < len(entries)]
    if not boundaries:
        raise ContextBudgetError("没有可压缩的旧完整对话段；当前消息或单个工具组超过输入预算")
    preferred = len(entries) - keep_recent_entries
    start = max((cut for cut in boundaries if cut <= preferred), default=boundaries[0])
    for cut in boundaries:
        if cut < start:
            continue
        kept = entries[cut:]
        without_recap = [system] + project_history("", kept) + [state]
        available = trigger_tokens - estimate_request(without_recap, tools, output_tokens)
        if available >= summary_output_tokens:
            return CompactionPlan(entries[cut - 1][0], entries[:cut], kept, available)
    raise ContextBudgetError("系统、工具与最新完整对话段没有给回想留下预算；原文未裁剪")


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
