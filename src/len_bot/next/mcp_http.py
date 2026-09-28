"""Single-attempt MCP Streamable HTTP, without background GET, reconnection or request replay."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager

import anyio
import httpx2
from mcp import types
from mcp.shared.message import ClientMessageMetadata, SessionMessage
from pydantic import ValidationError

from .mcp_config import HttpTransport, MCPService


class MCPTransportError(RuntimeError):
    pass


class SingleHTTP:
    def __init__(self, settings: MCPService, connection: HttpTransport, failed: Callable[[Exception], None]):
        self.settings = settings
        self.connection = connection
        self.failed = failed
        self.session_id: str | None = None
        self.protocol: str | None = None
        self.posts: dict[int | str, asyncio.Task] = {}
        self.tasks: set[asyncio.Task] = set()
        self.client: httpx2.AsyncClient | None = None

    def headers(self, metadata: ClientMessageMetadata | None = None) -> dict[str, str]:
        headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
        if self.session_id is not None:
            headers["mcp-session-id"] = self.session_id
        if self.protocol is not None:
            headers["mcp-protocol-version"] = self.protocol
        if metadata is not None and metadata.headers is not None:
            headers.update(metadata.headers)
            self.protocol = headers.get("MCP-Protocol-Version", headers.get("mcp-protocol-version", self.protocol))
        return headers

    async def body(self, response: httpx2.Response) -> bytes:
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self.settings.max_response_bytes:
                raise MCPTransportError(f"MCP HTTP response exceeds {self.settings.max_response_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    def parse(self, raw: str | bytes):
        try:
            return types.jsonrpc_message_adapter.validate_json(raw)
        except ValidationError as error:
            raise MCPTransportError(f"Invalid MCP JSON-RPC: {error}; response fragment={raw[:500]!r}") from error

    async def post(self, outgoing: SessionMessage, received) -> None:
        message = outgoing.message
        request = isinstance(message, types.JSONRPCRequest)
        try:
            async with asyncio.timeout(self.settings.timeout_seconds):
                metadata = outgoing.metadata
                headers = self.headers(metadata)
                async with self.client.stream("POST", self.connection.url, headers=headers,
                        content=message.model_dump_json(by_alias=True, exclude_none=True)) as response:
                    if not 200 <= response.status_code < 300:
                        raw = await self.body(response)
                        raise MCPTransportError(f"MCP HTTP {response.status_code}: {raw[:500]!r}; request not replayed")
                    if not request:
                        if response.status_code not in {202, 204}:
                            raise MCPTransportError(f"MCP notification/response POST expected 202/204, got {response.status_code}")
                        return
                    session = response.headers.get("mcp-session-id")
                    if session is not None:
                        if message.method != "initialize" and session != self.session_id:
                            raise MCPTransportError("MCP response changed its session ID outside initialize")
                        self.session_id = session
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                    if content_type == "application/json":
                        reply = self.parse(await self.body(response))
                        if not isinstance(reply, types.JSONRPCResponse | types.JSONRPCError) or reply.id != message.id:
                            raise MCPTransportError(f"MCP JSON response does not answer request {message.id!r}: {reply}")
                        await received.send(SessionMessage(reply))
                    elif content_type == "text/event-stream":
                        size = 0
                        async for event in httpx2.EventSource(response, max_event_size=self.settings.max_response_bytes):
                            if event.event != "message":
                                raise MCPTransportError(f"Unsupported MCP SSE event {event.event!r}: {event.data[:500]!r}")
                            # MCP may prime an SSE stream with an empty event carrying only an ID.
                            if not event.data:
                                continue
                            size += len(event.data.encode("utf-8"))
                            if size > self.settings.max_response_bytes:
                                raise MCPTransportError(f"MCP SSE response exceeds {self.settings.max_response_bytes} bytes")
                            reply = self.parse(event.data)
                            if isinstance(reply, types.JSONRPCResponse | types.JSONRPCError) and reply.id != message.id:
                                raise MCPTransportError(f"MCP SSE answered a different request: {reply.id!r}")
                            await received.send(SessionMessage(reply))
                            if isinstance(reply, types.JSONRPCResponse | types.JSONRPCError):
                                return
                        raise MCPTransportError("MCP SSE ended without a final response; not resumed or replayed")
                    else:
                        raise MCPTransportError(f"Unsupported MCP response content type {content_type!r}")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.failed(error)
            if request:
                await received.send(SessionMessage(types.JSONRPCError(jsonrpc="2.0", id=message.id,
                    error=types.ErrorData(code=types.CONNECTION_CLOSED, message=f"{type(error).__name__}: {error}"))))
        finally:
            if request:
                self.posts.pop(message.id, None)

    async def writer(self, sent, received) -> None:
        async for outgoing in sent:
            message = outgoing.message
            if isinstance(message, types.JSONRPCNotification) and message.method == "notifications/cancelled":
                request_id = message.params["requestId"]
                pending = self.posts.get(request_id)
                if pending is not None:
                    pending.cancel()
            if not isinstance(message, types.JSONRPCRequest):
                # initialized must reach the server before tools/list; cancellation is not a new tool attempt.
                await self.post(outgoing, received)
                continue
            task = asyncio.create_task(self.post(outgoing, received))
            self.tasks.add(task)
            def finished(task: asyncio.Task) -> None:
                self.tasks.discard(task)
                if not task.cancelled() and (error := task.exception()) is not None:
                    self.failed(error)
            task.add_done_callback(finished)
            if isinstance(message, types.JSONRPCRequest):
                self.posts[message.id] = task

    @asynccontextmanager
    async def connect(self):
        async with httpx2.AsyncClient(headers=self.connection.headers, trust_env=False,
                                      follow_redirects=False, timeout=self.settings.timeout_seconds) as client:
            self.client = client
            incoming, read = anyio.create_memory_object_stream[SessionMessage | Exception](32)
            write, outgoing = anyio.create_memory_object_stream[SessionMessage](32)
            async with incoming, read, write, outgoing:
                writer = asyncio.create_task(self.writer(outgoing, incoming))
                try:
                    yield read, write
                finally:
                    writer.cancel()
                    await asyncio.gather(writer, return_exceptions=True)
                    for task in self.tasks:
                        task.cancel()
                    await asyncio.gather(*self.tasks, return_exceptions=True)
                    if self.session_id is not None:
                        try:
                            response = await client.delete(self.connection.url, headers=self.headers())
                            if response.status_code not in {200, 204, 405}:
                                raise MCPTransportError(f"MCP session DELETE returned {response.status_code}")
                        except Exception as error:
                            self.failed(error)
            self.client = None
