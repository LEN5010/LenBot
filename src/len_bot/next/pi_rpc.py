"""Pi RPC subprocess boundary for one independent work task.

This module transports Pi's native JSONL records. It does not decide whether
the task succeeded, persist a task, or start a sandbox on its own.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping


DEFAULT_MAX_FRAME_BYTES = 64 * 1024 * 1024

_EVENT_FIELDS: dict[str, dict[str, type]] = {
    "agent_start": {},
    "agent_end": {"messages": list, "willRetry": bool},
    "agent_settled": {},
    "turn_start": {},
    "turn_end": {"message": dict, "toolResults": list},
    "message_start": {"message": dict},
    "message_update": {"usage": dict, "assistantMessageEvent": dict},
    "message_end": {"message": dict},
    "tool_execution_start": {"toolCallId": str, "toolName": str, "args": dict},
    "tool_execution_update": {"toolCallId": str, "toolName": str, "args": dict},
    "tool_execution_end": {"toolCallId": str, "toolName": str, "isError": bool},
    "queue_update": {"steering": list, "followUp": list},
    "entry_appended": {"entry": dict},
    "session_info_changed": {},
    "thinking_level_changed": {"level": str},
    "compaction_start": {"reason": str},
    "compaction_end": {"reason": str, "aborted": bool, "willRetry": bool},
    "auto_retry_start": {"attempt": int, "maxAttempts": int, "delayMs": int,
                         "errorMessage": str},
    "auto_retry_end": {"success": bool, "attempt": int},
    "summarization_retry_scheduled": {"attempt": int, "maxAttempts": int,
                                       "delayMs": int, "errorMessage": str},
    "summarization_retry_attempt_start": {"source": str},
    "summarization_retry_finished": {},
    "bash_execution_update": {"delta": str},
    "extension_error": {"extensionPath": str, "event": str, "error": str},
}
_UI_DIALOGS = frozenset({"select", "confirm", "input", "editor"})
_UI_NOTICES = frozenset({"notify", "setStatus", "setWidget", "setTitle", "set_editor_text"})


@dataclass(frozen=True)
class PiRecord:
    """One full native stdout record and its original LF-delimited frame."""

    body: dict[str, Any]
    raw: bytes

    @property
    def type(self) -> str:
        return self.body["type"]


class PiRpcError(RuntimeError):
    def __init__(self, message: str, *, raw: bytes | None = None):
        super().__init__(message)
        self.raw = raw


class PiProtocolError(PiRpcError):
    """Pi emitted a malformed or unsupported record."""


class PiProcessError(PiRpcError):
    """The RPC process exited or its pipes failed before the task ended."""


class PiCommandError(PiRpcError):
    """Pi returned a failed response for a submitted command."""


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


def _fragment(raw: bytes) -> str:
    return raw[:500].decode("utf-8", errors="replace")


def _parse_record(raw: bytes) -> PiRecord:
    line = raw[:-1] if raw.endswith(b"\r") else raw
    try:
        body = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError) as error:
        raise PiProtocolError(f"Invalid Pi RPC JSON: {error}; frame: {_fragment(raw)}", raw=raw) from error
    if not isinstance(body, dict) or not isinstance(body.get("type"), str):
        raise PiProtocolError(f"Pi RPC record needs an object and type; frame: {_fragment(raw)}", raw=raw)
    kind = body["type"]
    if kind == "response":
        if (not isinstance(body.get("command"), str) or type(body.get("success")) is not bool
                or ("id" in body and not isinstance(body["id"], str))):
            raise PiProtocolError(f"Invalid Pi RPC response; frame: {_fragment(raw)}", raw=raw)
        if body["success"] is False and not isinstance(body.get("error"), str):
            raise PiProtocolError(f"Pi RPC failed response lacks error; frame: {_fragment(raw)}", raw=raw)
    elif kind == "extension_ui_request":
        method = body.get("method")
        if (not isinstance(body.get("id"), str) or not body["id"]
                or not isinstance(method, str) or method not in _UI_DIALOGS | _UI_NOTICES):
            raise PiProtocolError(f"Invalid Pi RPC extension UI request; frame: {_fragment(raw)}", raw=raw)
    elif kind in _EVENT_FIELDS:
        for field, expected in _EVENT_FIELDS[kind].items():
            if type(body.get(field)) is not expected:
                raise PiProtocolError(
                    f"Invalid Pi RPC {kind}.{field}; frame: {_fragment(raw)}", raw=raw
                )
    else:
        raise PiProtocolError(f"Unknown Pi RPC record type {kind!r}; frame: {_fragment(raw)}", raw=raw)
    return PiRecord(body=body, raw=raw)


class PiRpc:
    """One subprocess, one stdout reader, and native events for one task.

    Consumers must read ``next_event`` concurrently with running commands.
    The bounded event queue then applies backpressure rather than buffering an
    unbounded task timeline in the host process.
    """

    def __init__(self, process: asyncio.subprocess.Process, *, stderr_file,
                 max_frame_bytes: int):
        self.process = process
        self.stderr_file = stderr_file
        self.max_frame_bytes = max_frame_bytes
        self._events: asyncio.Queue[PiRecord | PiRpcError] = asyncio.Queue(maxsize=256)
        self._pending: dict[str, tuple[str, asyncio.Future[PiRecord]]] = {}
        self._write_lock = asyncio.Lock()
        self._next_id = 0
        self._failure: PiRpcError | None = None
        self._closing = False
        self._reader_task = asyncio.create_task(self._read_stdout())

    @classmethod
    async def spawn(cls, argv: list[str], *, cwd: Path, env: Mapping[str, str],
                    stderr_path: Path,
                    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> PiRpc:
        """Start the already-chosen Pi or container command; never choose a model here."""
        if max_frame_bytes <= 0:
            raise ValueError("max_frame_bytes must be positive")
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
        return cls(process, stderr_file=stderr_file, max_frame_bytes=max_frame_bytes)

    def _fail(self, error: PiRpcError) -> None:
        if self._failure is not None:
            return
        self._failure = error
        for _, future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        if not self._events.full():
            self._events.put_nowait(error)

    async def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        buffer = bytearray()
        try:
            while chunk := await self.process.stdout.read(65536):
                buffer.extend(chunk)
                while (end := buffer.find(b"\n")) >= 0:
                    raw = bytes(buffer[:end])
                    del buffer[:end + 1]
                    if len(raw) > self.max_frame_bytes:
                        raise PiProtocolError(
                            f"Pi RPC frame exceeds {self.max_frame_bytes} bytes; frame: {_fragment(raw)}",
                            raw=raw,
                        )
                    record = _parse_record(raw)
                    if record.type == "response":
                        self._handle_response(record)
                    else:
                        await self._events.put(record)
                if len(buffer) > self.max_frame_bytes:
                    raise PiProtocolError(
                        f"Pi RPC frame exceeds {self.max_frame_bytes} bytes; frame: {_fragment(buffer)}",
                        raw=bytes(buffer),
                    )
            if buffer:
                raise PiProtocolError(f"Pi RPC ended with an unterminated frame: {_fragment(buffer)}",
                                      raw=bytes(buffer))
            code = await self.process.wait()
            if not self._closing:
                raise PiProcessError(f"Pi RPC process exited unexpectedly with status {code}")
        except PiRpcError as error:
            self._fail(error)
        except OSError as error:
            self._fail(PiProcessError(f"Pi RPC stdout failed: {error}"))
        except Exception as error:
            self._fail(PiProcessError(f"Pi RPC reader failed: {type(error).__name__}: {error}"))

    def _handle_response(self, record: PiRecord) -> None:
        body = record.body
        request_id = body.get("id")
        pending = self._pending.get(request_id)
        if pending is None:
            raise PiProtocolError(f"Unmatched Pi RPC response; frame: {_fragment(record.raw)}",
                                  raw=record.raw)
        command, future = pending
        if body["command"] != command:
            raise PiProtocolError(f"Pi RPC response command mismatch; frame: {_fragment(record.raw)}",
                                  raw=record.raw)
        self._pending.pop(request_id)
        if not future.done():
            if body["success"]:
                future.set_result(record)
            else:
                future.set_exception(PiCommandError(body["error"], raw=record.raw))

    async def _write(self, body: dict[str, Any]) -> None:
        if self._failure is not None:
            raise self._failure
        if self._closing:
            raise PiProcessError("Pi RPC process is closing")
        wire = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        async with self._write_lock:
            try:
                assert self.process.stdin is not None
                self.process.stdin.write(wire)
                await self.process.stdin.drain()
            except (BrokenPipeError, ConnectionError, OSError, RuntimeError) as error:
                failure = PiProcessError(f"Pi RPC stdin failed: {error}")
                self._fail(failure)
                raise failure from error

    async def command(self, command: str, **fields: Any) -> PiRecord:
        """Send one command and return its complete successful native response."""
        self._next_id += 1
        request_id = f"lenbot-{self._next_id}"
        future: asyncio.Future[PiRecord] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (command, future)
        try:
            await self._write({**fields, "id": request_id, "type": command})
        except asyncio.CancelledError:
            # Once writing starts, the child may receive this command even if
            # its caller stops waiting. Keep its ID until the response arrives.
            future.cancel()
            raise
        except BaseException:
            self._pending.pop(request_id, None)
            if future.done() and not future.cancelled():
                future.exception()
            raise
        return await future

    async def prompt(self, message: str) -> Literal["started", "queued", "handled"]:
        """Accept a prompt; this does not wait for or judge the run's result."""
        record = await self.command("prompt", message=message)
        data = record.body.get("data")
        disposition = data.get("disposition") if isinstance(data, dict) else None
        if disposition not in {"started", "queued", "handled"}:
            error = PiProtocolError(
                f"Invalid Pi RPC prompt disposition; frame: {_fragment(record.raw)}", raw=record.raw
            )
            self._fail(error)
            raise error
        return disposition

    async def next_event(self) -> PiRecord:
        """Read the next native session event or extension UI request."""
        if self._events.empty() and self._failure is not None:
            raise self._failure
        item = await self._events.get()
        if isinstance(item, PiRpcError):
            raise item
        return item

    async def respond_ui(self, request_id: str, *, value: str | None = None,
                         confirmed: bool | None = None, cancelled: bool = False) -> None:
        """Reply to one dialog request; notify/status records need no response."""
        if (int(value is not None) + int(confirmed is not None) + int(cancelled)) != 1:
            raise ValueError("exactly one UI value, confirmation or cancellation is required")
        body: dict[str, Any] = {"type": "extension_ui_response", "id": request_id}
        if cancelled:
            body["cancelled"] = True
        elif confirmed is not None:
            body["confirmed"] = confirmed
        else:
            body["value"] = value
        await self._write(body)

    async def close(self) -> None:
        """Request orderly shutdown, then stop a child that does not exit."""
        if self._closing:
            return
        self._closing = True
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            try:
                await asyncio.wait_for(self._reader_task, timeout=5)
            except TimeoutError:
                self._reader_task.cancel()
                await asyncio.gather(self._reader_task, return_exceptions=True)
        finally:
            self.stderr_file.close()
            self._fail(PiProcessError("Pi RPC process closed"))
