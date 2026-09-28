"""Typed configuration for the isolated chat lab and platform transport boundary."""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from datetime import time as WallTime
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

from len_bot.next.model import ModelSettings
from len_bot.next.pricing import ModelPrice
from len_bot.next.tasks_config import TaskSettings, WorkerSettings
from len_bot.next.web_search import WebSearchSettings
from len_bot.next.memory import MemorySettings, LocalMemoryConfig, OpenVikingMemoryConfig


STRICT = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


def _epoch_seconds(seconds: float) -> float:
    try:
        datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError("Unix seconds are outside the representable UTC date range") from error
    return seconds


EpochSeconds = Annotated[float, Field(strict=True, allow_inf_nan=False), AfterValidator(_epoch_seconds)]
_FiniteSeconds = Annotated[float, Field(strict=True, allow_inf_nan=False)]


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
    vision: Binding | None = None
    memory: Binding | None = None
    worker: Binding | None = None


class Models(BaseModel):
    model_config = STRICT

    providers: dict[str, Provider]
    roles: Roles
    prices: dict[str, dict[str, ModelPrice]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def known_providers(self) -> Models:
        for role in ("mind", "voice", "vision", "memory", "worker"):
            binding = getattr(self.roles, role)
            if binding is None:
                continue
            provider_name = binding.provider
            if provider_name not in self.providers:
                raise ValueError(f"models.roles.{role}.provider references unknown provider {provider_name!r}")
        for provider_name, model_prices in self.prices.items():
            if not provider_name.strip():
                raise ValueError("models.prices provider name must not be blank")
            if provider_name not in self.providers:
                raise ValueError(f"models.prices references unknown provider {provider_name!r}")
            if any(not model_name.strip() for model_name in model_prices):
                raise ValueError(f"models.prices.{provider_name} model name must not be blank")
        return self


class Compaction(BaseModel):
    model_config = STRICT

    trigger_ratio: float = Field(default=0.6, gt=0, lt=1, allow_inf_nan=False)
    keep_recent_entries: int = Field(default=30, ge=1)
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


class QuietHours(BaseModel):
    model_config = STRICT

    start: WallTime
    end: WallTime
    direct: Literal["allow", "notice", "defer"] = "defer"
    notice_text: str | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def local_clock(cls, value: object) -> WallTime:
        if not isinstance(value, str) or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9])?", value) is None:
            raise ValueError("must be a local HH:MM or HH:MM:SS clock without offset or fraction")
        return WallTime.fromisoformat(value)

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


def _valid_scene(value: str) -> str:
    if re.fullmatch(r"(?:group|private):[1-9][0-9]*", value) is None:
        raise ValueError("must be group:<QQ> or private:<QQ>")
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


class HistoryExportSettings(BaseModel):
    model_config = STRICT

    target: Path
    backup: Path
    scenes: list[str] = Field(min_length=1)

    @field_validator("scenes")
    @classmethod
    def valid_scenes(cls, scenes: list[str]) -> list[str]:
        return _history_scenes(scenes, "history_export")


class EvaluationProfile(BaseModel):
    model_config = STRICT

    voice_mode: Literal["voice", "direct"]


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


class SharedConfig(BaseModel):
    model_config = STRICT

    bot_qq: str
    timezone: str
    database: Path
    onebot: OneBotSettings | None = None
    delivery: Literal["simulated", "onebot"] = "simulated"
    max_steps: int = Field(default=8, gt=0)
    turn_timeout_seconds: float = Field(default=90.0, gt=0, allow_inf_nan=False)
    compaction: Compaction = Field(default_factory=Compaction)
    text_delivery: TextDelivery = Field(default_factory=TextDelivery)
    web_read: WebReadSettings | None = None
    web_search: WebSearchSettings | None = None
    memory: MemorySettings | None = None
    worker: WorkerSettings | None = None
    images: ImageSettings = Field(default_factory=ImageSettings)
    history_import: HistoryImportSettings | None = None
    history_export: HistoryExportSettings | None = None
    models: Models

    @model_validator(mode="after")
    def memory_provider_exists(self) -> SharedConfig:
        if isinstance(self.memory, LocalMemoryConfig) and self.memory.local.embedding is not None:
            provider = self.memory.local.embedding.provider
            if provider not in self.models.providers:
                raise ValueError(f"memory.local.embedding.provider references unknown provider {provider!r}")
        if (isinstance(self.memory, LocalMemoryConfig) and self.memory.ingest is not None
                and self.models.roles.memory is None):
            raise ValueError("local memory ingest requires explicit models.roles.memory")
        if self.worker is not None:
            binding = self.models.roles.worker
            if binding is None:
                raise ValueError("worker requires explicit models.roles.worker")
            if (self.worker.max_cost is not None
                    and self.models.prices.get(binding.provider, {}).get(binding.model) is None):
                raise ValueError(
                    "worker.max_cost requires models.prices for the exact worker provider and model"
                )
            if (self.worker.compaction_reserve_tokens + self.worker.compaction_keep_recent_tokens
                    >= binding.context_window_tokens):
                raise ValueError(
                    "worker.compaction_reserve_tokens + worker.compaction_keep_recent_tokens "
                    "must be less than models.roles.worker.context_window_tokens; "
                    "set both limits explicitly for a smaller worker context window"
                )
        return self

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
    def mind_output_fits_compaction_trigger(self) -> SharedConfig:
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

    @model_validator(mode="after")
    def valid_delivery(self) -> SharedConfig:
        if self.delivery == "onebot" and self.onebot is None:
            raise ValueError("delivery=onebot requires onebot transport")
        return self

    @model_validator(mode="after")
    def distinct_history_paths(self) -> SharedConfig:
        importing = self.history_import
        if importing is not None:
            paths = [self.database.resolve(), importing.source.resolve(), importing.backup.resolve()]
            if len(set(paths)) != len(paths):
                raise ValueError("history_import.source, history_import.backup and database must differ")
        exporting = self.history_export
        if exporting is not None:
            paths = [self.database.resolve(), exporting.target.resolve(), exporting.backup.resolve()]
            if len(set(paths)) != len(paths):
                raise ValueError("history_export.target, history_export.backup and database must differ")
        return self

    def model_settings(self, role: Literal["mind", "voice", "vision", "memory", "worker"]) -> ModelSettings:
        binding = getattr(self.models.roles, role)
        if binding is None:
            raise ValueError(f"models.roles.{role} is not configured")
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
    persona_aliases: list[str] = Field(default_factory=list)
    relationships: dict[str, str] = Field(default_factory=dict)
    behavior_addendum: str | None = None
    persona: Path
    voice_mode: Literal["voice", "direct"] = "voice"
    attention: Attention = Field(default_factory=Attention)
    schedules: ScheduleSettings = Field(default_factory=ScheduleSettings)
    tasks: TaskSettings = Field(default_factory=TaskSettings)


def _check_schedule_identity(bot_qq: str, schedules: ScheduleSettings) -> None:
    if (schedules.owner == bot_qq or bot_qq in schedules.admins
            or bot_qq in schedules.whitelist):
        raise ValueError("schedules owner, admins and whitelist must not include bot_qq")


class LabConfig(SharedConfig, SceneSettings):
    mode: Literal["isolated"]
    scene: str
    panel: PanelSettings | None = None
    evaluation: EvaluationSettings | None = None
    replay_clock: ReplayClockSettings | None = None

    @field_validator("scene")
    @classmethod
    def valid_scene(cls, value: str) -> str:
        return _valid_scene(value)

    @model_validator(mode="after")
    def bot_is_not_schedule_requester(self) -> LabConfig:
        _check_schedule_identity(self.bot_qq, self.schedules)
        if (self.tasks.owner == self.bot_qq or self.bot_qq in self.tasks.admins
                or self.bot_qq in self.tasks.whitelist):
            raise ValueError("tasks owner, admins and whitelist must not include bot_qq")
        if self.tasks.enabled or self.worker is not None:
            raise ValueError("worker tasks require the isolated-multi host, not the single-scene lab or replay")
        if isinstance(self.memory, OpenVikingMemoryConfig) and set(self.memory.openviking.scenes) != {self.scene}:
            raise ValueError("memory.openviking.scenes must contain only the configured scene")
        if self.history_import is not None and self.history_import.scenes != [self.scene]:
            raise ValueError("history_import.scenes must contain only the configured scene")
        if self.history_export is not None and self.history_export.scenes != [self.scene]:
            raise ValueError("history_export.scenes must contain only the configured scene")
        if self.replay_clock is not None:
            incompatible = [
                field for field, enabled in (
                    ("onebot", self.onebot is not None),
                    ("panel", self.panel is not None),
                    ("web_read", self.web_read is not None),
                    ("web_search", self.web_search is not None),
                    ("memory", self.memory is not None),
                    ("models.roles.vision", self.models.roles.vision is not None),
                    ("history_import", self.history_import is not None),
                    ("history_export", self.history_export is not None),
                    ("worker", self.worker is not None),
                    ("delivery", self.delivery != "simulated"),
                ) if enabled
            ]
            if incompatible:
                raise ValueError(
                    "replay_clock requires isolated stdin with delivery=simulated and no external "
                    f"or offline entry settings; incompatible: {', '.join(incompatible)}"
                )
        return self


class HostConfig(SharedConfig):
    mode: Literal["isolated-multi"]
    onebot: OneBotSettings
    panel: PanelSettings | None = None
    scenes: dict[str, SceneSettings] = Field(min_length=1)
    max_model_requests: int = Field(default=4, gt=0, strict=True)

    @field_validator("scenes")
    @classmethod
    def valid_scenes(cls, scenes: dict[str, SceneSettings]) -> dict[str, SceneSettings]:
        for scene in scenes:
            _valid_scene(scene)
        return scenes

    @model_validator(mode="after")
    def bot_is_not_schedule_requester(self) -> HostConfig:
        if isinstance(self.memory, OpenVikingMemoryConfig) and set(self.memory.openviking.scenes) != set(self.scenes):
            raise ValueError("memory.openviking.scenes must exactly match configured scenes")
        for scene, settings in self.scenes.items():
            try:
                _check_schedule_identity(self.bot_qq, settings.schedules)
            except ValueError as error:
                raise ValueError(f"scenes.{scene}: {error}") from error
            if (settings.tasks.owner == self.bot_qq or self.bot_qq in settings.tasks.admins
                    or self.bot_qq in settings.tasks.whitelist):
                raise ValueError(f"scenes.{scene}.tasks owner, admins and whitelist must not include bot_qq")
            if settings.tasks.enabled and self.worker is None:
                raise ValueError(f"scenes.{scene}.tasks.enabled requires global worker settings")
        if self.history_import is not None:
            unknown = [scene for scene in self.history_import.scenes if scene not in self.scenes]
            if unknown:
                raise ValueError(f"history_import.scenes are not configured: {unknown!r}")
        if self.history_export is not None:
            unknown = [scene for scene in self.history_export.scenes if scene not in self.scenes]
            if unknown:
                raise ValueError(f"history_export.scenes are not configured: {unknown!r}")
        return self

    def scene_config(self, scene: str) -> LabConfig:
        if scene not in self.scenes:
            raise ValueError(f"Scene {scene} is not configured")
        # Both typed parts were validated at the single root boundary. Keep
        # parsed local clocks and paths as typed values rather than roundtripping.
        shared = {name: getattr(self, name) for name in SharedConfig.model_fields}
        # Both offline settings belong to the original root object and their
        # commands, not to this derived runtime scene view or a saved root file.
        shared["history_import"] = None
        shared["history_export"] = None
        local = {name: getattr(self.scenes[scene], name) for name in SceneSettings.model_fields}
        return LabConfig.model_construct(**shared, **local, mode="isolated", scene=scene)


def _resolved_path(root: Path, value: object, *, within_root: bool, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a nonempty path string")
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve()
    if within_root and not resolved.is_relative_to(root):
        raise ValueError(f"{field} must resolve inside the lab root")
    return resolved


def _read_root(root: Path) -> tuple[Path, dict]:
    root = root.resolve()
    path = root / "lenbot.config.json"
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}") from error
    if not isinstance(source, dict):
        raise ValueError(f"{path}: configuration must be a JSON object")
    return path, source


def _validation_error(path: Path, error: ValidationError, kind: str) -> ValueError:
    details = "; ".join(
        f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
        for item in error.errors(include_input=False)
    )
    return ValueError(f"{path}: invalid {kind} configuration: {details}")


def _resolve_history_paths(root: Path, source: dict) -> None:
    importing = source.get("history_import")
    if isinstance(importing, dict):
        importing["source"] = _resolved_path(
            root, importing.get("source"), within_root=False, field="history_import.source"
        )
        importing["backup"] = _resolved_path(
            root, importing.get("backup"), within_root=True, field="history_import.backup"
        )
    exporting = source.get("history_export")
    if isinstance(exporting, dict):
        exporting["target"] = _resolved_path(
            root, exporting.get("target"), within_root=True, field="history_export.target"
        )
        exporting["backup"] = _resolved_path(
            root, exporting.get("backup"), within_root=True, field="history_export.backup"
        )


def _resolve_memory_path(root: Path, source: dict) -> None:
    memory = source.get("memory")
    if isinstance(memory, dict) and isinstance(memory.get("local"), dict):
        memory["local"]["directory"] = _resolved_path(
            root, memory["local"].get("directory"), within_root=True, field="memory.local.directory",
        )


def _resolve_worker_paths(root: Path, source: dict) -> None:
    worker = source.get("worker")
    if not isinstance(worker, dict):
        return
    binary = worker.get("docker_binary")
    if not isinstance(binary, str) or not Path(binary).is_absolute():
        raise ValueError("worker.docker_binary must be an explicit absolute path string")
    worker["docker_binary"] = Path(binary).resolve()
    for name in ("workspace_root", "runtime_root", "delivery_root"):
        worker[name] = _resolved_path(
            root, worker.get(name), within_root=True, field=f"worker.{name}",
        )
    if worker.get("skills_directory") is not None:
        worker["skills_directory"] = _resolved_path(
            root, worker["skills_directory"], within_root=True, field="worker.skills_directory",
        )


def _load_lab_source(path: Path, source: dict) -> LabConfig:
    root = path.parent
    source["database"] = _resolved_path(root, source.get("database"), within_root=True, field="database")
    source["persona"] = _resolved_path(root, source.get("persona"), within_root=False, field="persona")
    panel = source.get("panel")
    if isinstance(panel, dict) and panel.get("assets_dir") is not None:
        panel["assets_dir"] = _resolved_path(
            root, panel["assets_dir"], within_root=False, field="panel.assets_dir"
        )
    evaluation = source.get("evaluation")
    if isinstance(evaluation, dict):
        evaluation["runs_directory"] = _resolved_path(
            root, evaluation.get("runs_directory", "data/eval/runs"),
            within_root=True, field="evaluation.runs_directory",
        )
        sets = evaluation.get("sets")
        if isinstance(sets, dict):
            for name, location in sets.items():
                sets[name] = _resolved_path(
                    root, location, within_root=False, field=f"evaluation.sets.{name}",
                )
    _resolve_history_paths(root, source)
    _resolve_memory_path(root, source)
    _resolve_worker_paths(root, source)
    try:
        return LabConfig.model_validate(source)
    except ValidationError as error:
        raise _validation_error(path, error, "lab") from error


def _load_host_source(path: Path, source: dict) -> HostConfig:
    root = path.parent
    source["database"] = _resolved_path(root, source.get("database"), within_root=True, field="database")
    panel = source.get("panel")
    if isinstance(panel, dict) and panel.get("assets_dir") is not None:
        panel["assets_dir"] = _resolved_path(
            root, panel["assets_dir"], within_root=False, field="panel.assets_dir"
        )
    scenes = source.get("scenes")
    if isinstance(scenes, dict):
        for scene, settings in scenes.items():
            if isinstance(settings, dict):
                settings["persona"] = _resolved_path(
                    root, settings.get("persona"), within_root=False,
                    field=f"scenes.{scene}.persona",
                )
    _resolve_history_paths(root, source)
    _resolve_memory_path(root, source)
    _resolve_worker_paths(root, source)
    try:
        return HostConfig.model_validate(source)
    except ValidationError as error:
        raise _validation_error(path, error, "host") from error


def load_config(root: Path) -> LabConfig:
    """Load only ``root/lenbot.config.json``; no environment or CLI overlay."""
    path, source = _read_root(root)
    return _load_lab_source(path, source)


def read_scene_persona(root: Path) -> ScenePersona:
    """Read only the three scene-persona values from the validated single-scene root."""
    config = load_config(root)
    return ScenePersona.model_construct(
        persona_aliases=config.persona_aliases,
        relationships=config.relationships,
        behavior_addendum=config.behavior_addendum,
    )


def save_scene_persona(root: Path, changes: ScenePersona) -> None:
    """Replace only scene-persona values in the sole validated root file."""
    path, candidate = _read_root(root)
    candidate["persona_aliases"] = changes.persona_aliases
    candidate["relationships"] = changes.relationships
    candidate["behavior_addendum"] = changes.behavior_addendum
    # Path resolution is only for validation. Preserve the original relative
    # paths and every other raw root field in the file that is written.
    _load_lab_source(path, copy.deepcopy(candidate))

    descriptor, name = tempfile.mkstemp(prefix=".lenbot-config-", suffix=".json", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(candidate, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_host_config(root: Path) -> HostConfig:
    """Load one explicit multi-scene host from its sole root configuration."""
    path, source = _read_root(root)
    return _load_host_source(path, source)


def load_instance_config(root: Path) -> LabConfig | HostConfig:
    """Select the explicitly declared instance format from the one root file."""
    path, source = _read_root(root)
    if source.get("mode") == "isolated":
        return _load_lab_source(path, source)
    if source.get("mode") == "isolated-multi":
        return _load_host_source(path, source)
    raise ValueError(f"{path}: mode must explicitly be isolated or isolated-multi")
