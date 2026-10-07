"""One non-streaming request through the configured native chat protocol."""

from __future__ import annotations

import copy
import asyncio
import json
import math
from dataclasses import dataclass
from typing import Any, Literal
from .providers import ChatAPI, http_client, valid_url

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from len_bot.next.models.tokens import TokenUsage


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api: ChatAPI
    base_url: str
    api_key: str = Field(repr=False)
    model: str
    reasoning_effort: str | None = None
    thinking_budget_tokens: int | None = Field(default=None, ge=0)
    output_token_field: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    proxy: str | None = Field(default=None, repr=False)
    temperature: float | None = Field(default=0.6, ge=0, le=2, allow_inf_nan=False)
    max_output_tokens: int = Field(default=1024, gt=0)
    timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)

    @classmethod
    def from_binding(cls, provider, binding) -> ModelSettings:
        return cls(api=provider.api, base_url=provider.base_url, api_key=provider.api_key, proxy=provider.proxy,
                   **binding.model_dump(exclude={'provider', 'context_window_tokens', 'history_policy'}))

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        return valid_url(value)

    @field_validator("api_key", "model")
    @classmethod
    def required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("reasoning_effort")
    @classmethod
    def nonblank_reasoning_effort(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def protocol_options(self):
        if self.thinking_budget_tokens is not None and self.reasoning_effort is not None:
            raise ValueError('思考额度与思考强度只能选一个')
        if self.thinking_budget_tokens is not None and self.api not in {'anthropic', 'gemini'}:
            raise ValueError('思考额度仅支持 Anthropic 和 Gemini')
        if self.api == 'anthropic' and (self.reasoning_effort is not None or self.thinking_budget_tokens is not None):
            if self.temperature is not None:
                raise ValueError('Anthropic 思考模式需要将温度留空')
            if self.thinking_budget_tokens is not None and not 1024 <= self.thinking_budget_tokens < self.max_output_tokens:
                raise ValueError('Anthropic 思考额度至少 1024，且必须小于输出额度')
        if self.api != 'openai-chat' and self.output_token_field != 'max_completion_tokens':
            raise ValueError('输出字段选择仅适用于 OpenAI 兼容聊天接口')
        return self


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelReply:
    message: dict[str, Any]
    text: str
    tool_calls: list[ToolCall]
    finish_reason: str
    usage: dict[str, Any] | None
    token_usage: TokenUsage | None
    response: dict | None = None


class ModelProtocolError(RuntimeError):
    """A successful HTTP response did not contain a complete model reply."""

    def __init__(self, message: str, *, response: object | None = None,
                 usage: dict[str, Any] | None = None, token_usage: TokenUsage | None = None):
        super().__init__(message)
        self.response = copy.deepcopy(response)
        self.usage = copy.deepcopy(usage)
        self.token_usage = token_usage


class ModelHTTPError(RuntimeError):
    """The configured provider rejected a model request."""


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant in tool arguments: {value}")


def _finite_json_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'non-finite JSON number: {value[:100]}')
    return number


def parse_token_usage(usage: dict[str, Any] | None) -> TokenUsage | None:
    if usage is None:
        return None

    def token(value: object, field: str) -> int | None:
        if value is None:
            return None
        if type(value) is not int or value < 0:
            raise ValueError(f"usage.{field} must be a nonnegative integer or null")
        return value

    prompt = token(usage.get("prompt_tokens"), "prompt_tokens")
    completion = token(usage.get("completion_tokens"), "completion_tokens")
    details = usage.get("prompt_tokens_details")
    if details is not None and not isinstance(details, dict):
        raise ValueError("usage.prompt_tokens_details must be an object or null")
    cached = token(None if details is None else details.get("cached_tokens"),
                   "prompt_tokens_details.cached_tokens")
    if prompt is not None and cached is not None and cached > prompt:
        raise ValueError("usage.prompt_tokens_details.cached_tokens exceeds prompt_tokens")
    return TokenUsage(prompt, completion, cached)


def parse_chat_completion(body: object) -> ModelReply:
    """Parse a provider response once, retaining its native assistant message."""
    token_usage = None
    try:
        if not isinstance(body, dict):
            raise ValueError("expected a response object")
        usage = body.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise ValueError("usage must be an object or null")
        token_usage = parse_token_usage(usage)
        choices = body["choices"]
        if not isinstance(choices, list) or not choices:
            raise ValueError("expected a nonempty choices array")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ValueError("expected a choice object")
        finish_reason = choice["finish_reason"]
        if finish_reason not in {"stop", "tool_calls"}:
            raise ValueError(f"incomplete or unsupported finish_reason: {finish_reason!r}")
        message = choice["message"]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("expected an assistant message")
        content = message["content"]
        if content is not None and not isinstance(content, str):
            raise ValueError("assistant content must be a string or null")

        native_calls = message.get("tool_calls")
        if native_calls is None:
            native_calls = []
        if not isinstance(native_calls, list):
            raise ValueError("tool_calls must be an array")
        calls: list[ToolCall] = []
        for native in native_calls:
            if not isinstance(native, dict) or native.get("type") != "function":
                raise ValueError("expected a native function tool call")
            call_id = native["id"]
            function = native["function"]
            if not isinstance(function, dict):
                raise ValueError("expected a function object")
            name = function["name"]
            raw_arguments = function["arguments"]
            if not isinstance(call_id, str) or not call_id.strip():
                raise ValueError("tool call id must be a nonempty string")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("tool name must be a nonempty string")
            if not isinstance(raw_arguments, str):
                raise ValueError("tool arguments must be a JSON string")
            try:
                arguments = json.loads(raw_arguments, parse_constant=_reject_non_json_constant)
            except json.JSONDecodeError as error:
                raise ValueError(f"incomplete or invalid tool arguments JSON: {error}") from error
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must decode to an object")
            calls.append(ToolCall(call_id, name, arguments))
        if len({call.id for call in calls}) != len(calls):
            raise ValueError("duplicate tool call ids")
        if (finish_reason == "tool_calls") != bool(calls):
            raise ValueError("finish_reason and tool calls disagree")

    except (KeyError, TypeError, ValueError) as error:
        fragment = json.dumps(body, ensure_ascii=False, default=repr)[:500]
        raw_usage = body.get("usage") if isinstance(body, dict) else None
        usage = raw_usage if isinstance(raw_usage, dict) else None
        raise ModelProtocolError(
            f"Invalid chat completion: {error}; response fragment: {fragment}",
            response=body, usage=usage, token_usage=token_usage,
        ) from error

    return ModelReply(
        message=copy.deepcopy(message),
        text=content or "",
        tool_calls=calls,
        finish_reason=finish_reason,
        usage=copy.deepcopy(usage),
        token_usage=token_usage,
        response=copy.deepcopy(body),
    )


class ChatModel:
    def __init__(self, settings: ModelSettings):
        self.settings = settings
        self._client = http_client(base_url=settings.base_url, api_key=settings.api_key, api=settings.api,
                                   timeout_seconds=settings.timeout_seconds, proxy=settings.proxy)

    async def __aenter__(self) -> ChatModel:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def complete(self, messages: list[dict], tools: list[dict], *,
                       max_output_tokens: int | None = None, session_id: str | None = None) -> ModelReply:
        from .protocols import build_request, parse_reply
        path, payload = build_request(self.settings, messages, tools, max_output_tokens=max_output_tokens)
        async with asyncio.timeout(self.settings.timeout_seconds):
            response = await self._client.post(path, json=payload,
                                               headers={} if session_id is None else {"Session-Id": session_id})
        if not response.is_success:
            fragment = response.text.replace(self.settings.api_key, '[hidden]')[:2000]
            raise ModelHTTPError(f"Model HTTP {response.status_code}: {fragment}")
        try:
            body = json.loads(response.text, parse_constant=_reject_non_json_constant, parse_float=_finite_json_number)
        except ValueError as error:
            raise ModelProtocolError(
                f"Invalid chat completion JSON: {error}; response fragment: {response.text[:500]}",
                response=response.text[:2000],
            ) from error
        return parse_reply(self.settings, body)
