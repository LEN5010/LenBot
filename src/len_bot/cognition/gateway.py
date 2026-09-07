"""One frozen model binding and lossless native assistant continuations."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import dataclass
from typing import Any

from len_bot.cognition.providers import RouteResolution
from len_bot.cognition.call_store import estimate_request
from len_bot.cognition.projection import estimate_tokens


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
    call_id: str | None = None
    local_estimate: dict[str, Any] | None = None


class ModelGateway:
    def __init__(self, binding: RouteResolution, max_output_tokens: int = 4096, *,
                 call_store=None, scene_id: str = "", episode_id: str | None = None,
                 job_id: str | None = None, batch_id: str | None = None, purpose: str | None = None):
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        self.binding = binding
        self.max_output_tokens = max_output_tokens
        self.call_store = call_store
        self.scene_id = scene_id
        self.episode_id = episode_id
        self.job_id = job_id
        self.batch_id = batch_id
        self.purpose = purpose or binding.role
        if self.purpose not in {"conversation", "work", "history_maintenance", "work_compression", "skill_maintenance", "capability_probe"}:
            raise ValueError("Maintenance requests need an explicit accounting purpose")

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
        estimate = estimate_request(request["messages"], request["tools"])
        for message in request['messages']:
            message.pop('_context_section', None)
        call_id = None
        if self.call_store is not None:
            call_id = await self.call_store.begin_model_call(
                scene_id=self.scene_id, episode_id=self.episode_id, job_id=self.job_id, batch_id=self.batch_id,
                role=self.binding.role, purpose=self.purpose, provider_id=self.binding.provider_id,
                model=self.binding.model, reasoning_effort=self.binding.reasoning_effort, estimate=estimate,
            )
        started = time.monotonic()
        usage = None
        try:
            raw = await self.binding.client.chat.completions.with_raw_response.create(**request)
            # Preserve provider-native signatures and continuation fields.
            body = raw.http_response.json()
            usage = body.get("usage") or None
            response = self._parse_response(body, round((time.monotonic() - started) * 1000))
        except BaseException as error:
            if call_id is not None:
                # Client cancellation cannot prove the supplier stopped billing.
                await asyncio.shield(self.call_store.end_model_call(
                    call_id, status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
                    usage=usage, error_type=type(error).__name__,
                ))
            raise
        if call_id is not None:
            await asyncio.shield(self.call_store.end_model_call(
                call_id, status="completed", usage=usage,
                output_estimate_tokens=estimate_tokens(json.dumps(response.continuation, ensure_ascii=False)),
            ))
        return GatewayResponse(continuation=response.continuation, tool_calls=response.tool_calls,
                               finish_reason=response.finish_reason, usage=response.usage,
                               latency_ms=response.latency_ms, call_id=call_id, local_estimate=estimate)

    @staticmethod
    def _parse_response(body: dict, latency_ms: int) -> GatewayResponse:
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
