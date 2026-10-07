"""One-task, byte-transparent native model stream boundary.

The caller owns the task record and the HTTP route. This object owns only one
configured upstream binding, its task token, and actual model-call limits.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
import hmac
import json
import math
from typing import Any


from ..chat.recap import estimate_text_request
from ..models.client import ModelProtocolError, ModelSettings
from ..models.slots import ModelSlots
from ..models.tokens import token_record
from .worker_stream import ChatCompletionStream
from .native_stream import NativeStream
from ..models.providers import http_client, auth_headers
from urllib.parse import quote
from ..runtime.logs import redact, redact_record


class WorkerModelError(RuntimeError):
    """One worker model call was refused or did not complete."""


@dataclass(frozen=True, slots=True)
class Limits:
    max_calls: int
    max_request_bytes: int
    max_response_bytes: int
    # Input plus output tokens left for this execution; None means no token cap.
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in ("max_calls", "max_request_bytes", "max_response_bytes"):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_tokens is not None and self.max_tokens < 0:
            raise ValueError("max_tokens must be a nonnegative integer or null")


@dataclass(frozen=True, slots=True)
class WorkerResponse:
    status: int
    headers: dict[str, str]
    body: AsyncIterator[bytes]


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"JSON number is not finite: {value[:100]}")
    return number


class WorkerModelProxy:
    def __init__(
        self,
        settings: ModelSettings,
        provider: str,
        context_window_tokens: int,
        token: str,
        limits: Limits,
        *,
        slots: ModelSlots | None = None,
        scene: str | None = None,
        start_call: Callable[[dict[str, Any]], int],
        finish_call: Callable[[int, dict[str, Any]], None],
    ):
        if not provider.strip() or not token:
            raise ValueError("provider and task token must not be empty")
        if context_window_tokens <= 0:
            raise ValueError("context_window_tokens must be positive")
        self.settings = settings
        self.provider = provider
        self.context_window_tokens = context_window_tokens
        self.limits = limits
        self.slots = slots
        self.scene = scene
        self.start_call = start_call
        self.finish_call = finish_call
        self._token = token
        self._entered = False
        self._closed = False
        self._busy = False
        self._calls = 0
        self._spent = 0
        self._tokens_unknown = False
        self._active_task: asyncio.Task[Any] | None = None
        self._client = http_client(base_url=settings.base_url, api_key=settings.api_key, api=settings.api,
                                   timeout_seconds=settings.timeout_seconds, proxy=settings.proxy)

    @property
    def path(self) -> str:
        return {'openai-chat': '/v1/chat/completions', 'openai-responses': '/v1/responses',
                'anthropic': '/v1/messages', 'gemini': '/v1beta/models/' + quote(self.settings.model.removeprefix('models/'), safe='') + ':streamGenerateContent?alt=sse'}[self.settings.api]

    @property
    def upstream_path(self) -> str:
        return {'openai-chat': 'chat/completions', 'openai-responses': 'responses',
                'anthropic': 'messages', 'gemini': 'models/' + quote(self.settings.model.removeprefix('models/'), safe='') + ':streamGenerateContent?alt=sse'}[self.settings.api]

    def _native_request(self, inbound: dict, payload_bytes: bytes):
        api = self.settings.api
        if api != 'gemini' and inbound.get('model') != self.settings.model:
            raise WorkerModelError('worker model does not match the configured binding')
        if api != 'gemini' and inbound.get('stream') is not True:
            raise WorkerModelError('worker native request must use stream=true')
        allowed = ({'model', 'input', 'instructions', 'tools', 'tool_choice', 'parallel_tool_calls', 'stream', 'store', 'include', 'max_output_tokens', 'temperature', 'reasoning', 'prompt_cache_key', 'service_tier'} if api == 'openai-responses' else
                   {'model', 'messages', 'system', 'tools', 'tool_choice', 'stream', 'max_tokens', 'temperature', 'thinking', 'output_config', 'metadata', 'cache_control'} if api == 'anthropic' else
                   {'contents', 'systemInstruction', 'tools', 'toolConfig', 'generationConfig', 'safetySettings'})
        unknown = inbound.keys() - allowed
        if unknown:
            raise WorkerModelError(f'unsupported worker {api} fields: {sorted(unknown)}')
        key = 'input' if api == 'openai-responses' else 'messages' if api == 'anthropic' else 'contents'
        if not isinstance(inbound.get(key), list):
            raise WorkerModelError(f'worker {key} must be an array')
        outgoing = inbound.copy()
        options = dict(inbound.get('generationConfig', {})) if api == 'gemini' else outgoing
        output_field = 'max_output_tokens' if api == 'openai-responses' else 'max_tokens' if api == 'anthropic' else 'maxOutputTokens'
        output_tokens = options.get(output_field, self.settings.max_output_tokens)
        if type(output_tokens) is not int or not 0 < output_tokens <= self.settings.max_output_tokens:
            raise WorkerModelError('worker output tokens exceed configured binding')
        options[output_field] = output_tokens
        if self.settings.temperature is None:
            options.pop('temperature', None)
        else:
            options['temperature'] = self.settings.temperature
        if api == 'openai-responses':
            if outgoing.get('store') is True:
                raise WorkerModelError('worker store may only be false')
            outgoing.update(store=False, include=['reasoning.encrypted_content'])
            outgoing.pop('reasoning', None)
            if self.settings.reasoning_effort is not None:
                outgoing['reasoning'] = {'effort': self.settings.reasoning_effort}
        elif api == 'anthropic':
            outgoing.pop('thinking', None)
            outgoing.pop('output_config', None)
            if self.settings.thinking_budget_tokens is not None:
                outgoing['thinking'] = {'type': 'enabled', 'budget_tokens': self.settings.thinking_budget_tokens}
            elif self.settings.reasoning_effort is not None:
                outgoing.update(thinking={'type': 'adaptive'}, output_config={'effort': self.settings.reasoning_effort})
        else:
            options.pop('thinkingConfig', None)
            if self.settings.thinking_budget_tokens is not None:
                options['thinkingConfig'] = {'thinkingBudget': self.settings.thinking_budget_tokens}
            elif self.settings.reasoning_effort is not None:
                options['thinkingConfig'] = {'thinkingLevel': self.settings.reasoning_effort}
            outgoing['generationConfig'] = options
        # Count readable JSON, retaining signed continuation in the estimate; image token cost is unknown.
        image_unknown = False
        def readable(value):
            nonlocal image_unknown
            if isinstance(value, dict):
                if value.get('type') in {'input_image', 'image'} or 'inlineData' in value or 'fileData' in value:
                    image_unknown = True
                    return '[image tokens unknown]'
                return {k: readable(v) for k, v in value.items()}
            return [readable(v) for v in value] if isinstance(value, list) else value
        estimated = math.ceil(len(json.dumps(readable(outgoing), ensure_ascii=False).encode()) / 3) + output_tokens
        if estimated > self.context_window_tokens:
            raise WorkerModelError(f'worker native request estimate {estimated} exceeds configured context window {self.context_window_tokens}')
        wire = json.dumps(outgoing, ensure_ascii=False, allow_nan=False).encode()
        if len(wire) > self.limits.max_request_bytes:
            raise WorkerModelError('forwarded worker model request exceeds max_request_bytes')
        return outgoing, wire, estimated, image_unknown

    async def __aenter__(self) -> WorkerModelProxy:
        if self._entered or self._closed:
            raise WorkerModelError("worker model proxy has already been opened or closed")
        self._entered = True
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        self._closed = True
        active = self._active_task
        if active is not None and active is not asyncio.current_task():
            active.cancel()
        await self._client.aclose()

    @asynccontextmanager
    async def _slot(self) -> AsyncIterator[None]:
        if self.slots is None:
            yield
        else:
            async with self.slots.slot(scene=self.scene):
                yield

    def _add_tokens(self, tokens: dict[str, int | None] | None) -> None:
        if tokens is None:
            self._tokens_unknown = True
            return
        self._spent += tokens["input"] + tokens["output"]

    def _request(self, payload_bytes: bytes) -> tuple[dict[str, Any], bytes, int, bool]:
        if len(payload_bytes) > self.limits.max_request_bytes:
            raise WorkerModelError("worker model request exceeds max_request_bytes")
        try:
            inbound = json.loads(payload_bytes.decode("utf-8"), parse_constant=_reject_constant,
                                 parse_float=_finite_float)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise WorkerModelError(
                f"invalid worker Chat Completions JSON: {error}; fragment={payload_bytes[:500]!r}"
            ) from error
        if not isinstance(inbound, dict):
            raise WorkerModelError(
                f"worker Chat Completions request must be an object: {payload_bytes[:500]!r}"
            )
        if self.settings.api != "openai-chat":
            return self._native_request(inbound, payload_bytes)
        allowed = {
            "model", "messages", "tools", "tool_choice", "stream", "stream_options",
            "temperature", "max_completion_tokens", "max_tokens", "reasoning_effort", "store",
        }
        unsupported = sorted(inbound.keys() - allowed)
        if unsupported:
            raise WorkerModelError(f"unsupported worker Chat Completions fields: {unsupported!r}; "
                                   f"fragment={payload_bytes[:500]!r}")
        if inbound.get("model") != self.settings.model:
            raise WorkerModelError("worker model does not match the configured binding")
        if inbound.get("stream") is not True:
            raise WorkerModelError("worker Chat Completions request must use stream=true")
        if "stream_options" in inbound and inbound["stream_options"] != {"include_usage": True}:
            raise WorkerModelError("worker stream_options must request include_usage=true")
        if "store" in inbound and inbound["store"] is not False:
            raise WorkerModelError("worker store may only be false")
        messages = inbound.get("messages")
        tools = inbound.get("tools", [])
        if not isinstance(messages, list) or not all(
            isinstance(message, dict) and isinstance(message.get("role"), str)
            for message in messages
        ):
            raise WorkerModelError(f"worker messages must be native message objects; "
                                   f"fragment={payload_bytes[:500]!r}")
        if not isinstance(tools, list) or not all(isinstance(tool, dict) for tool in tools):
            raise WorkerModelError(
                f"worker tools must be an array of objects; fragment={payload_bytes[:500]!r}"
            )
        if "tool_choice" in inbound and not isinstance(inbound["tool_choice"], (str, dict)):
            raise WorkerModelError(f"worker tool_choice must be a string or object; "
                                   f"fragment={payload_bytes[:500]!r}")
        if "store" in inbound:
            store = False
        else:
            store = None
        other_output_field = 'max_tokens' if self.settings.output_token_field == 'max_completion_tokens' else 'max_completion_tokens'
        if other_output_field in inbound:
            raise WorkerModelError(f'worker output budget must use configured field {self.settings.output_token_field}')
        output_tokens = inbound.get(self.settings.output_token_field, self.settings.max_output_tokens)
        if type(output_tokens) is not int or not 0 < output_tokens <= self.settings.max_output_tokens:
            raise WorkerModelError(
                f"worker max_completion_tokens must be a positive integer <= "
                f"{self.settings.max_output_tokens}; actual={output_tokens!r}")
        outgoing: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            self.settings.output_token_field: output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.settings.temperature is not None:
            outgoing["temperature"] = self.settings.temperature
        if tools or "tools" in inbound:
            outgoing["tools"] = tools
        if "tool_choice" in inbound:
            outgoing["tool_choice"] = inbound["tool_choice"]
        if store is False:
            outgoing["store"] = False
        if self.settings.reasoning_effort is not None:
            outgoing["reasoning_effort"] = self.settings.reasoning_effort
        image_tokens_unknown = any(
            isinstance(message.get("content"), list)
            and any(isinstance(block, dict) and block.get("type") == "image_url"
                    for block in message["content"])
            for message in messages
        )
        try:
            text_estimate = estimate_text_request(
                [{"content": None, **message} for message in messages],
                tools, output_tokens,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkerModelError(f"invalid worker message content: {error}; "
                                   f"fragment={payload_bytes[:500]!r}") from error
        if text_estimate > self.context_window_tokens:
            raise WorkerModelError(
                f"worker text request estimate {text_estimate} exceeds configured "
                f"context window {self.context_window_tokens}; image tokens are unknown"
            )
        try:
            wire = json.dumps(outgoing, ensure_ascii=False, separators=(",", ":"),
                              allow_nan=False).encode("utf-8")
        except (UnicodeEncodeError, ValueError) as error:
            raise WorkerModelError(f"invalid worker request data: {error}; "
                                   f"fragment={payload_bytes[:500]!r}") from error
        if len(wire) > self.limits.max_request_bytes:
            raise WorkerModelError("forwarded worker model request exceeds max_request_bytes")
        return outgoing, wire, text_estimate, image_tokens_unknown

    def authorize(self, token: str) -> None:
        if (not self._entered or self._closed
                or not hmac.compare_digest(token.encode("utf-8"), self._token.encode("utf-8"))):
            raise WorkerModelError("worker model task token is invalid or expired")

    def recorded(self, value: Any) -> Any:
        """Project records, not executed requests, using credentials owned by this proxy."""
        secrets = tuple(sorted((self._token, self.settings.api_key), key=len, reverse=True))
        return redact_record(value, lambda text: redact(text, secrets))

    def _finish_record(self, call_id: int, facts: dict[str, Any]) -> None:
        recorded = {**facts, **{name: self.recorded(facts[name]) for name in ('error', 'error_body', 'usage')}}
        self.finish_call(call_id, recorded)

    @asynccontextmanager
    async def open(self, token: str, payload_bytes: bytes, *, headers: dict | None = None) -> AsyncIterator[WorkerResponse]:
        self.authorize(token)
        headers = {} if headers is None else headers
        if (headers.keys() - {"anthropic-beta"} or any(not isinstance(v, str) for v in headers.values())
                or (headers and self.settings.api != "anthropic")):
            raise WorkerModelError("unsupported worker protocol headers")
        if self._busy:
            raise WorkerModelError("worker model proxy already has an active call")
        if self._calls >= self.limits.max_calls:
            raise WorkerModelError("worker model call limit reached")
        if self.limits.max_tokens is not None and (
            self._tokens_unknown or self._spent >= self.limits.max_tokens
        ):
            raise WorkerModelError("worker model token limit reached, or an earlier call reported no tokens")
        self._busy = True
        self._active_task = asyncio.current_task()
        call_id: int | None = None
        settled = False
        status: int | None = None
        response_bytes = 0
        error_body = bytearray()
        deadline: asyncio.Timeout | None = None
        try:
            outgoing, wire, text_estimate, image_unknown = self._request(payload_bytes)
            async with self._slot():
                if self._closed:
                    raise WorkerModelError("worker model task token has expired")
                call_id = self.start_call({
                    "provider": self.provider,
                    "model": self.settings.model,
                    "request": self.recorded(outgoing),
                    "request_bytes": len(wire),
                    "incoming_bytes": len(payload_bytes),
                    "text_request_estimate_tokens": text_estimate,
                    "image_tokens_unknown": image_unknown,
                })
                self._calls += 1
                deadline = asyncio.timeout(self.settings.timeout_seconds)
                async with deadline:
                    request = self._client.build_request(
                        "POST", self.upstream_path, content=wire,
                        headers={
                            **auth_headers(self.settings.api, self.settings.api_key),
                            **headers,
                            "Content-Type": "application/json",
                            "Accept": "text/event-stream",
                            "Accept-Encoding": "identity",
                        },
                    )
                    upstream = await self._client.send(request, stream=True)
                    try:
                        status = upstream.status_code
                        encoding = upstream.headers.get("content-encoding", "identity")
                        if encoding != "identity":
                            raise WorkerModelError(f"unsupported upstream content-encoding: {encoding}")
                        parser = ((ChatCompletionStream(self.limits.max_response_bytes) if self.settings.api == "openai-chat"
                                   else NativeStream(self.settings, self.limits.max_response_bytes))
                                  if upstream.is_success else None)

                        def settle(*, result: Any = None, error: str | None = None) -> None:
                            nonlocal settled
                            tokens = token_record(None if result is None else result.token_usage)
                            facts = {
                                "http_status": status,
                                "request_bytes": len(wire),
                                "response_bytes": response_bytes,
                                "finish_reason": None if result is None else result.finish_reason,
                                "usage": None if result is None else result.usage,
                                "token_usage": None if result is None or result.token_usage is None
                                else asdict(result.token_usage),
                                "tokens": tokens,
                                "error": error,
                                "error_body": error_body.decode("utf-8", errors="backslashreplace")
                                if error_body else None,
                            }
                            settled = True
                            try:
                                self._finish_record(call_id, facts)
                            except BaseException:
                                self._closed = True
                                raise
                            self._add_tokens(tokens)

                        async def body() -> AsyncIterator[bytes]:
                            nonlocal response_bytes
                            async for chunk in upstream.aiter_raw():
                                response_bytes += len(chunk)
                                if response_bytes > self.limits.max_response_bytes:
                                    raise WorkerModelError("worker model response exceeds max_response_bytes")
                                if parser is not None:
                                    parser.feed(chunk)
                                    if parser.done:
                                        settle(result=parser.finish())
                                        yield chunk
                                        return
                                else:
                                    error_body.extend(chunk)
                                yield chunk
                            if parser is not None:
                                settle(result=parser.finish())
                            else:
                                settle(error=f"upstream model HTTP {status}")

                        stream = body()
                        headers = {
                            "content-type": upstream.headers.get("content-type", "application/octet-stream")
                        }
                        yield WorkerResponse(status, headers, stream)
                        if not settled:
                            await stream.aclose()
                            raise WorkerModelError("worker model response was not fully consumed")
                    finally:
                        await upstream.aclose()
        except BaseException as error:
            original_error = error
            if isinstance(error, TimeoutError) and deadline is not None and deadline.expired():
                error = TimeoutError(
                    f"worker model {self.provider}/{self.settings.model} POST {self.upstream_path} "
                    f"exceeded {self.settings.timeout_seconds:g} seconds; "
                    f"HTTP status={status}, received_bytes={response_bytes}; request did not finish"
                )
            if isinstance(error, asyncio.CancelledError):
                self._closed = True
            if call_id is not None and not settled:
                settled = True
                known_usage = error.usage if isinstance(error, ModelProtocolError) else None
                known_tokens = error.token_usage if isinstance(error, ModelProtocolError) else None
                tokens = token_record(known_tokens)
                facts = {
                    "http_status": status,
                    "request_bytes": len(wire),
                    "response_bytes": response_bytes,
                    "finish_reason": None,
                    "usage": known_usage,
                    "token_usage": None if known_tokens is None else asdict(known_tokens),
                    "tokens": tokens,
                    "error": f"{type(error).__name__}: {error}",
                    "error_body": error_body.decode("utf-8", errors="backslashreplace")
                    if error_body else None,
                }
                try:
                    self._finish_record(call_id, facts)
                except BaseException as finish_error:
                    self._closed = True
                    error.add_note(f"model-call recording also failed: {finish_error}")
                self._add_tokens(tokens)
            if error is not original_error:
                raise error from original_error
            raise
        finally:
            self._active_task = None
            self._busy = False
            if self._closed and not self._client.is_closed:
                await self._client.aclose()
