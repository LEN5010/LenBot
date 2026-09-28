"""Bounded duplex streams over one task's public-network exec pipes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import struct


CHUNK_BYTES = 65536
MAX_METADATA_BYTES = 16384
HEADER = struct.Struct('!cII')


class PipeError(RuntimeError):
    """A network pipe failed or emitted an invalid protocol record."""


async def read_frame(reader: asyncio.StreamReader) -> tuple[bytes, int, bytes]:
    try:
        raw = await reader.readexactly(HEADER.size)
        kind, connection, size = HEADER.unpack(raw)
        limit = CHUNK_BYTES if kind == b'D' else MAX_METADATA_BYTES
        if size > limit:
            raise PipeError(f'egress frame {kind!r}/{connection} exceeds {limit}: {size}')
        return kind, connection, await reader.readexactly(size)
    except asyncio.IncompleteReadError as error:
        raise PipeError(f'egress pipe closed: {error.partial[:500]!r}') from error


async def write_frame(writer: asyncio.StreamWriter, kind: bytes, connection: int,
                      body: bytes = b'') -> None:
    writer.write(HEADER.pack(kind, connection, len(body)) + body)
    await writer.drain()


class Channel:
    """One TCP stream; each direction has at most one unread pipe data frame."""

    def __init__(self, pipe: Pipe, connection: int):
        self.pipe, self.id = pipe, connection
        self._opened = asyncio.Event()
        self._readable = asyncio.Event()
        self._credit = asyncio.Event()
        self._credit.set()
        self._incoming: bytes | None = None
        self._read_eof = False
        self._write_eof = False
        self._accepted = False
        self._announced = pipe.on_open is not None
        self._closed = False
        self._error: str | None = None
        self._handler: asyncio.Task | None = None

    def _check_open(self) -> None:
        if self._closed:
            raise PipeError(self._error or 'egress connection closed')

    @property
    def close_reason(self) -> str | None:
        return self._error

    def _stop(self, error: str | None) -> None:
        self._closed, self._error = True, error
        self._opened.set()
        self._readable.set()
        self._credit.set()

    async def accept(self) -> None:
        self._check_open()
        self._accepted = True
        await self.pipe._send(b'K', self.id)

    async def read(self) -> bytes:
        await self._readable.wait()
        if self._incoming is not None:
            value, self._incoming = self._incoming, None
            if not self._read_eof and not self._closed:
                self._readable.clear()
            if not self._closed:
                await self.pipe._send(b'A', self.id)
            return value
        if self._error is not None:
            raise PipeError(self._error)
        return b''

    async def write(self, data: bytes) -> None:
        if not data or len(data) > CHUNK_BYTES:
            raise ValueError(f'egress write must contain 1..{CHUNK_BYTES} bytes')
        await self._credit.wait()
        self._check_open()
        if not self._accepted or self._write_eof:
            raise PipeError('egress write before acceptance or after EOF')
        self._credit.clear()
        await self.pipe._send(b'D', self.id, data)

    async def write_eof(self) -> None:
        await self._credit.wait()
        self._check_open()
        if self._write_eof:
            raise PipeError('duplicate egress write EOF')
        self._write_eof = True
        await self.pipe._send(b'F', self.id)

    async def close(self) -> None:
        if self._closed:
            return
        self._stop(None)
        self.pipe.channels.pop(self.id)
        if not self.pipe.closed and self._announced:
            await self.pipe._send(b'C', self.id)

    async def abort(self, error: str) -> None:
        if self._closed:
            return
        self._stop(error)
        self.pipe.channels.pop(self.id)
        if not self.pipe.closed and self._announced:
            body = error.encode('utf-8')
            if len(body) > MAX_METADATA_BYTES:
                body = body[:MAX_METADATA_BYTES - 50].decode('utf-8', errors='ignore').encode() + b'\n[pipe error truncated]'
            await self.pipe._send(b'X', self.id, body)


class Pipe:
    """Multiplex only this task's TCP streams, not business or audit events."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *,
                 max_connections: int,
                 on_open: Callable[[Channel, dict], Awaitable[None]] | None):
        self.reader, self.writer = reader, writer
        self.max_connections, self.on_open = max_connections, on_open
        self.channels: dict[int, Channel] = {}
        self.closed = False
        self._last_id = 0
        self._write_lock = asyncio.Lock()
        self._serving: asyncio.Task | None = None
        self._handlers: set[asyncio.Task] = set()
        self._failure: asyncio.Future[BaseException] = asyncio.get_running_loop().create_future()

    def start(self) -> None:
        self._serving = asyncio.create_task(self._serve())

    def _fail(self, error: BaseException) -> None:
        if self.closed:
            return
        self.closed = True
        if not self._failure.done():
            self._failure.set_result(error)
        for channel in self.channels.values():
            channel._stop(f'{type(error).__name__}: {error}')
        self.channels.clear()
        if not self.writer.is_closing():
            self.writer.transport.abort()
        for task in self._handlers:
            if task is not asyncio.current_task():
                task.cancel()

    async def _send(self, kind: bytes, connection: int, body: bytes = b'') -> None:
        try:
            async with self._write_lock:
                if self.closed:
                    raise PipeError('egress pipe is closed')
                if kind == b'O':
                    self.channels[connection]._announced = True
                await write_frame(self.writer, kind, connection, body)
        except Exception as error:
            self._fail(error)
            raise

    async def open(self, metadata: dict) -> Channel:
        if self.on_open is not None:
            raise PipeError('only the container side opens connections')
        if self.closed or len(self.channels) >= self.max_connections:
            raise PipeError('egress pipe closed or connection limit reached')
        body = json.dumps(metadata, ensure_ascii=False, allow_nan=False).encode()
        if len(body) > MAX_METADATA_BYTES:
            raise ValueError('egress connection metadata exceeds the frame limit')
        if self._last_id == 0xffffffff:
            raise PipeError('egress connection counter exhausted')
        self._last_id += 1
        channel = Channel(self, self._last_id)
        self.channels[channel.id] = channel
        try:
            await self._send(b'O', channel.id, body)
            await channel._opened.wait()
            channel._check_open()
            return channel
        except BaseException:
            await channel.close()
            raise

    async def _handle(self, channel: Channel, metadata: dict) -> None:
        try:
            await self.on_open(channel, metadata)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await channel.abort(f'{type(error).__name__}: {error}')
        finally:
            await channel.close()

    def _finished(self, task: asyncio.Task) -> None:
        self._handlers.remove(task)
        if not task.cancelled() and task.exception() is not None:
            self._fail(task.exception())

    async def _serve(self) -> None:
        try:
            while True:
                kind, connection, body = await read_frame(self.reader)
                if not connection or kind not in {b'O', b'K', b'D', b'A', b'F', b'C', b'X'}:
                    raise PipeError(f'invalid egress frame {kind!r}/{connection}: {body[:500]!r}')
                if kind in {b'K', b'A', b'F', b'C'} and body:
                    raise PipeError(f'egress control frame {kind!r} has a body: {body[:500]!r}')
                if kind == b'O':
                    if self.on_open is None or connection <= self._last_id:
                        raise PipeError(f'unexpected egress OPEN {connection}: {body[:500]!r}')
                    self._last_id = connection
                    try:
                        metadata = json.loads(body)
                    except ValueError as error:
                        raise PipeError(f'invalid egress OPEN JSON: {body[:500]!r}; {error}') from error
                    if not isinstance(metadata, dict):
                        raise PipeError(f'egress OPEN must be an object: {body[:500]!r}')
                    if len(self._handlers) >= self.max_connections:
                        await self._send(b'X', connection, b'egress connection limit reached')
                        continue
                    channel = Channel(self, connection)
                    self.channels[connection] = channel
                    task = asyncio.create_task(self._handle(channel, metadata))
                    channel._handler = task
                    self._handlers.add(task)
                    task.add_done_callback(self._finished)
                    continue
                if connection > self._last_id:
                    raise PipeError(f'egress frame for unknown connection {connection}')
                channel = self.channels.get(connection)
                if channel is None:
                    # A peer can close while earlier data or ACKs are already in
                    # the other pipe. IDs are never reused on this transport.
                    continue
                if kind == b'K':
                    if self.on_open is not None or channel._accepted:
                        raise PipeError(f'unexpected egress acceptance {connection}')
                    channel._accepted = True
                    channel._opened.set()
                elif kind == b'D':
                    if not channel._accepted or channel._read_eof or channel._incoming is not None or not body:
                        raise PipeError(f'egress data outside the receive window: {connection}')
                    channel._incoming = body
                    channel._readable.set()
                elif kind == b'A':
                    if channel._credit.is_set():
                        raise PipeError(f'egress ACK without outstanding data: {connection}')
                    channel._credit.set()
                elif kind == b'F':
                    if not channel._accepted or channel._read_eof:
                        raise PipeError(f'unexpected egress EOF: {connection}')
                    channel._read_eof = True
                    channel._readable.set()
                else:
                    error = body.decode('utf-8') if kind == b'X' else None
                    channel._stop(error)
                    self.channels.pop(connection)
                    if channel._handler is not None:
                        channel._handler.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._fail(error)

    async def wait_failure(self) -> None:
        raise await asyncio.shield(self._failure)

    async def close(self) -> None:
        self.closed = True
        for channel in self.channels.values():
            channel._stop('egress pipe stopped')
        self.channels.clear()
        if not self.writer.is_closing():
            self.writer.transport.abort()
        tasks = list(self._handlers)
        if self._serving is not None:
            tasks.append(self._serving)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.writer.wait_closed()
