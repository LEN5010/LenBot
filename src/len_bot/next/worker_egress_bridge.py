"""Container-side loopback HTTP proxy over one task's binary egress pipe.

Run this copied file directly beside egress_wire.py. Only the host chooses its
argv; the process neither reads task settings nor opens a public socket.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
from urllib.parse import urlsplit

from egress_wire import CHUNK_BYTES, MAX_METADATA_BYTES, Channel, Pipe, write_frame


_TOKEN = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_HEX = re.compile(rb"[0-9A-Fa-f]+\Z")
_REASONS = {400: 'Bad Request', 408: 'Request Timeout', 431: 'Request Header Fields Too Large',
            502: 'Bad Gateway', 503: 'Service Unavailable'}


class RequestError(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


def _authority(raw: bytes, *, require_port: bool) -> tuple[str, int]:
    if not raw or any(byte <= 32 or byte >= 127 for byte in raw) or b'\\' in raw:
        raise RequestError(400, 'invalid request authority')
    try:
        value = urlsplit('//' + raw.decode('ascii'))
        host, port = value.hostname, value.port
    except ValueError as error:
        raise RequestError(400, f'invalid request authority: {error}') from error
    if (not host or value.username is not None or value.password is not None
            or value.path or value.query or value.fragment or b'?' in raw or b'#' in raw
            or (require_port and port is None)):
        raise RequestError(400, 'request authority needs a host and explicit port without credentials or path')
    if port is None:
        port = 80
    if not 1 <= port <= 65535:
        raise RequestError(400, 'request authority port must be 1..65535')
    return host, port


def _request(raw: bytes) -> tuple[str, str, int, bytes | None, tuple[str, int]]:
    lines = raw[:-4].split(b'\r\n')
    first = lines.pop(0).split(b' ')
    if (len(first) != 3 or not _TOKEN.fullmatch(first[0])
            or first[2] not in {b'HTTP/1.0', b'HTTP/1.1'}):
        raise RequestError(400, 'expected an HTTP/1.0 or HTTP/1.1 request line')
    method, target, version = first
    headers: list[tuple[bytes, bytes, bytes]] = []
    for line in lines:
        name, separator, value = line.partition(b':')
        if (not separator or not _TOKEN.fullmatch(name)
                or any(byte < 32 and byte != 9 or byte == 127 for byte in value)):
            raise RequestError(400, f'invalid HTTP header: {line[:200]!r}')
        headers.append((name.lower(), name, value.strip(b' \t')))
    connections: set[bytes] = set()
    for lower, _, value in headers:
        if lower in {b'connection', b'proxy-connection'}:
            for token in value.split(b','):
                token = token.strip(b' \t').lower()
                if not _TOKEN.fullmatch(token):
                    raise RequestError(400, f'invalid Connection token: {value[:200]!r}')
                connections.add(token)
    lengths = [value for lower, _, value in headers if lower == b'content-length']
    transfers = [value for lower, _, value in headers if lower == b'transfer-encoding']
    if len(lengths) > 1 or (lengths and transfers):
        raise RequestError(400, 'conflicting Content-Length / Transfer-Encoding framing')
    if lengths:
        if not lengths[0].isascii() or not lengths[0].isdigit():
            raise RequestError(400, f'invalid Content-Length: {lengths[0][:200]!r}')
        framing = ('length', int(lengths[0]))
    elif transfers:
        codings = [part.strip(b' \t').lower() for value in transfers for part in value.split(b',')]
        if (not codings or codings[-1] != b'chunked' or codings.count(b'chunked') != 1
                or any(not _TOKEN.fullmatch(part) for part in codings)):
            raise RequestError(400, 'Transfer-Encoding must end in one chunked coding')
        framing = ('chunked', 0)
    else:
        framing = ('none', 0)
    if ((lengths and b'content-length' in connections)
            or (transfers and b'transfer-encoding' in connections)):
        raise RequestError(400, 'Connection may not remove request-body framing')

    if method == b'CONNECT':
        if framing != ('none', 0):
            raise RequestError(400, 'CONNECT does not carry an HTTP request body')
        host, port = _authority(target, require_port=True)
        return 'CONNECT', host, port, None, framing

    if (any(byte <= 32 or byte >= 127 for byte in target) or b'#' in target
            or target[:7].lower() != b'http://'):
        raise RequestError(400, 'ordinary proxy requests need an absolute http:// URL')
    parsed = urlsplit(target.decode('ascii'))
    host, port = _authority(parsed.netloc.encode('ascii'), require_port=False)
    suffix = target[7 + len(parsed.netloc):]
    origin = b'/' + suffix if not suffix or suffix.startswith(b'?') else suffix
    if not origin.startswith(b'/'):
        raise RequestError(400, 'invalid absolute HTTP path')
    if any(lower == b'upgrade' for lower, _, _ in headers):
        raise RequestError(400, 'HTTP Upgrade is not supported by this proxy')
    removed = {b'host', b'connection', b'proxy-connection', b'proxy-authorization',
               b'proxy-authenticate', b'keep-alive', b'te', b'upgrade', *connections}
    if framing[0] != 'chunked':
        removed.add(b'trailer')
    elif b'trailer' in connections and any(lower == b'trailer' for lower, _, _ in headers):
        raise RequestError(400, 'Connection may not remove chunked request trailers')
    forwarded = [method + b' ' + origin + b' ' + version,
                 b'Host: ' + parsed.netloc.encode('ascii')]
    forwarded.extend(name + b': ' + value for lower, name, value in headers if lower not in removed)
    forwarded.extend([b'Connection: close', b'', b''])
    return 'HTTP', host, port, b'\r\n'.join(forwarded), framing


async def _send_error(writer: asyncio.StreamWriter, status: int, detail: str) -> None:
    body = detail.encode('utf-8', errors='replace')
    writer.write((f'HTTP/1.1 {status} {_REASONS[status]}\r\nContent-Type: text/plain; charset=utf-8\r\n'
                  f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode('ascii') + body)
    await writer.drain()


async def _write(channel: Channel, body: bytes) -> None:
    for start in range(0, len(body), CHUNK_BYTES):
        await channel.write(body[start:start + CHUNK_BYTES])


async def _line(reader: asyncio.StreamReader) -> bytes:
    try:
        line = await reader.readuntil(b'\r\n')
    except (asyncio.LimitOverrunError, asyncio.IncompleteReadError) as error:
        raise RequestError(400, 'chunked request line or trailer is incomplete or too large') from error
    if len(line) > MAX_METADATA_BYTES:
        raise RequestError(400, 'chunked request line or trailer is too large')
    return line


async def _exact(reader: asyncio.StreamReader, size: int) -> bytes:
    try:
        return await reader.readexactly(size)
    except asyncio.IncompleteReadError as error:
        raise RequestError(400, 'request body ended before declared framing') from error


async def _upload(reader: asyncio.StreamReader, channel: Channel,
                  framing: tuple[str, int]) -> None:
    kind, remaining = framing
    if kind == 'stream':
        while data := await reader.read(CHUNK_BYTES):
            await channel.write(data)
    elif kind == 'length':
        while remaining:
            data = await _exact(reader, min(remaining, CHUNK_BYTES))
            await channel.write(data)
            remaining -= len(data)
    elif kind == 'chunked':
        while True:
            line = await _line(reader)
            size_text = line[:-2].split(b';', 1)[0]
            if not _HEX.fullmatch(size_text):
                raise RequestError(400, f'invalid chunk size: {line[:200]!r}')
            size = int(size_text, 16)
            await _write(channel, line)
            if size == 0:
                while True:
                    trailer = await _line(reader)
                    await _write(channel, trailer)
                    if trailer == b'\r\n':
                        break
                break
            while size:
                data = await _exact(reader, min(size, CHUNK_BYTES))
                await channel.write(data)
                size -= len(data)
            terminator = await _exact(reader, 2)
            if terminator != b'\r\n':
                raise RequestError(400, 'chunk body lacks CRLF terminator')
            await channel.write(terminator)
    await channel.write_eof()


async def _exchange(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                    channel: Channel, framing: tuple[str, int], response_started: list[bool]) -> None:
    async def download() -> None:
        while data := await channel.read():
            response_started[0] = True
            writer.write(data)
            await writer.drain()
        if framing[0] == 'stream':
            writer.write_eof()
            await writer.drain()

    upload = asyncio.create_task(_upload(reader, channel, framing))
    receive = asyncio.create_task(download())
    try:
        if framing[0] == 'stream':
            await asyncio.gather(upload, receive)
            return
        done, _ = await asyncio.wait({upload, receive}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if not receive.done():
            await receive
    finally:
        for task in (upload, receive):
            if not task.done():
                task.cancel()
        await asyncio.gather(upload, receive, return_exceptions=True)


async def _client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, pipe: Pipe,
                  header_timeout: float) -> None:
    channel: Channel | None = None
    response_started = [False]
    completed = False
    try:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), header_timeout)
            if len(raw) > MAX_METADATA_BYTES:
                raise RequestError(431, 'proxy request headers exceed the limit')
        except TimeoutError as error:
            raise RequestError(408, 'proxy request headers timed out') from error
        except asyncio.LimitOverrunError as error:
            raise RequestError(431, 'proxy request headers exceed the limit') from error
        except asyncio.IncompleteReadError as error:
            if not error.partial:
                return
            raise RequestError(400, 'proxy request headers ended before CRLF CRLF') from error
        method, host, port, head, framing = _request(raw)
        channel = await pipe.open({'host': host, 'port': port, 'method': method})
        if method == 'CONNECT':
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            await writer.drain()
            response_started[0] = True
            framing = ('stream', 0)
        else:
            assert head is not None
            await _write(channel, head)
        await _exchange(reader, writer, channel, framing, response_started)
        completed = True
    except RequestError as error:
        if not response_started[0]:
            await _send_error(writer, error.status, str(error))
        if channel is not None:
            await channel.abort(f'{type(error).__name__}: {error}')
    except Exception as error:
        if channel is not None:
            await channel.abort(f'{type(error).__name__}: {error}')
        if not response_started[0]:
            await _send_error(writer, 502, f'{type(error).__name__}: {error}')
    finally:
        try:
            if channel is not None:
                await channel.close()
        finally:
            if completed:
                writer.close()
            else:
                writer.transport.abort()
            await writer.wait_closed()


async def run(port: int, max_connections: int, header_timeout: float) -> None:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    input_transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    output_reader = asyncio.StreamReader()
    output_transport, output_protocol = await loop.connect_write_pipe(
        lambda: asyncio.StreamReaderProtocol(output_reader), sys.stdout.buffer)
    writer = asyncio.StreamWriter(output_transport, output_protocol, output_reader, loop)
    pipe = Pipe(reader, writer, max_connections=max_connections, on_open=None)
    clients: set[asyncio.Task] = set()

    async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        clients.add(task)
        if len(clients) > max_connections:
            try:
                await _send_error(client_writer, 503, 'proxy connection limit reached')
            finally:
                client_writer.transport.abort()
                await client_writer.wait_closed()
                clients.discard(task)
            return
        try:
            await _client(client_reader, client_writer, pipe, header_timeout)
        finally:
            clients.discard(task)

    server: asyncio.AbstractServer | None = None
    try:
        server = await asyncio.start_server(handle, '127.0.0.1', port,
                                            limit=MAX_METADATA_BYTES)
        async with server:
            actual_port = server.sockets[0].getsockname()[1]
            await write_frame(writer, b'R', 0, json.dumps({'port': actual_port}).encode())
            pipe.start()
            await pipe.wait_failure()
    finally:
        if server is not None:
            server.close()
            await server.wait_closed()
        for task in tuple(clients):
            task.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
        await pipe.close()
        input_transport.close()


def main() -> None:
    parser = argparse.ArgumentParser(description='one-task loopback HTTP egress proxy')
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--max-connections', required=True, type=int)
    parser.add_argument('--header-timeout-seconds', required=True, type=float)
    args = parser.parse_args()
    if not (0 <= args.port <= 65535 and args.max_connections > 0
            and math.isfinite(args.header_timeout_seconds) and args.header_timeout_seconds > 0):
        parser.error('invalid egress port, connection limit or header timeout')
    asyncio.run(run(args.port, args.max_connections, args.header_timeout_seconds))


if __name__ == '__main__':
    main()
