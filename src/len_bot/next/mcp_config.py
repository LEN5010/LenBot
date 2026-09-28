"""Explicit host MCP services; no connection or process creation during configuration parsing."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

STRICT = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
SERVICE_NAME = re.compile(r"[a-z][a-z0-9_]{0,23}\Z")


class StdioTransport(BaseModel):
    model_config = STRICT
    type: Literal["stdio"]
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    cwd: Path
    env: dict[str, str] = Field(default_factory=dict, repr=False)

    @field_validator("command")
    @classmethod
    def command_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("MCP command must be nonblank and contain no NUL")
        return value

    @field_validator("args")
    @classmethod
    def argument_text(cls, values: list[str]) -> list[str]:
        if any("\x00" in value for value in values):
            raise ValueError("MCP arguments cannot contain NUL")
        return values

    @field_validator("env")
    @classmethod
    def environment(cls, values: dict[str, str]) -> dict[str, str]:
        if any(not key or "=" in key or "\x00" in key + value for key, value in values.items()):
            raise ValueError("MCP environment names must be nonempty, without '=' or NUL; values cannot contain NUL")
        return values


class HttpTransport(BaseModel):
    model_config = STRICT
    type: Literal["http"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict, repr=False)

    @field_validator("url")
    @classmethod
    def endpoint(cls, value: str) -> str:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.netloc
                or parts.username is not None or parts.password is not None or parts.query or parts.fragment):
            raise ValueError("MCP URL must be HTTP(S) without userinfo, query or fragment; use headers for credentials")
        return value

    @field_validator("headers")
    @classmethod
    def header_values(cls, values: dict[str, str]) -> dict[str, str]:
        reserved = {"host", "content-length", "content-type", "accept", "mcp-session-id", "mcp-protocol-version"}
        if any(not key or key.lower() in reserved or "\r" in key + value or "\n" in key + value
               for key, value in values.items()):
            raise ValueError("MCP headers contain a reserved name, blank name, CR or LF")
        if len({key.lower() for key in values}) != len(values):
            raise ValueError("MCP headers must not repeat a name with different casing")
        return values


class MCPService(BaseModel):
    model_config = STRICT
    enabled: bool = False
    scenes: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300, allow_inf_nan=False)
    max_response_bytes: int = Field(default=1_048_576, ge=1024, le=10_000_000)
    transport: Annotated[StdioTransport | HttpTransport, Field(discriminator="type")]

    @field_validator("scenes")
    @classmethod
    def scene_list(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("MCP scenes must not repeat")
        return values
