"""Host public TCP egress for one task's network-none container pipe."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Callable
import json
import os
from pathlib import Path
import socket
import time
from typing import Mapping

from .egress_policy import EgressBlocked, blocked_address_reason, blocked_resolved_reason

from .egress_wire import CHUNK_BYTES, Channel, Pipe, read_frame


def _is_literal_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class EgressTransportError(RuntimeError):
    """One task egress pipe or its upstream connection failed."""


class EgressTransport:
    def __init__(self, process: asyncio.subprocess.Process, *, stderr_file, port: int,
                 connect_timeout_seconds: float,
                 fake_ip_networks: tuple,
                 bytes_per_second: int,
                 before_bytes: Callable[[int, str, int], None],
                 on_connection: Callable[[dict], None],
                 on_bytes: Callable[[int, str, int], None]):
        self.process = process
        self.fake_ip_networks = fake_ip_networks
        self.stderr_file = stderr_file
        self.port = port
        self.connect_timeout_seconds = connect_timeout_seconds
        self.bytes_per_second = bytes_per_second
        self.before_bytes = before_bytes
        self.on_connection = on_connection
        self.on_bytes = on_bytes
        self.pipe: Pipe | None = None
        self._closing = False
        self._failure: asyncio.Future[BaseException] = asyncio.get_running_loop().create_future()
        self._watching: asyncio.Task | None = None
        self._watching_pipe: asyncio.Task | None = None

    @classmethod
    async def spawn(cls, argv: list[str], *, cwd: Path, env: Mapping[str, str],
                    stderr_path: Path, max_connections: int,
                    connect_timeout_seconds: float,
                    fake_ip_networks: tuple,
                    bytes_per_second: int,
                    before_bytes: Callable[[int, str, int], None],
                    on_connection: Callable[[dict], None],
                    on_bytes: Callable[[int, str, int], None]) -> EgressTransport:
        stderr_file = os.fdopen(os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                                       0o600), "ab")
        try:
            process = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, env=dict(env), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=stderr_file,
            )
        except BaseException:
            stderr_file.close()
            raise
        transport = cls(process, stderr_file=stderr_file, port=0,
                        connect_timeout_seconds=connect_timeout_seconds,
                        fake_ip_networks=fake_ip_networks,
                        bytes_per_second=bytes_per_second, before_bytes=before_bytes,
                        on_connection=on_connection, on_bytes=on_bytes)
        try:
            async with asyncio.timeout(connect_timeout_seconds):
                kind, connection_id, body = await read_frame(process.stdout)
                try:
                    ready = json.loads(body)
                except ValueError as error:
                    raise EgressTransportError(f'invalid egress ready JSON: {body[:500]!r}; {error}') from error
                if (kind != b"R" or connection_id != 0 or not isinstance(ready, dict)
                        or set(ready) != {"port"} or type(ready["port"]) is not int
                        or not 1 <= ready["port"] <= 65535):
                    raise EgressTransportError(
                        f"invalid worker egress ready frame {kind!r}/{connection_id}: {body[:500]!r}")
                transport.port = ready["port"]
            transport.pipe = Pipe(process.stdout, process.stdin,
                                  max_connections=max_connections, on_open=transport._on_open)
            transport.pipe.start()
            transport._watching_pipe = asyncio.create_task(transport._watch_pipe())
            transport._watching = asyncio.create_task(transport._watch_process())
            return transport
        except BaseException as error:
            try:
                await transport.close()
            except BaseException as cleanup_error:
                error.add_note(f"Worker egress cleanup also failed: {cleanup_error}")
            try:
                with stderr_path.open("rb") as diagnostic:
                    diagnostic.seek(0, 2)
                    diagnostic.seek(max(0, diagnostic.tell() - 4000))
                    tail = diagnostic.read().decode("utf-8", errors="replace")
                if tail:
                    error.add_note(f"Worker egress stderr (last 4000 bytes): {tail}")
            except OSError as diagnostic_error:
                error.add_note(f"Reading worker egress stderr also failed: {diagnostic_error}")
            raise

    def _fail(self, error: BaseException) -> None:
        if not self._closing and not self._failure.done():
            self._failure.set_result(error)

    async def _watch_process(self) -> None:
        status = await self.process.wait()
        if not self._closing:
            self._fail(EgressTransportError(f"worker egress process exited with status {status}"))

    async def _watch_pipe(self) -> None:
        assert self.pipe is not None
        try:
            await self.pipe.wait_failure()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._fail(error)

    @staticmethod
    def _target(metadata: dict) -> tuple[str, int, str]:
        if (set(metadata) != {"host", "port", "method"}
                or not isinstance(metadata["host"], str) or not metadata["host"]
                or metadata["host"] != metadata["host"].strip()
                or any(character in metadata["host"] for character in "/?#@\x00")
                or type(metadata["port"]) is not int or not 1 <= metadata["port"] <= 65535
                or not isinstance(metadata["method"], str)
                or metadata["method"] not in {"CONNECT", "HTTP"}):
            raise EgressTransportError(f"invalid worker egress OPEN metadata: {metadata!r}")
        return metadata["host"], metadata["port"], metadata["method"]

    async def _dial_public(self, host: str, port: int) -> tuple[str, asyncio.StreamReader,
                                                                   asyncio.StreamWriter]:
        loop = asyncio.get_running_loop()
        async with asyncio.timeout(self.connect_timeout_seconds):
            answers = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if not answers:
                raise EgressTransportError(f"public egress target {host}:{port} has no DNS answers")
            literal = _is_literal_address(host)
            for entry in answers:
                ip = entry[4][0]
                reason = (blocked_address_reason(ip) if literal
                          else blocked_resolved_reason(ip, self.fake_ip_networks))
                if reason is not None:
                    raise EgressTransportError(
                        f"public egress target {host}:{port} resolved to {ip} ({reason})")
            family, kind, protocol, _, address = answers[0]
            ip = address[0]
            sock = socket.socket(family, kind, protocol)
            sock.setblocking(False)
            try:
                await loop.sock_connect(sock, address)
                reader, writer = await asyncio.open_connection(sock=sock)
            except BaseException:
                sock.close()
                raise
        return ip, reader, writer

    def _permit(self, channel_id: int, side: str, amount: int) -> None:
        try:
            self.before_bytes(channel_id, side, amount)
        except EgressBlocked:
            raise
        except Exception as error:
            self._fail(error)
            raise

    def _count(self, channel_id: int, side: str, amount: int, facts: dict) -> None:
        facts[side] += amount
        try:
            self.on_bytes(channel_id, side, amount)
        except Exception as error:
            self._fail(error)
            raise

    async def _forward(self, channel: Channel, origin_reader: asyncio.StreamReader,
                       origin_writer: asyncio.StreamWriter, facts: dict) -> None:
        next_chunk_at = 0.0

        async def pace() -> None:
            await asyncio.sleep(max(0, next_chunk_at - asyncio.get_running_loop().time()))

        def wrote(side: str, amount: int) -> None:
            nonlocal next_chunk_at
            now = asyncio.get_running_loop().time()
            next_chunk_at = max(now, next_chunk_at) + amount / self.bytes_per_second
            self._count(channel.id, side, amount, facts)

        async def upstream() -> None:
            while data := await channel.read():
                await pace()
                self._permit(channel.id, "up", len(data))
                origin_writer.write(data)
                wrote("up", len(data))
                await origin_writer.drain()
            if origin_writer.can_write_eof():
                origin_writer.write_eof()
                await origin_writer.drain()

        async def downstream() -> None:
            while data := await origin_reader.read(CHUNK_BYTES):
                await pace()
                await channel.write(
                    data,
                    before_write=lambda: self._permit(channel.id, "down", len(data)),
                    on_write=lambda: wrote("down", len(data)),
                )
            await channel.write_eof()

        tasks = (asyncio.create_task(upstream()), asyncio.create_task(downstream()))
        try:
            # A normal half-close leaves the other direction running. An error
            # propagates its original text and cancels the peer pump below.
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _on_open(self, channel: Channel, metadata: dict) -> None:
        origin_writer: asyncio.StreamWriter | None = None
        facts: dict | None = None
        record_started = False
        abnormal = False
        try:
            host, port, method = self._target(metadata)
            facts = {"phase": "start", "connection_id": channel.id, "host": host,
                     "port": port, "ip": None, "method": method, "up": 0, "down": 0,
                     "started": time.time(), "ended": None, "error": None}
            try:
                self.on_connection(dict(facts))
                record_started = True
            except EgressBlocked:
                raise
            except Exception as error:
                self._fail(error)
                raise
            ip, origin_reader, origin_writer = await self._dial_public(host, port)
            facts["ip"] = ip
            facts["phase"] = "connected"
            try:
                self.on_connection(dict(facts))
            except Exception as error:
                self._fail(error)
                raise
            await channel.accept()
            await self._forward(channel, origin_reader, origin_writer, facts)
        except asyncio.CancelledError:
            abnormal = True
            if facts is not None:
                facts["error"] = channel.close_reason
            raise
        except Exception as error:
            abnormal = True
            if facts is not None:
                facts["error"] = f"{type(error).__name__}: {error}"
            await channel.abort(f"{type(error).__name__}: {error}")
        finally:
            if origin_writer is not None:
                if abnormal:
                    origin_writer.transport.abort()
                else:
                    origin_writer.close()
            if record_started:
                facts["phase"] = "end"
                facts["ended"] = time.time()
                try:
                    self.on_connection(dict(facts))
                except Exception as error:
                    self._fail(error)
                    raise
            await channel.close()
            if origin_writer is not None and not abnormal:
                await origin_writer.wait_closed()

    async def wait_failure(self) -> None:
        raise await asyncio.shield(self._failure)

    def raise_if_failed(self) -> None:
        if self._failure.done():
            raise self._failure.result()

    async def close(self) -> None:
        self._closing = True
        try:
            if self.pipe is not None:
                await self.pipe.close()
        finally:
            if self.process.stdin is not None:
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                if self.process.returncode is None:
                    try:
                        self.process.kill()
                    except ProcessLookupError:
                        pass
                await self.process.wait()
            finally:
                if self._watching_pipe is not None:
                    self._watching_pipe.cancel()
                    await asyncio.gather(self._watching_pipe, return_exceptions=True)
                if self._watching is not None:
                    await self._watching
                self.stderr_file.close()
