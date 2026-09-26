"""Strict configuration for an explicitly isolated local chat lab."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from len_bot.next.model import ModelSettings


STRICT = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Provider(BaseModel):
    model_config = STRICT

    api: Literal["openai-chat"]
    base_url: str
    api_key: str = Field(repr=False)

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.netloc
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            raise ValueError("must be an HTTP(S) URL without credentials, query or fragment")
        return value

    @field_validator("api_key")
    @classmethod
    def required_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class Binding(BaseModel):
    model_config = STRICT

    provider: str
    model: str
    context_window_tokens: int = Field(gt=0)
    temperature: float = Field(default=0.6, ge=0, le=2, allow_inf_nan=False)
    max_output_tokens: int = Field(default=1024, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    reasoning_effort: str | None = None

    @field_validator("provider", "model")
    @classmethod
    def required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("reasoning_effort")
    @classmethod
    def valid_reasoning_effort(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def output_fits_window(self) -> Binding:
        if self.max_output_tokens >= self.context_window_tokens:
            raise ValueError("max_output_tokens must be less than context_window_tokens")
        return self


class Roles(BaseModel):
    model_config = STRICT

    mind: Binding
    voice: Binding


class Models(BaseModel):
    model_config = STRICT

    providers: dict[str, Provider]
    roles: Roles

    @model_validator(mode="after")
    def known_providers(self) -> Models:
        for role in ("mind", "voice"):
            provider_name = getattr(self.roles, role).provider
            if provider_name not in self.providers:
                raise ValueError(f"models.roles.{role}.provider references unknown provider {provider_name!r}")
        return self


class Compaction(BaseModel):
    model_config = STRICT

    trigger_ratio: float = Field(default=0.6, gt=0, lt=1, allow_inf_nan=False)
    keep_recent_entries: int = Field(default=30, ge=1)
    max_output_tokens: int = Field(default=1024, gt=0)


class LabConfig(BaseModel):
    model_config = STRICT

    mode: Literal["isolated"]
    scene: str
    bot_qq: str
    timezone: str
    database: Path
    persona: Path
    voice_mode: Literal["voice", "direct"] = "voice"
    max_steps: int = Field(default=8, gt=0)
    turn_timeout_seconds: float = Field(default=90.0, gt=0, allow_inf_nan=False)
    compaction: Compaction = Field(default_factory=Compaction)
    models: Models

    @field_validator("scene")
    @classmethod
    def valid_scene(cls, value: str) -> str:
        if re.fullmatch(r"(?:group|private):[1-9][0-9]*", value) is None:
            raise ValueError("must be group:<QQ> or private:<QQ>")
        return value

    @field_validator("bot_qq")
    @classmethod
    def valid_bot_qq(cls, value: str) -> str:
        if re.fullmatch(r"[1-9][0-9]*", value) is None:
            raise ValueError("must be a QQ number as text")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"unknown timezone {value!r}") from error
        return value

    @model_validator(mode="after")
    def mind_output_fits_compaction_trigger(self) -> LabConfig:
        mind = self.models.roles.mind
        if self.compaction.max_output_tokens >= mind.context_window_tokens:
            raise ValueError("compaction.max_output_tokens must be less than mind.context_window_tokens")
        trigger_tokens = mind.context_window_tokens * self.compaction.trigger_ratio
        if mind.max_output_tokens >= trigger_tokens:
            raise ValueError(
                "models.roles.mind.max_output_tokens must be less than "
                "models.roles.mind.context_window_tokens * compaction.trigger_ratio"
            )
        return self

    def model_settings(self, role: Literal["mind", "voice"]) -> ModelSettings:
        binding = getattr(self.models.roles, role)
        provider = self.models.providers[binding.provider]
        return ModelSettings(
            api=provider.api,
            base_url=provider.base_url,
            api_key=provider.api_key,
            model=binding.model,
            temperature=binding.temperature,
            max_output_tokens=binding.max_output_tokens,
            timeout_seconds=binding.timeout_seconds,
            reasoning_effort=binding.reasoning_effort,
        )


def _resolved_path(root: Path, value: object, *, within_root: bool, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a nonempty path string")
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve()
    if within_root and not resolved.is_relative_to(root):
        raise ValueError(f"{field} must resolve inside the lab root")
    return resolved


def load_config(root: Path) -> LabConfig:
    """Load only ``root/lenbot.config.json``; no environment or CLI overlay."""
    root = root.resolve()
    path = root / "lenbot.config.json"
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}") from error
    if not isinstance(source, dict):
        raise ValueError(f"{path}: configuration must be a JSON object")
    source["database"] = _resolved_path(root, source.get("database"), within_root=True, field="database")
    source["persona"] = _resolved_path(root, source.get("persona"), within_root=False, field="persona")
    try:
        return LabConfig.model_validate(source)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
            for item in error.errors(include_input=False)
        )
        raise ValueError(f"{path}: invalid lab configuration: {details}") from error
