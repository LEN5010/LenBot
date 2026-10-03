"""OneBot transport settings, independent of host and scene assembly."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from .types import STRICT


class OneBotCommon(BaseModel):
    model_config = STRICT

    action_transport: Literal["websocket", "http"] = "websocket"
    http_url: str | None = None
    access_token: str = Field(default="", repr=False)
    request_timeout_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    ping_interval_seconds: float = Field(default=20.0, gt=0, allow_inf_nan=False)
    ping_timeout_seconds: float = Field(default=20.0, gt=0, allow_inf_nan=False)
    max_frame_bytes: int = Field(default=1048576, gt=0)
    upload_visible_root: str | None = None

    @field_validator("upload_visible_root")
    @classmethod
    def absolute_upload_root(cls, value: str | None) -> str | None:
        if value is not None:
            path = PurePosixPath(value)
            if (not path.is_absolute() or value.startswith("//") or ".." in path.parts
                    or any(char in value for char in ("\x00", "\r", "\n"))):
                raise ValueError("upload_visible_root must be an explicit absolute POSIX directory in NapCat")
        return value

    @field_validator("http_url")
    @classmethod
    def valid_http_url(cls, value: str | None) -> str | None:
        if value is not None:
            parts = urlsplit(value)
            if (parts.scheme not in {"http", "https"} or not parts.netloc
                    or parts.username is not None or parts.password is not None
                    or parts.query or parts.fragment):
                raise ValueError("must be an HTTP(S) URL without credentials, query or fragment")
        return value

    @field_validator("access_token")
    @classmethod
    def valid_access_token(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("access_token must not contain CR or LF")
        return value

    @model_validator(mode="after")
    def valid_action_endpoint(self) -> OneBotCommon:
        if self.action_transport == "http" and self.http_url is None:
            raise ValueError("http action transport requires http_url")
        if self.action_transport == "websocket" and self.http_url is not None:
            raise ValueError("websocket action transport does not accept http_url")
        return self


class OneBotForward(OneBotCommon):
    mode: Literal["forward_ws"]
    ws_url: str

    @field_validator("ws_url")
    @classmethod
    def valid_ws_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (parts.scheme not in {"ws", "wss"} or not parts.netloc
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            raise ValueError("must be a WS(S) URL without credentials, query or fragment")
        return value


class OneBotReverse(OneBotCommon):
    mode: Literal["reverse_ws"]
    listen_host: str
    listen_port: int = Field(ge=0, le=65535)

    @field_validator("listen_host")
    @classmethod
    def valid_listen_host(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("listen_host must not be blank")
        return value


OneBotSettings = Annotated[OneBotForward | OneBotReverse, Field(discriminator="mode")]


ONEBOT_SETTINGS = TypeAdapter(OneBotSettings, config=ConfigDict(hide_input_in_errors=True))
