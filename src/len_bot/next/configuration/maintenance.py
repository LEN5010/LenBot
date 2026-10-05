"""Panel access and explicit offline import, export and evaluation settings."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .types import STRICT, EpochSeconds, _FiniteSeconds, _valid_scene, _valid_timezone


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


def _history_scenes(scenes: list[str], setting: str) -> list[str]:
    for scene in scenes:
        _valid_scene(scene)
    if len(scenes) != len(set(scenes)):
        raise ValueError(f"{setting}.scenes must not repeat")
    return scenes


class HistoryImportSettings(BaseModel):
    model_config = STRICT

    source: Path
    backup: Path
    scenes: list[str] = Field(min_length=1)
    recent_messages: int = Field(default=50, gt=0, strict=True)

    @field_validator("scenes")
    @classmethod
    def valid_scenes(cls, scenes: list[str]) -> list[str]:
        return _history_scenes(scenes, "history_import")


class MediaImportSettings(BaseModel):
    model_config = STRICT
    source: Path
    directory: Path
    original_directory: str
    backup: Path
    scenes: list[str] = Field(min_length=1)

    @field_validator('scenes')
    @classmethod
    def valid_scenes(cls, value: list[str]) -> list[str]:
        return _history_scenes(value, 'media_import')

    @field_validator('original_directory')
    @classmethod
    def original_root(cls, value: str) -> str:
        if not value.startswith('/') or '\\' in value or any(part in {'.', '..'} for part in value.split('/')):
            raise ValueError('media_import.original_directory must be the original absolute POSIX media directory')
        return value


class MediaArchiveSettings(BaseModel):
    model_config = STRICT
    source: Path
    directory: Path
    original_directory: str
    destination: Path
    scenes: list[str] = Field(min_length=1)
    include_public: bool

    @field_validator('scenes')
    @classmethod
    def valid_scenes(cls, value: list[str]) -> list[str]:
        return _history_scenes(value, 'media_archive')

    @field_validator('original_directory')
    @classmethod
    def original_root(cls, value: str) -> str:
        if not value.startswith('/') or '\\' in value or any(part in {'.', '..'} for part in value.split('/')):
            raise ValueError('media_archive.original_directory must be the original absolute POSIX media directory')
        return value


class TaskArchiveSettings(BaseModel):
    model_config = STRICT
    destination: Path
    scenes: list[str] = Field(min_length=1)

    @field_validator('scenes')
    @classmethod
    def valid_scenes(cls, value: list[str]) -> list[str]:
        return _history_scenes(value, 'task_archive')


class ReminderImportSettings(BaseModel):
    model_config = STRICT

    source: Path
    backup: Path
    scenes: list[str] = Field(min_length=1)
    timezone: str

    @field_validator('scenes')
    @classmethod
    def valid_scenes(cls, value: list[str]) -> list[str]:
        return _history_scenes(value, 'reminder_import')

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return _valid_timezone(value)


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
