"""Non-streaming Chat Completions boundary for one configured model binding."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from len_bot.next.models.pricing import TokenUsage


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api: Literal["openai-chat"]
    base_url: str
    api_key: str = Field(repr=False)
    model: str
    reasoning_effort: str | None = None
    temperature: float = Field(default=0.6, ge=0, le=2, allow_inf_nan=False)
    max_output_tokens: int = Field(default=1024, gt=0)
    timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.netloc
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            raise ValueError("base_url must be an HTTP(S) URL without credentials, query or fragment")
        return value

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
    )


class ChatModel:
    def __init__(self, settings: ModelSettings):
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/") + "/",
            timeout=settings.timeout_seconds,
            trust_env=False,
            transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
            headers={"Authorization": f"Bearer {settings.api_key}"},
        )

    async def __aenter__(self) -> ChatModel:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def complete(self, messages: list[dict], tools: list[dict], *,
                       max_output_tokens: int | None = None) -> ModelReply:
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature,
            "max_completion_tokens": (
                self.settings.max_output_tokens if max_output_tokens is None else max_output_tokens
            ),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if self.settings.reasoning_effort is not None:
            payload["reasoning_effort"] = self.settings.reasoning_effort
        response = await self._client.post("chat/completions", json=payload)
        if not response.is_success:
            raise ModelHTTPError(f"Model HTTP {response.status_code}: {response.text}")
        try:
            body = response.json()
        except json.JSONDecodeError as error:
            raise ModelProtocolError(
                f"Invalid chat completion JSON: {error}; response fragment: {response.text[:500]}",
                response=response.text,
            ) from error
        return parse_chat_completion(body)
