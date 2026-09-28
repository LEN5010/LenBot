"""One bounded local-memory extraction from actual stored chat messages."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path
from string import Template

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .context import ContextBudgetError, estimate_request
from .memory_local import LocalMemory, LocalMemoryChange
from .messages import ChatMessage, render_message
from .model import ChatModel, ModelProtocolError, ModelReply, ToolCall
from .model_slots import ModelSlots
from .pricing import ModelPrice, estimate_cost
from .store import encode


PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "next_memory_extract.md"
STRICT = ConfigDict(extra="forbid", strict=True)


class BrowseArguments(BaseModel):
    model_config = STRICT
    path: str = ""
    offset: int = Field(default=0, ge=0, strict=True)
    limit: int = Field(default=20, ge=1, le=100, strict=True)


class ReadArguments(BaseModel):
    model_config = STRICT
    path: str = Field(min_length=1)


class SearchArguments(BaseModel):
    model_config = STRICT
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20, strict=True)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class WriteArguments(BaseModel):
    model_config = STRICT
    path: str = Field(min_length=1)
    content: str
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value


TOOLS = [{"type": "function", "function": {
    "name": name,
    "description": description,
    "parameters": arguments.model_json_schema(),
}} for name, description, arguments in (
    ("memory_browse", "列出当前来源场景的记忆目录；path为空表示本场景根目录。", BrowseArguments),
    ("memory_read", "读取当前来源场景的完整 Markdown 文件，供修改前核对。", ReadArguments),
    ("memory_search", "仅在当前来源场景检索记忆正文，不读取公共或其他场景。", SearchArguments),
    ("memory_write", "替换当前来源场景的完整 Markdown 正文并记录实际修改原因。", WriteArguments),
)]


@dataclass(frozen=True, slots=True)
class ExtractResult:
    summary: str
    write_count: int
    failed_tools: int


def _source_messages(scene: str, source: list[ChatMessage], timezone: str) -> list[dict]:
    if not 1 <= len(source) <= 100:
        raise ValueError("memory extraction requires 1..100 original messages")
    rows: list[dict] = []
    for message in source:
        if message.scene != scene:
            raise ValueError(f"memory extraction message {message.id} belongs to {message.scene!r}, not {scene!r}")
        if message.is_self:
            if message.send_status not in {"sent", "received"}:
                raise ValueError(f"memory extraction Bot message {message.id} was not actually sent")
        elif message.send_status != "received":
            raise ValueError(f"memory extraction original message {message.id} was not received")
        rows.append({
            "record": message.id,
            "platform_message_id": message.platform_message_id,
            "sender_qq": message.sender.uid,
            "is_self": message.is_self,
            "text": render_message(message, timezone=timezone),
        })
    return rows


async def _tool(scene: str, backend: LocalMemory, call: ToolCall) -> tuple[str, LocalMemoryChange | None]:
    if call.name == "memory_browse":
        arguments = BrowseArguments.model_validate(call.arguments)
        result = await backend.browse(scene, arguments.path, scope="scene",
                                      offset=arguments.offset, limit=arguments.limit)
        return encode(asdict(result)), None
    if call.name == "memory_read":
        arguments = ReadArguments.model_validate(call.arguments)
        result = await backend.read(scene, arguments.path, scope="scene")
        return encode(asdict(result)), None
    if call.name == "memory_search":
        arguments = SearchArguments.model_validate(call.arguments)
        hits = await backend.search(scene, arguments.query, arguments.limit, include_public=False)
        return encode({"hits": [asdict(hit) for hit in hits]}), None
    if call.name == "memory_write":
        arguments = WriteArguments.model_validate(call.arguments)
        change = await backend.write(scene, arguments.path, arguments.content, arguments.reason)
        return encode({"action": change.action, "path": change.path,
                       "changed_at": change.changed_at,
                       "before_chars": None if change.before is None else len(change.before),
                       "after_chars": None if change.after is None else len(change.after)}), change
    raise ValueError(f"memory extraction tool was not offered: {call.name}")


async def extract_local(
    scene: str,
    messages: list[ChatMessage],
    backend: LocalMemory,
    model: ChatModel,
    *,
    timezone: str,
    context_window_tokens: int,
    max_steps: int,
    start_call: Callable[[dict], int],
    finish_call: Callable[[int, object | None, dict | None, str | None, dict | None], None],
    record_tool: Callable[[str, str, dict, str, str | None], None],
    record_write: Callable[[LocalMemoryChange], None],
    price: ModelPrice | None = None,
    slots: ModelSlots | None = None,
) -> ExtractResult:
    """Run under the host-held scene write lock with its freshly selected input."""
    if max_steps <= 0 or context_window_tokens <= model.settings.max_output_tokens:
        raise ValueError("memory extraction needs positive steps and a window larger than output reservation")
    source = _source_messages(scene, messages, timezone)
    conversation = [
        {"role": "system", "content": Template(PROMPT.read_text()).substitute(scene=scene)},
        {"role": "user", "content": encode({"scene": scene, "messages": source})},
    ]
    written = 0
    failed_tools = 0
    for _ in range(max_steps):
        estimated = estimate_request(conversation, TOOLS, model.settings.max_output_tokens)
        if estimated > context_window_tokens:
            raise ContextBudgetError(
                f"memory extraction request with reserved output estimates {estimated} tokens, "
                f"exceeding configured window {context_window_tokens}; model was not called")
        reply: ModelReply | None = None
        async with (slots.slot(direct=False, scene=scene) if slots is not None else nullcontext()):
            call_id = start_call({
                "settings": model.settings.model_dump(exclude={"api_key"}),
                "messages": conversation, "tools": TOOLS,
                "estimated_total_tokens": estimated,
                "context_window_tokens": context_window_tokens,
                "price": None if price is None else price.model_dump(mode="json"),
            })
            try:
                reply = await model.complete(conversation, TOOLS)
            except BaseException as error:
                response: object | None = None
                usage: dict | None = None
                token_usage = None
                if isinstance(error, ModelProtocolError):
                    response, usage, token_usage = error.response, error.usage, error.token_usage
                finish_call(call_id, response, usage, f"{type(error).__name__}: {error}",
                            estimate_cost(price, token_usage))
                raise
            finish_call(call_id, {"message": reply.message, "finish_reason": reply.finish_reason},
                        reply.usage, None, estimate_cost(price, reply.token_usage))
        conversation.append(reply.message)
        if not reply.tool_calls:
            return ExtractResult(summary=reply.text, write_count=written,
                                 failed_tools=failed_tools)
        for call in reply.tool_calls:
            try:
                content, change = await _tool(scene, backend, call)
            except Exception as error:
                original = f"{type(error).__name__}: {error}"
                content = f"{call.name} 失败：{original}"
                record_tool(call.id, call.name, call.arguments, content, original)
                failed_tools += 1
            else:
                if change is not None:
                    record_write(change)
                record_tool(call.id, call.name, call.arguments, content, None)
                if change is not None:
                    written += 1
            conversation.append({"role": "tool", "tool_call_id": call.id, "content": content})
    raise RuntimeError(f"memory extraction exhausted max_steps={max_steps} after "
                       f"{written} actual writes and {failed_tools} failed tools; final response still called tools")
