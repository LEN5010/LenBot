"""Host side of one container loopback model endpoint's exec pipes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import os
from pathlib import Path
from typing import Mapping

from .worker_bridge import CHUNK_BYTES, HEADER, MAX_METADATA_BYTES
from .worker_model import WorkerModelProxy


class WorkerTransportError(RuntimeError):
    """The task's model endpoint stopped or emitted invalid pipe records."""


class WorkerTransport:
    def __init__(self, process: asyncio.subprocess.Process, *, proxy: WorkerModelProxy,
                 stderr_file, port: int,
                 task_request: Callable[[str, bytes], Awaitable[dict]] | None):
        self.process = process
        self.proxy = proxy
        self.stderr_file = stderr_file
        self.port = port
        self.task_request = task_request
        self._closing = False
        self._failure: asyncio.Future[BaseException] = asyncio.get_running_loop().create_future()
        self._serving: asyncio.Task | None = None
        self._watching: asyncio.Task | None = None

    @classmethod
    async def spawn(cls, argv: list[str], *, cwd: Path, env: Mapping[str, str],
                    stderr_path: Path, proxy: WorkerModelProxy,
                    startup_timeout_seconds: float,
                    task_request: Callable[[str, bytes], Awaitable[dict]] | None = None) -> WorkerTransport:
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
        transport = cls(process, proxy=proxy, stderr_file=stderr_file, port=0,
                        task_request=task_request)
        try:
            async with asyncio.timeout(startup_timeout_seconds):
                kind, data = await transport._read_frame(MAX_METADATA_BYTES)
                ready = json.loads(data)
                if (kind != b"R" or not isinstance(ready, dict)
                        or set(ready) != {"port"} or type(ready["port"]) is not int
                        or not 1 <= ready["port"] <= 65535):
                    raise WorkerTransportError(f"invalid worker bridge ready frame {kind!r}: {data[:500]!r}")
                transport.port = ready["port"]
            transport._serving = asyncio.create_task(transport._serve())
            transport._watching = asyncio.create_task(transport._watch_process())
            return transport
        except BaseException as error:
            try:
                await transport.close()
            except BaseException as cleanup_error:
                error.add_note(f"Worker bridge cleanup also failed: {cleanup_error}")
            try:
                with stderr_path.open("rb") as diagnostic:
                    diagnostic.seek(0, 2)
                    diagnostic.seek(max(0, diagnostic.tell() - 4000))
                    tail = diagnostic.read().decode("utf-8", errors="replace")
                if tail:
                    error.add_note(f"Worker bridge stderr (last 4000 bytes): {tail}")
            except OSError as diagnostic_error:
                error.add_note(f"Reading worker bridge stderr also failed: {diagnostic_error}")
            raise

    async def _read_frame(self, limit: int) -> tuple[bytes, bytes]:
        assert self.process.stdout is not None
        try:
            header = await self.process.stdout.readexactly(HEADER.size)
            kind, length = HEADER.unpack(header)
            if length > limit:
                raise WorkerTransportError(f"worker bridge frame {kind!r} exceeds {limit}: {length}")
            return kind, await self.process.stdout.readexactly(length)
        except asyncio.IncompleteReadError as error:
            raise WorkerTransportError(f"worker bridge output closed: {error.partial[:500]!r}") from error

    async def _write_frame(self, kind: bytes, body: bytes = b"") -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(HEADER.pack(kind, len(body)))
        self.process.stdin.write(body)
        await self.process.stdin.drain()

    def _fail(self, error: BaseException) -> None:
        if not self._closing and not self._failure.done():
            self._failure.set_result(error)

    async def _watch_process(self) -> None:
        status = await self.process.wait()
        if not self._closing:
            self._fail(WorkerTransportError(f"worker bridge process exited with status {status}"))
            assert self._serving is not None
            self._serving.cancel()

    async def _serve(self) -> None:
        try:
            while True:
                kind, data = await self._read_frame(MAX_METADATA_BYTES)
                request = json.loads(data)
                if (kind != b"Q" or not isinstance(request, dict)
                        or set(request) != {"token", "path", "body_bytes"}
                        or not isinstance(request["token"], str)
                        or not isinstance(request["path"], str)
                        or request["path"] not in {"/v1/chat/completions", "/task/deliver-file", "/task/network",
                                                   "/task/recall-chat", "/task/memory", "/task/transcribe", "/task/account-browser", "/task/mcp"}
                        or type(request["body_bytes"]) is not int
                        or not 0 < request["body_bytes"] <= self.proxy.limits.max_request_bytes):
                    raise WorkerTransportError(f"invalid worker request frame {kind!r}: {data[:500]!r}")
                kind, body = await self._read_frame(self.proxy.limits.max_request_bytes)
                if kind != b"B" or len(body) != request["body_bytes"]:
                    raise WorkerTransportError(f"invalid worker body frame {kind!r}: {body[:500]!r}")
                try:
                    if request["path"] in {"/task/deliver-file", "/task/network",
                                          "/task/recall-chat", "/task/memory", "/task/transcribe", "/task/account-browser", "/task/mcp"}:
                        self.proxy.authorize(request["token"])
                        if self.task_request is None:
                            raise WorkerTransportError("Task API is not configured")
                        result = await self.task_request(request["path"], body)
                        content = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
                        await self._write_frame(b"H", json.dumps({
                            "status": 200, "headers": {"content-type": "application/json"},
                        }).encode())
                        for offset in range(0, len(content), CHUNK_BYTES):
                            await self._write_frame(b"D", content[offset:offset + CHUNK_BYTES])
                        await self._write_frame(b"E")
                        continue
                    async with self.proxy.open(request["token"], body) as response:
                        headers = {name.lower(): value for name, value in response.headers.items()
                                   if name.lower() == "content-type"}
                        await self._write_frame(b"H", json.dumps({
                            "status": response.status, "headers": headers,
                        }).encode())
                        async for chunk in response.body:
                            for offset in range(0, len(chunk), CHUNK_BYTES):
                                await self._write_frame(b"D", chunk[offset:offset + CHUNK_BYTES])
                    await self._write_frame(b"E")
                except Exception as error:
                    # Pi loses the cause when a started model stream is cut off.
                    # Its owner already waits on this failure channel.
                    if request["path"] == "/v1/chat/completions":
                        self._fail(error)
                    detail = f"{type(error).__name__}: {error}".encode("utf-8")
                    if len(detail) > CHUNK_BYTES:
                        detail = detail[:CHUNK_BYTES - 64].decode(
                            "utf-8", errors="ignore").encode("utf-8") + b"\n[error text truncated at pipe frame limit]"
                    await self._write_frame(b"X", detail)
                    if request["path"] == "/v1/chat/completions":
                        return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            assert self.process.stdin is not None
            self.process.stdin.close()
            self._fail(error)

    async def wait_failure(self) -> None:
        """Task owner waits alongside Pi; model bridge exit ends the task."""
        raise await asyncio.shield(self._failure)

    def raise_if_failed(self) -> None:
        """Read an already reported failure before accepting Pi's final result."""
        if self._failure.done():
            raise self._failure.result()

    async def close(self) -> None:
        self._closing = True
        assert self.process.stdin is not None
        self.process.stdin.close()
        if self._serving is not None:
            self._serving.cancel()
            await asyncio.gather(self._serving, return_exceptions=True)
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
            if self._watching is not None:
                await self._watching
            self.stderr_file.close()
