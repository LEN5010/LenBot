"""One frozen model binding and lossless native assistant continuations."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any

from len_bot.cognition.providers import RouteResolution


class ModelProtocolError(RuntimeError):
    """The provider did not return a complete chat-completions response."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class GatewayResponse:
    continuation: dict[str, Any]
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str | None
    usage: dict[str, Any]
    latency_ms: int


class ModelGateway:
    def __init__(self, binding: RouteResolution, max_output_tokens: int = 4096):
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        self.binding = binding
        self.max_output_tokens = max_output_tokens

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any],
    ) -> GatewayResponse:
        request: dict[str, Any] = {
            "model": self.binding.model,
            "messages": copy.deepcopy(messages),
            "tools": copy.deepcopy(tools),
            "tool_choice": tool_choice,
            "stream": False,
            "max_completion_tokens": self.max_output_tokens,
        }
        if self.binding.reasoning_effort is not None:
            request["reasoning_effort"] = self.binding.reasoning_effort
        started = time.monotonic()
        raw = await self.binding.client.chat.completions.with_raw_response.create(**request)
        # Read the provider object before the SDK normalizes known fields. This
        # carries signatures, reasoning blocks and unknown extensions verbatim
        # into the next request; arguments remain the original JSON string.
        body = raw.http_response.json()
        latency_ms = round((time.monotonic() - started) * 1000)
        try:
            choice = body["choices"][0]
            continuation = choice["message"]
            if not isinstance(continuation, dict) or continuation.get("role") != "assistant":
                raise ValueError("Expected an assistant message")
            calls = []
            for call in continuation.get("tool_calls") or []:
                if call.get("type") != "function":
                    raise ValueError("Unsupported native tool-call type")
                function = call["function"]
                tool_call = ToolCall(id=call["id"], name=function["name"], arguments=function["arguments"])
                if not all(isinstance(value, str) for value in (tool_call.id, tool_call.name, tool_call.arguments)):
                    raise ValueError("Native tool-call id, name and arguments must be strings")
                calls.append(tool_call)
            if len({call.id for call in calls}) != len(calls):
                raise ValueError("Duplicate native tool-call IDs")
            usage = body.get("usage") or {}
            if not isinstance(usage, dict):
                raise ValueError("Expected usage metadata object")
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ModelProtocolError(f"Invalid model response: {exc}") from exc
        return GatewayResponse(continuation=copy.deepcopy(continuation), tool_calls=tuple(calls),
                               finish_reason=choice.get("finish_reason"), usage=copy.deepcopy(usage),
                               latency_ms=latency_ms)
