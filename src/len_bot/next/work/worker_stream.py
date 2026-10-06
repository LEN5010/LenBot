"""Incremental Chat Completions SSE observation for a byte-transparent proxy.

Pi consumes the forwarded stream itself. This parser retains only the current
SSE event, final usage and the argument fragments needed to verify native tool
calls; it never reconstructs assistant text or changes outbound bytes.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
from typing import Any, NoReturn

from ..models.client import ModelProtocolError, parse_token_usage
from ..models.tokens import TokenUsage


@dataclass(frozen=True, slots=True)
class StreamResult:
    usage: dict[str, Any] | None
    token_usage: TokenUsage | None
    finish_reason: str


@dataclass(slots=True)
class _ToolArguments:
    id: str | None = None
    type: str | None = None
    name: str | None = None
    fragments: list[str] = field(default_factory=list)


def _reject_non_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard JSON constant in tool arguments: {value}")


class ChatCompletionStream:
    """Read one Chat Completions SSE body, with completion confirmed by DONE."""

    def __init__(self, max_event_bytes: int):
        if max_event_bytes <= 0:
            raise ValueError("max_event_bytes must be positive")
        self.max_event_bytes = max_event_bytes
        self._line = bytearray()
        self._event_lines: list[bytes] = []
        self._event_bytes = 0
        self._usage: dict[str, Any] | None = None
        self._token_usage: TokenUsage | None = None
        self._finish_reason: str | None = None
        self._tools: dict[int, _ToolArguments] = {}
        self._done = False

    @property
    def done(self) -> bool:
        return self._done

    def _fail(self, message: str, raw: bytes, *, usage: dict | None = None) -> NoReturn:
        raise ModelProtocolError(
            f"Invalid Chat Completions SSE: {message}; frame={raw[:500]!r}",
            response=raw.decode("utf-8", errors="backslashreplace"),
            usage=self._usage if usage is None else usage,
            token_usage=self._token_usage,
        )

    def _frame_so_far(self) -> bytes:
        return b"\n".join([*self._event_lines, bytes(self._line)])

    def feed(self, chunk: bytes) -> None:
        """Consume arbitrary HTTP body bytes; no event or UTF-8 alignment needed."""
        start = 0
        while start < len(chunk):
            newline = chunk.find(b"\n", start)
            if newline < 0:
                self._line.extend(chunk[start:])
                if self._event_bytes + len(self._line) > self.max_event_bytes:
                    self._fail("SSE event exceeds max_event_bytes", self._frame_so_far())
                return
            self._line.extend(chunk[start:newline])
            if self._event_bytes + len(self._line) + 1 > self.max_event_bytes:
                self._fail("SSE event exceeds max_event_bytes", self._frame_so_far())
            line = bytes(self._line)
            self._line.clear()
            self._event_bytes += len(line) + 1
            if line not in {b"", b"\r"}:
                self._event_lines.append(line)
            else:
                raw = b"\n".join(self._event_lines)
                self._event_lines.clear()
                self._event_bytes = 0
                self._event(raw)
            start = newline + 1

    def _event(self, raw: bytes) -> None:
        if not raw:
            return
        try:
            lines = [line.removesuffix("\r") for line in raw.decode("utf-8").split("\n")]
        except UnicodeDecodeError as error:
            self._fail(f"invalid UTF-8: {error}", raw)
        data_lines: list[str] = []
        for line in lines:
            if line.startswith(":"):
                continue
            field_name, separator, value = line.partition(":")
            if not separator:
                value = ""
            elif value.startswith(" "):
                value = value[1:]
            if field_name == "data":
                data_lines.append(value)
        if not data_lines:
            return  # SSE comments and metadata are not chat chunks.
        if self._done:
            self._fail("data arrived after [DONE]", raw)
        payload = "\n".join(data_lines)
        if payload == "[DONE]":
            self._validate_done(raw)
            self._done = True
            return
        try:
            value = json.loads(payload, parse_constant=_reject_non_json_constant)
        except (json.JSONDecodeError, ValueError) as error:
            self._fail(f"invalid chunk JSON: {error}", raw)
        self._chunk(value, raw)

    def _chunk(self, chunk: object, raw: bytes) -> None:
        if not isinstance(chunk, dict):
            self._fail("chunk must be an object", raw)
        usage = chunk.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                self._fail("usage must be an object or null", raw)
            try:
                token_usage = parse_token_usage(usage)
            except ValueError as error:
                self._fail(f"invalid usage: {error}", raw, usage=usage)
            self._usage = usage
            self._token_usage = token_usage

        choices = chunk.get("choices")
        if not isinstance(choices, list):
            self._fail("choices must be an array", raw)
        if not choices:
            if usage is None:
                self._fail("empty choices without usage", raw)
            return

        selected: dict | None = None
        for choice in choices:
            if not isinstance(choice, dict) or type(choice.get("index")) is not int:
                self._fail("choice must have an integer index", raw)
            if choice["index"] == 0:
                if selected is not None:
                    self._fail("duplicate choice index 0 in one chunk", raw)
                selected = choice
        if selected is None:
            return  # Only choice 0 belongs to the proxied default completion.
        if self._finish_reason is not None:
            self._fail("choice 0 continued after finish_reason", raw)

        delta = selected.get("delta")
        if not isinstance(delta, dict):
            self._fail("choice 0 delta must be an object", raw)
        if "function_call" in delta and delta["function_call"] is not None:
            self._fail("legacy function_call delta is not supported", raw)
        native_calls = delta.get("tool_calls")
        if native_calls is not None:
            if not isinstance(native_calls, list):
                self._fail("delta.tool_calls must be an array or null", raw)
            for native in native_calls:
                self._tool_delta(native, raw)

        reason = selected.get("finish_reason")
        if reason is not None:
            if not isinstance(reason, str):
                self._fail("finish_reason must be a string or null", raw)
            self._finish_reason = reason

    def _tool_delta(self, native: object, raw: bytes) -> None:
        if not isinstance(native, dict):
            self._fail("tool call delta must be an object", raw)
        index = native.get("index")
        if type(index) is not int or index < 0:
            self._fail("tool call delta needs a nonnegative integer index", raw)
        tool = self._tools.setdefault(index, _ToolArguments())
        for key in ("id", "type"):
            value = native.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                self._fail(f"tool call {key} must be a nonempty string", raw)
            previous = getattr(tool, key)
            if previous is not None and previous != value:
                self._fail(f"tool call {key} changed during streaming", raw)
            setattr(tool, key, value)
        function = native.get("function")
        if function is None:
            return
        if not isinstance(function, dict):
            self._fail("tool call function must be an object", raw)
        name = function.get("name")
        if name is not None:
            if not isinstance(name, str) or not name.strip():
                self._fail("tool name must be a nonempty string", raw)
            if tool.name is not None and tool.name != name:
                self._fail("tool name changed during streaming", raw)
            tool.name = name
        arguments = function.get("arguments")
        if arguments is not None:
            if not isinstance(arguments, str):
                self._fail("tool arguments delta must be a string", raw)
            tool.fragments.append(arguments)

    def _validate_done(self, raw: bytes) -> None:
        reason = self._finish_reason
        if reason is None:
            self._fail("[DONE] before choice 0 finish_reason", raw)
        if reason not in {"stop", "tool_calls"}:
            self._fail(f"incomplete or unsupported finish_reason: {reason!r}", raw)
        if (reason == "tool_calls") != bool(self._tools):
            self._fail("finish_reason and native tool calls disagree", raw)
        ids: set[str] = set()
        for index, tool in sorted(self._tools.items()):
            if tool.type != "function" or not tool.id or not tool.name:
                self._fail(f"native tool call {index} lacks function type, id or name", raw)
            if tool.id in ids:
                self._fail(f"duplicate native tool call id: {tool.id!r}", raw)
            ids.add(tool.id)
            arguments = "".join(tool.fragments)
            try:
                parsed = json.loads(arguments, parse_constant=_reject_non_json_constant)
            except (json.JSONDecodeError, ValueError) as error:
                self._fail(f"incomplete or invalid tool arguments JSON: {error}; "
                           f"arguments={arguments[:500]!r}", raw)
            if not isinstance(parsed, dict):
                self._fail(f"native tool call {index} arguments must decode to an object; "
                           f"arguments={arguments[:500]!r}", raw)

    def finish(self) -> StreamResult:
        """Require a complete terminal event, rather than treating EOF as DONE."""
        if self._line or self._event_lines:
            self._fail("stream ended inside an SSE event", self._frame_so_far())
        if not self._done:
            self._fail("stream ended before [DONE]", b"")
        return StreamResult(copy.deepcopy(self._usage), self._token_usage, self._finish_reason)
