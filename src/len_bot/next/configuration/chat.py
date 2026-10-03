"""Scene conversation, expression, attention and arrangement settings."""

from __future__ import annotations

import re
from datetime import time as WallTime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .types import STRICT, _valid_timezone
from ..runtime.identity import IdentitySettings
from .learning import LearningSettings
from .tasks import TaskSettings


class Compaction(BaseModel):
    model_config = STRICT

    trigger_ratio: float = Field(default=0.6, gt=0, lt=1, allow_inf_nan=False)
    keep_recent_tokens: int = Field(default=20000, gt=0, strict=True)
    max_output_tokens: int = Field(default=1024, gt=0)


class TextDelivery(BaseModel):
    model_config = STRICT

    max_chars: int = Field(default=300, gt=0, strict=True)
    min_interval_seconds: float = Field(default=0.6, ge=0, allow_inf_nan=False)
    max_interval_seconds: float = Field(default=2.0, ge=0, allow_inf_nan=False)
    chars_per_second: float = Field(default=40.0, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_interval(self) -> TextDelivery:
        if self.min_interval_seconds > self.max_interval_seconds:
            raise ValueError("min_interval_seconds must not exceed max_interval_seconds")
        return self


class WebReadSettings(BaseModel):
    model_config = STRICT

    timeout_seconds: float = Field(default=20, gt=0, allow_inf_nan=False)


class ImageSettings(BaseModel):
    model_config = STRICT

    max_bytes: int = Field(default=10000000, gt=0)
    max_pixels: int = Field(default=25000000, gt=0)
    max_dimension: int = Field(default=1280, gt=0)
    timeout_seconds: float = Field(default=20, gt=0, allow_inf_nan=False)


ScheduleRole = Literal["owner", "admin", "group_manager", "whitelist", "member"]


class ScheduleSettings(BaseModel):
    model_config = STRICT

    enabled: bool = True
    max_pending: int = Field(default=50, gt=0)
    owner: str | None = None
    admins: list[str] = Field(default_factory=list)
    whitelist: list[str] = Field(default_factory=list)
    own: list[ScheduleRole] = Field(
        default_factory=lambda: ["owner", "admin", "group_manager", "whitelist", "member"]
    )
    others: list[ScheduleRole] = Field(default_factory=lambda: ["owner", "admin", "group_manager"])
    manage: list[ScheduleRole] = Field(default_factory=lambda: ["owner", "admin", "group_manager"])
    autonomous: bool = True

    @field_validator("owner")
    @classmethod
    def valid_owner(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[1-9][0-9]*", value) is None:
            raise ValueError("owner must be a positive QQ number as text")
        return value

    @field_validator("admins", "whitelist")
    @classmethod
    def valid_qqs(cls, values: list[str]) -> list[str]:
        if any(re.fullmatch(r"[1-9][0-9]*", value) is None for value in values):
            raise ValueError("must contain positive QQ numbers as text")
        return values

    @field_validator("own", "others", "manage")
    @classmethod
    def unique_roles(cls, values: list[ScheduleRole]) -> list[ScheduleRole]:
        if len(values) != len(set(values)):
            raise ValueError("roles must not repeat")
        return values


def _local_clock(value: object) -> WallTime:
    if not isinstance(value, str) or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9])?", value) is None:
        raise ValueError("must be a local HH:MM or HH:MM:SS clock without offset or fraction")
    return WallTime.fromisoformat(value)


class QuietHours(BaseModel):
    model_config = STRICT

    start: WallTime
    end: WallTime
    direct: Literal["allow", "notice", "defer"] = "defer"
    notice_text: str | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def local_clock(cls, value: object) -> WallTime:
        return _local_clock(value)

    @model_validator(mode="after")
    def valid_period(self) -> "QuietHours":
        if self.start == self.end:
            raise ValueError("quiet_hours start and end must differ")
        if self.direct == "notice":
            if self.notice_text is None or not self.notice_text.strip():
                raise ValueError("quiet_hours notice requires nonblank notice_text")
        elif self.notice_text is not None:
            raise ValueError("quiet_hours notice_text is only accepted for direct=notice")
        return self


class Proactive(BaseModel):
    """Wake a quiet group at most once per scene-local day inside active hours."""

    model_config = STRICT

    idle_seconds: float = Field(default=10800.0, ge=600, allow_inf_nan=False)
    start: WallTime = WallTime(10, 0)
    end: WallTime = WallTime(22, 0)

    @field_validator("start", "end", mode="before")
    @classmethod
    def local_clock(cls, value: object) -> WallTime:
        return _local_clock(value)

    @model_validator(mode="after")
    def valid_period(self) -> Proactive:
        if self.start == self.end:
            raise ValueError("proactive start and end must differ")
        return self


class Attention(BaseModel):
    model_config = STRICT

    only_direct: bool = False
    keywords: list[str] = Field(default_factory=list)
    other_bot_qqs: list[str] = Field(default_factory=list)
    direct_idle_seconds: float = Field(default=1.5, ge=0, allow_inf_nan=False)
    direct_max_seconds: float = Field(default=4.0, gt=0, allow_inf_nan=False)
    named_idle_seconds: float = Field(default=3.0, ge=0, allow_inf_nan=False)
    named_max_seconds: float = Field(default=8.0, gt=0, allow_inf_nan=False)
    keyword_cooldown_seconds: float = Field(default=60.0, ge=0, allow_inf_nan=False)
    focus_seconds: float = Field(default=180.0, ge=0, allow_inf_nan=False)
    focus_idle_seconds: float = Field(default=4.0, ge=0, allow_inf_nan=False)
    focus_max_seconds: float = Field(default=12.0, gt=0, allow_inf_nan=False)
    activity: float = Field(default=0.3, ge=0, le=1, allow_inf_nan=False)
    ambient_threshold: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    ambient_min_interval_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    ambient_max_interval_seconds: float = Field(default=900.0, gt=0, allow_inf_nan=False)
    max_extensions: int = Field(default=2, ge=0)
    quiet_hours: QuietHours | None = None

    @field_validator("keywords")
    @classmethod
    def valid_keywords(cls, values: list[str]) -> list[str]:
        stripped = [value.strip() for value in values]
        if any(not value for value in stripped):
            raise ValueError("keywords must be nonempty after stripping whitespace")
        if len(stripped) != len(set(stripped)):
            raise ValueError("keywords must not repeat")
        return stripped

    @field_validator("other_bot_qqs")
    @classmethod
    def valid_other_bot_qqs(cls, values: list[str]) -> list[str]:
        if any(re.fullmatch(r"[1-9][0-9]*", value) is None for value in values):
            raise ValueError("other_bot_qqs must contain positive QQ numbers as text")
        return values

    @model_validator(mode="after")
    def idle_within_max(self) -> Attention:
        if self.direct_idle_seconds > self.direct_max_seconds:
            raise ValueError("direct_idle_seconds must not exceed direct_max_seconds")
        if self.named_idle_seconds > self.named_max_seconds:
            raise ValueError("named_idle_seconds must not exceed named_max_seconds")
        if self.focus_idle_seconds > self.focus_max_seconds:
            raise ValueError("focus_idle_seconds must not exceed focus_max_seconds")
        if self.ambient_min_interval_seconds > self.ambient_max_interval_seconds:
            raise ValueError("ambient_min_interval_seconds must not exceed ambient_max_interval_seconds")
        return self


class ScenePersona(BaseModel):
    model_config = STRICT

    persona_aliases: list[str]
    relationships: dict[str, str]
    behavior_addendum: str | None

    @field_validator("persona_aliases")
    @classmethod
    def nonblank_persona_aliases(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("persona_aliases must not contain blank entries")
        return values

    @field_validator("relationships")
    @classmethod
    def valid_relationships(cls, values: dict[str, str]) -> dict[str, str]:
        for qq, description in values.items():
            if re.fullmatch(r"[1-9][0-9]*", qq) is None:
                raise ValueError(f"relationships key must be a positive QQ number as text: {qq!r}")
            if not description.strip():
                raise ValueError(f"relationships[{qq!r}] must not be blank")
        return values

    @field_validator("behavior_addendum")
    @classmethod
    def nonblank_behavior_addendum(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("behavior_addendum must not be blank")
        return value


class SceneSettings(ScenePersona):
    replay_web: Path | None = None
    replay_images: Path | None = None
    chat_control_roles: list[ScheduleRole] = Field(default_factory=lambda: ["owner", "admin", "group_manager"])

    @field_validator("chat_control_roles")
    @classmethod
    def distinct_control_roles(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("chat_control_roles must not repeat")
        return values

    permissions: IdentitySettings | None = None
    # Explicit IANA override for this scene; None uses the root timezone.
    timezone: str | None = None
    persona_aliases: list[str] = Field(default_factory=list)
    relationships: dict[str, str] = Field(default_factory=dict)
    behavior_addendum: str | None = None
    persona: Path
    voice_mode: Literal["voice", "direct"] = "voice"
    voice_context_tokens: int = Field(default=6000, gt=0, strict=True)
    attention: Attention = Field(default_factory=Attention)
    schedules: ScheduleSettings = Field(default_factory=ScheduleSettings)
    tasks: TaskSettings = Field(default_factory=TaskSettings)
    learning: LearningSettings | None = None
    proactive: Proactive | None = None
    transcribe_audio: bool = False
    # Plugins enabled in this scene; each must be loaded by root ``plugins``.
    plugins: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def proactive_requirements(self) -> SceneSettings:
        if self.proactive is None:
            return self
        quiet = self.attention.quiet_hours
        if quiet is not None:
            def intervals(start: WallTime, end: WallTime) -> list[tuple[int, int]]:
                a = start.hour * 3600 + start.minute * 60 + start.second
                b = end.hour * 3600 + end.minute * 60 + end.second
                return [(a, b)] if a < b else [(a, 86400), (0, b)]

            active = intervals(self.proactive.start, self.proactive.end)
            silent = intervals(quiet.start, quiet.end)
            available = sum(b - a - sum(max(0, min(b, d) - max(a, c)) for c, d in silent)
                            for a, b in active)
            if available == 0:
                raise ValueError("proactive active hours are entirely covered by quiet_hours")
        return self

    @field_validator("timezone")
    @classmethod
    def valid_scene_timezone(cls, value: str | None) -> str | None:
        return None if value is None else _valid_timezone(value)

    @field_validator("plugins")
    @classmethod
    def distinct_plugins(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("plugins must not repeat a name")
        return value
