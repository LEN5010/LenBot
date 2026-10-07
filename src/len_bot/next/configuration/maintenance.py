"""Panel access and offline task archive and evaluation settings."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .types import STRICT, EpochSeconds, _FiniteSeconds, _valid_scene


class PanelSettings(BaseModel):
    model_config = STRICT

    host: str
    port: int = Field(strict=True, ge=0, le=65535)
    username: str
    password_hash: str = Field(repr=False)
    cookie_secure: bool = False
    assets_dir: Path | None = None

    @field_validator("host", "username")
    @classmethod
    def required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("password_hash")
    @classmethod
    def valid_password_hash(cls, value: str) -> str:
        if re.fullmatch(r"[^$]+\$[0-9a-f]{64}", value) is None:
            raise ValueError("must use a nonempty salt followed by $ and 64 lowercase hex digits")
        return value


class TaskArchiveSettings(BaseModel):
    model_config = STRICT
    destination: Path
    scenes: list[str] = Field(min_length=1)

    @field_validator('scenes')
    @classmethod
    def valid_scenes(cls, value: list[str]) -> list[str]:
        for scene in value:
            _valid_scene(scene)
        if len(value) != len(set(value)):
            raise ValueError('task_archive.scenes must not repeat')
        return value


class EvaluationProfile(BaseModel):
    model_config = STRICT

    voice_mode: Literal["direct"]


class EvaluationSettings(BaseModel):
    model_config = STRICT

    profiles: dict[str, EvaluationProfile] = Field(min_length=1)
    sets: dict[str, Path] = Field(min_length=1)
    runs_directory: Path = Path("data/eval/runs")
    repetitions: int = Field(default=3, gt=0, strict=True)
    case_timeout_seconds: float = Field(default=300.0, gt=0, allow_inf_nan=False)

    @field_validator("profiles", "sets")
    @classmethod
    def safe_names(cls, values: dict) -> dict:
        for name in values:
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) is None:
                raise ValueError(f"name must be a single ASCII path segment: {name!r}")
        return values


class ReplayClockSettings(BaseModel):
    model_config = STRICT

    epoch: EpochSeconds
    monotonic_origin: _FiniteSeconds
