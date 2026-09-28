"""One-task, byte-transparent Chat Completions stream boundary.

The caller owns the task record and the HTTP route. This object owns only one
configured upstream binding, its task token, and actual model-call limits.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
import hmac
import json
import math
from typing import Any

import httpx

from .context import estimate_text_request
from .model import ModelProtocolError, ModelSettings
from .model_slots import ModelSlots
from .pricing import ModelPrice, estimate_cost
from .worker_stream import ChatCompletionStream


class WorkerModelError(RuntimeError):
    """One worker model call was refused or did not complete."""


@dataclass(frozen=True, slots=True)
class Limits:
    max_calls: int
    max_request_bytes: int
    max_response_bytes: int
    max_cost: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("max_calls", "max_request_bytes", "max_response_bytes"):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_cost is not None and (
            not self.max_cost.is_finite()
            or self.max_cost < 0
        ):
            raise ValueError("max_cost must be a finite nonnegative Decimal or null")


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
        price: ModelPrice | None,
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
        if limits.max_cost is not None and price is None:
            raise ValueError("max_cost requires a configured model price")
        self.settings = settings
        self.provider = provider
        self.context_window_tokens = context_window_tokens
        self.price = price
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
        self._spent = Decimal(0)
        self._cost_unknown = False
        self._active_task: asyncio.Task[Any] | None = None
        self._client = httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/") + "/",
            timeout=settings.timeout_seconds,
            trust_env=False,
            follow_redirects=False,
            transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
        )

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

    def _add_cost(self, cost: dict[str, str] | None) -> None:
        if cost is None:
            self._cost_unknown = True
            return
        amount = Decimal(cost["amount"])
        with localcontext() as context:
            context.prec = max(self._spent.adjusted(), amount.adjusted(), 0) - min(
                self._spent.as_tuple().exponent, amount.as_tuple().exponent
            ) + 2
            self._spent += amount

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
        allowed = {
            "model", "messages", "tools", "tool_choice", "stream", "stream_options",
            "temperature", "max_completion_tokens", "reasoning_effort", "store",
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
        outgoing: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature,
            "max_completion_tokens": self.settings.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
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
                tools, self.settings.max_output_tokens,
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

    @asynccontextmanager
    async def open(self, token: str, payload_bytes: bytes) -> AsyncIterator[WorkerResponse]:
        self.authorize(token)
        if self._busy:
            raise WorkerModelError("worker model proxy already has an active call")
        if self._calls >= self.limits.max_calls:
            raise WorkerModelError("worker model call limit reached")
        if self.limits.max_cost is not None and (
            self._cost_unknown or self._spent >= self.limits.max_cost
        ):
            raise WorkerModelError("worker model cost limit reached or prior cost is unknown")
        self._busy = True
        self._active_task = asyncio.current_task()
        call_id: int | None = None
        settled = False
        status: int | None = None
        response_bytes = 0
        error_body = bytearray()
        try:
            outgoing, wire, text_estimate, image_unknown = self._request(payload_bytes)
            async with self._slot():
                if self._closed:
                    raise WorkerModelError("worker model task token has expired")
                call_id = self.start_call({
                    "provider": self.provider,
                    "model": self.settings.model,
                    "price": None if self.price is None else self.price.model_dump(mode="json"),
                    "request": outgoing,
                    "request_bytes": len(wire),
                    "incoming_bytes": len(payload_bytes),
                    "text_request_estimate_tokens": text_estimate,
                    "image_tokens_unknown": image_unknown,
                })
                self._calls += 1
                async with asyncio.timeout(self.settings.timeout_seconds):
                    request = self._client.build_request(
                        "POST", "chat/completions", content=wire,
                        headers={
                            "Authorization": f"Bearer {self.settings.api_key}",
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
                        parser = (ChatCompletionStream(self.limits.max_response_bytes)
                                  if upstream.is_success else None)

                        def settle(*, result: Any = None, error: str | None = None) -> None:
                            nonlocal settled
                            cost = estimate_cost(self.price, None if result is None else result.token_usage)
                            facts = {
                                "http_status": status,
                                "request_bytes": len(wire),
                                "response_bytes": response_bytes,
                                "finish_reason": None if result is None else result.finish_reason,
                                "usage": None if result is None else result.usage,
                                "token_usage": None if result is None or result.token_usage is None
                                else asdict(result.token_usage),
                                "cost": cost,
                                "error": error,
                                "error_body": error_body.decode("utf-8", errors="backslashreplace")
                                if error_body else None,
                            }
                            settled = True
                            try:
                                self.finish_call(call_id, facts)
                            except BaseException:
                                self._closed = True
                                raise
                            self._add_cost(cost)

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
            if isinstance(error, asyncio.CancelledError):
                self._closed = True
            if call_id is not None and not settled:
                settled = True
                known_usage = error.usage if isinstance(error, ModelProtocolError) else None
                known_tokens = error.token_usage if isinstance(error, ModelProtocolError) else None
                cost = estimate_cost(self.price, known_tokens)
                facts = {
                    "http_status": status,
                    "request_bytes": len(wire),
                    "response_bytes": response_bytes,
                    "finish_reason": None,
                    "usage": known_usage,
                    "token_usage": None if known_tokens is None else asdict(known_tokens),
                    "cost": cost,
                    "error": f"{type(error).__name__}: {error}",
                    "error_body": error_body.decode("utf-8", errors="backslashreplace")
                    if error_body else None,
                }
                try:
                    self.finish_call(call_id, facts)
                except BaseException as finish_error:
                    self._closed = True
                    error.add_note(f"model-call recording also failed: {finish_error}")
                self._add_cost(cost)
            raise
        finally:
            self._active_task = None
            self._busy = False
            if self._closed and not self._client.is_closed:
                await self._client.aclose()
