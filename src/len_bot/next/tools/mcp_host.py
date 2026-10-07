"""Configured MCP connections and their actual low-frequency tools, owned by this host."""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from importlib.metadata import version
import logging
from pathlib import Path
import re
import time
from typing import Literal

import anyio
from jsonschema.validators import validator_for
from mcp import Client, StdioServerParameters, stdio_client, types
from mcp.shared.exceptions import MCPError
from mcp.shared.message import SessionMessage
from referencing import Registry

from .external_tools import ExternalTool
from ..configuration.mcp import MCPService, StdioTransport
from .mcp_http import SingleHTTP
from ..storage.store import encode
from ..runtime.logs import REDACTED, log_event, recorded_errors

LOG = logging.getLogger(__name__)


class MCPToolError(RuntimeError):
    pass


def error_text(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup):
        return "; ".join(error_text(item) for item in error.exceptions)
    return f"{type(error).__name__}: {error}"


def text_result(result: types.CallToolResult, max_bytes: int) -> str:
    """Keep real text/structured output and links; binary blocks are not viewed or uploaded."""
    blocks, unsupported = [], []
    for block in result.content:
        if isinstance(block, types.TextContent | types.ResourceLink):
            blocks.append(block.model_dump(mode="json", by_alias=True, exclude_none=True))
        elif isinstance(block, types.EmbeddedResource) and isinstance(block.resource, types.TextResourceContents):
            blocks.append(block.model_dump(mode="json", by_alias=True, exclude_none=True))
        else:
            unsupported.append(block.type)
    output = encode({"content": blocks, "structuredContent": result.structured_content,
                     "isError": result.is_error, "unprocessed": unsupported})
    if len(output.encode("utf-8")) > max_bytes:
        raise MCPToolError(f"MCP tool executed but its textual result exceeds {max_bytes} bytes; "
                           f"not replayed; result fragment: {output[:500]}")
    if result.is_error:
        raise MCPToolError(output)
    if unsupported:
        raise MCPToolError("MCP tool executed, but this host does not process its binary content; "
                           "nothing was viewed, played or uploaded, and the call was not repeated: " + output)
    return output


@dataclass
class Connection:
    name: str
    settings: MCPService
    status: Literal["disabled", "connecting", "running", "failed", "stopped"] = "disabled"
    error: str | None = None
    errors: deque = field(default_factory=lambda: deque(maxlen=20))
    tools: list[ExternalTool] = field(default_factory=list)
    protocol: str | None = None
    server: dict | None = None
    client: Client | None = None
    task: asyncio.Task | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    operation: asyncio.Lock = field(default_factory=asyncio.Lock)


class MCPHost:
    def __init__(self, settings: dict[str, MCPService], *, reserved_tools: set[str], log_directory: Path | None = None):
        self.connections = {name: Connection(name, item) for name, item in settings.items()}
        previous = {} if log_directory is None else recorded_errors(log_directory, 'mcp_error', 'service')
        for name, connection in self.connections.items():
            connection.errors.extend(previous.get(name, []))
        self.reserved = reserved_tools
        self.on_update: Callable[[], None] | None = None

    def notify(self) -> None:
        if self.on_update is not None:
            self.on_update()

    def error(self, connection: Connection, where: str, error: BaseException) -> str:
        text = error_text(error)
        transport = connection.settings.transport
        secrets = transport.env.values() if isinstance(transport, StdioTransport) else transport.headers.values()
        for value in secrets:
            if value:
                text = text.replace(value, REDACTED)
        connection.errors.append({"at": time.time(), "where": where, "error": text})
        log_event(LOG, 'mcp_error', f'MCP {connection.name} {where} 出错', level=logging.ERROR,
                  error=error, service=connection.name, where=where)
        return text

    def failed(self, connection: Connection, error: Exception) -> None:
        connection.error = self.error(connection, "connection", error)
        connection.status = "failed"
        connection.tools = []
        connection.stop.set()
        self.notify()

    @asynccontextmanager
    async def stdio(self, connection: Connection, transport: StdioTransport):
        params = StdioServerParameters(command=transport.command, args=transport.args,
                                       cwd=transport.cwd, env=transport.env)
        async with stdio_client(params) as (source, write):
            send, read = anyio.create_memory_object_stream[SessionMessage | Exception](32)
            async def copy() -> None:
                try:
                    async for message in source:
                        if isinstance(message, Exception):
                            raise message
                        await send.send(message)
                    if not connection.stop.is_set():
                        self.failed(connection, ConnectionError("MCP stdio stream ended; not restarted"))
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.failed(connection, error)
                finally:
                    await send.aclose()
            async with send, read:
                reader = asyncio.create_task(copy())
                try:
                    yield read, write
                finally:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)

    async def catalog(self, connection: Connection, client: Client) -> list[ExternalTool]:
        cursor, seen, tools, names = None, set(), [], set()
        while True:
            page = await client.list_tools(cursor=cursor)
            for item in page.tools:
                name = f"mcp__{connection.name}__{item.name}"
                if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) is None:
                    raise ValueError(f"MCP tool name cannot be exposed to the model: {name!r}")
                if name in self.reserved or name in names:
                    raise ValueError(f"MCP tool name conflicts or repeats: {name!r}")
                names.add(name)
                if item.input_schema.get("type") != "object":
                    raise ValueError(f"MCP tool {name!r} inputSchema must explicitly describe an object: {item.input_schema}")
                validator_class = validator_for(item.input_schema)
                validator_class.check_schema(item.input_schema)
                validator = validator_class(item.input_schema, registry=Registry())
                tools.append(ExternalTool(name=name, description=item.description or item.name,
                    parameters=item.input_schema, source=f"MCP {connection.name} / {item.name}",
                    call=self.tool_call(connection, item.name, validator)))
            if page.next_cursor is None:
                return tools
            if page.next_cursor in seen:
                raise ValueError(f"MCP tools/list repeated cursor: {page.next_cursor!r}")
            seen.add(page.next_cursor)
            cursor = page.next_cursor

    async def worker(self, connection: Connection) -> None:
        connection.status = "connecting"
        self.notify()
        try:
            settings = connection.settings.transport
            transport = (self.stdio(connection, settings) if isinstance(settings, StdioTransport)
                         else SingleHTTP(connection.settings, settings, lambda error: self.failed(connection, error)).connect())
            # One handshake, no probe/fallback, no cached results and no automatic input-required rounds.
            async with Client(transport, mode="legacy", cache=None,
                              client_info=types.Implementation(name="LenBot", version=version("len-bot")),
                              read_timeout_seconds=connection.settings.timeout_seconds) as client:
                tools = await self.catalog(connection, client)
                if connection.stop.is_set():
                    return
                connection.client = client
                connection.tools = tools
                connection.protocol = client.protocol_version
                connection.server = (None if client.server_info is None else
                                     client.server_info.model_dump(mode="json", by_alias=True))
                connection.status = "running"
                connection.ready.set()
                self.notify()
                await connection.stop.wait()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.failed(connection, error)
        finally:
            connection.client = None
            connection.tools = []
            if connection.status != "failed":
                connection.status = "stopped"
            connection.ready.set()
            self.notify()

    async def connect(self, name: str) -> dict:
        connection = self.connections[name]
        async with connection.operation:
            if not connection.settings.enabled:
                raise ValueError("This MCP service is disabled in the running root configuration")
            await self.stop_connection(connection)
            connection.ready = asyncio.Event()
            connection.stop = asyncio.Event()
            connection.error = None
            connection.protocol = None
            connection.server = None
            connection.task = asyncio.create_task(self.worker(connection), name=f"mcp:{name}")
            try:
                await asyncio.wait_for(connection.ready.wait(), timeout=connection.settings.timeout_seconds)
            except TimeoutError as error:
                connection.error = self.error(connection, "startup", error)
                connection.status = "failed"
                await self.stop_connection(connection)
                self.notify()
            return self.state_one(connection)

    async def stop_connection(self, connection: Connection) -> None:
        connection.stop.set()
        if connection.task is not None:
            connection.task.cancel()
            results = await asyncio.gather(connection.task, return_exceptions=True)
            if isinstance(results[0], BaseException) and not isinstance(results[0], asyncio.CancelledError):
                connection.error = self.error(connection, "close", results[0])
                connection.status = "failed"
            connection.task = None

    async def disconnect(self, name: str) -> dict:
        connection = self.connections[name]
        async with connection.operation:
            await self.stop_connection(connection)
            return self.state_one(connection)

    async def start(self) -> None:
        await asyncio.gather(*(self.connect(name) for name, item in self.connections.items() if item.settings.enabled))

    async def close(self) -> None:
        await asyncio.gather(*(self.disconnect(name) for name in self.connections))

    def tools_for(self, scene: str) -> list[ExternalTool]:
        return [tool for item in self.connections.values()
                if item.status == "running" and scene in item.settings.scenes for tool in item.tools]

    def tool_call(self, connection: Connection, name: str, validator):
        async def call(scene: str, arguments: dict) -> str:
            if scene not in connection.settings.scenes:
                raise PermissionError(f"MCP {connection.name} is not enabled in {scene}")
            try:
                if connection.status != "running":
                    raise RuntimeError(f"MCP {connection.name} is {connection.status}: {connection.error}")
                validator.validate(arguments)
                # The low-level call returns once; it does not drive SDK callback/retry rounds.
                result = await connection.client.session.call_tool(name, arguments,
                    read_timeout_seconds=connection.settings.timeout_seconds)
                return text_result(result, connection.settings.max_response_bytes)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                text = self.error(connection, f"tool {name}", error)
                if connection.error is not None:
                    text = connection.error + "; " + text
                if isinstance(error, MCPError) and error.error.code == types.CONNECTION_CLOSED:
                    if connection.status != "failed":
                        self.failed(connection, error)
                self.notify()
                raise MCPToolError(text) from error
        return call

    def state_one(self, item: Connection) -> dict:
        return {"name": item.name, "status": item.status, "error": item.error,
                "scenes": item.settings.scenes, "protocol": item.protocol, "server": item.server,
                "transport": item.settings.transport.type,
                "tools": [{"name": tool.name, "description": tool.description, "parameters": tool.parameters}
                          for tool in item.tools], "errors": list(reversed(item.errors))}

    def state(self) -> list[dict]:
        return [self.state_one(item) for item in self.connections.values()]
