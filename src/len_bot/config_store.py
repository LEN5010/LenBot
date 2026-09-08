"""One root file for operator-owned runtime settings."""
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os
import tempfile

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StringConstraints, ValidationError, field_validator, model_validator

from len_bot.config import RuntimeConfig
from len_bot.cognition.providers import ProviderConfig, RoutingConfig
from len_bot.plugins.builtin.asoul_calendar.config import CalendarConfig
from len_bot.plugins.builtin.asoul_dynamics.config import DynamicsConfig
from len_bot.plugins.builtin.bilibili_live.config import LivePluginConfig
from len_bot.plugins.builtin.group_summary.config import GroupSummaryConfig


GroupSceneId = Annotated[str, StringConstraints(pattern=r"^group:[1-9][0-9]*$")]
PositiveUid = Annotated[int, Field(gt=0)]
MemberName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ClockTime = Annotated[str, StringConstraints(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
CalendarCommand = Literal["calendar_today", "calendar_tomorrow", "calendar_week"]
AnnouncementKind = Literal["live_started"]


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    providers: list[ProviderConfig]
    routing: RoutingConfig | None

    @model_validator(mode="after")
    def known_profiles(self):
        ids = [provider.id for provider in self.providers]
        if len(ids) != len(set(ids)):
            raise ValueError("models.providers contains duplicate IDs")
        if self.routing:
            for role, profile in self.routing:
                if profile is not None and profile.provider_id not in ids:
                    raise ValueError(f"models.routing.{role} references an unknown provider")
        return self


class DeliverySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    shadow: bool


class AccessSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    qq_reply_whitelist: list[PositiveUid]


class TimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    timezone: str = Field(min_length=1)
    week_start: int = Field(ge=0, le=6, description="0=周一，6=周日")
    afternoon_start: ClockTime
    afternoon_end: ClockTime

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("time.timezone must name an available IANA timezone") from None
        return value

    @model_validator(mode="after")
    def afternoon_range(self):
        if self.afternoon_start >= self.afternoon_end:
            raise ValueError("time.afternoon_end must be later than afternoon_start")
        return self


class MemberSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: MemberName
    aliases: list[MemberName]
    bilibili_uid: PositiveUid
    room_id: PositiveUid


class SceneSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    chat: bool
    plugins: list[str]
    commands: list[CalendarCommand]
    announcements: list[AnnouncementKind]
    live_subscriptions: list[MemberName]
    mention_all: bool


class PluginSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    config: dict | None
    _parsed_config: BaseModel | None = PrivateAttr(default=None)

    @property
    def parsed_config(self) -> BaseModel | None:
        return self._parsed_config

    @model_validator(mode="after")
    def configured_before_enable(self):
        if self.enabled and self.config is None:
            raise ValueError("An unconfigured plugin cannot be enabled")
        return self


class SearchPluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_results: int = Field(ge=1, le=10)
    request_timeout_seconds: float = Field(gt=0)
    tool_timeout_seconds: float = Field(gt=0)


class BilibiliPluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sessdata: str
    bili_jct: str
    request_timeout_seconds: float = Field(gt=0)
    tool_timeout_seconds: float = Field(gt=0)


PLUGIN_CONFIG_TYPES = {
    "bilibili_live_sensor": LivePluginConfig,
    "web_search_tool": SearchPluginConfig,
    "bilibili_content": BilibiliPluginConfig,
    "asoul_calendar": CalendarConfig,
    "asoul_dynamics": DynamicsConfig,
    "group_summary": GroupSummaryConfig,
}


class RootConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    runtime: RuntimeConfig
    models: ModelSettings
    delivery: DeliverySettings
    access: AccessSettings
    scenes: dict[GroupSceneId, SceneSettings]
    time: TimeSettings | None
    members: list[MemberSettings]
    plugins: dict[str, PluginSettings]

    @model_validator(mode="after")
    def plugin_parameters(self):
        if set(self.plugins) != set(PLUGIN_CONFIG_TYPES):
            raise ValueError("plugins must explicitly configure every installed builtin plugin")
        for name, schema in PLUGIN_CONFIG_TYPES.items():
            if self.plugins[name].config is None:
                continue
            try:
                parsed = schema.model_validate(self.plugins[name].config, strict=True)
            except ValidationError as error:
                details = "; ".join(".".join(map(str,item["loc"])) + ": " + item["msg"] for item in error.errors())
                raise ValueError(f"plugins.{name}.config: {details}") from None
            self.plugins[name].config = parsed.model_dump()
            self.plugins[name]._parsed_config = parsed
        return self

    @model_validator(mode="after")
    def scene_and_member_references(self):
        names = set()
        aliases = set()
        bilibili_uids = set()
        rooms = set()
        for member in self.members:
            if member.name in names or member.bilibili_uid in bilibili_uids or member.room_id in rooms:
                raise ValueError("members must have distinct names, bilibili_uid and room_id")
            names.add(member.name)
            bilibili_uids.add(member.bilibili_uid)
            rooms.add(member.room_id)
            for alias in [member.name, *member.aliases]:
                key = alias.casefold()
                if key in aliases:
                    raise ValueError(f"members contains an ambiguous name or alias: {alias}")
                aliases.add(key)
        for scene_id, scene in self.scenes.items():
            unknown_plugins = set(scene.plugins) - set(self.plugins)
            unknown_members = set(scene.live_subscriptions) - names
            if unknown_plugins:
                raise ValueError(f"scenes.{scene_id}.plugins references unknown plugins: {', '.join(sorted(unknown_plugins))}")
            if unknown_members:
                raise ValueError(f"scenes.{scene_id}.live_subscriptions references unknown members: {', '.join(sorted(unknown_members))}")
            if scene.commands and "asoul_calendar" not in scene.plugins:
                raise ValueError(f"scenes.{scene_id}.commands requires asoul_calendar in plugins")
            if scene.announcements and "bilibili_live_sensor" not in scene.plugins:
                raise ValueError(f"scenes.{scene_id}.announcements requires bilibili_live_sensor in plugins")
        for name in ("asoul_calendar", "asoul_dynamics", "group_summary", "bilibili_live_sensor"):
            if self.plugins[name].enabled and self.time is None:
                raise ValueError(f"plugins.{name} requires configured time settings")
        for name in ("asoul_dynamics", "bilibili_live_sensor"):
            if self.plugins[name].enabled and not self.members:
                raise ValueError(f"plugins.{name} requires configured members")
        summary = self.plugins["group_summary"].parsed_config
        if summary is not None and summary.page_chars > self.runtime.tool_result_max_chars:
            raise ValueError("plugins.group_summary.config.page_chars must not exceed runtime.tool_result_max_chars")
        return self


class ConfigStore:
    """The Runtime's existing config_update_lock owns every save."""
    def __init__(self, path: Path, current: RootConfig):
        self.path = path
        self.current = current

    @classmethod
    def load(cls):
        path = Path.cwd() / "lenbot.config.json"
        if not path.is_file():
            raise FileNotFoundError(f"Missing {path}; create it from lenbot.config.example.json before starting")
        try:
            current = RootConfig.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as error:
            details = "; ".join(".".join(map(str,item["loc"])) + ": " + item["msg"] for item in error.errors())
            raise ValueError(f"Invalid {path}: {details}") from None
        return cls(path, current)

    def save(self, candidate: RootConfig):
        descriptor, name = tempfile.mkstemp(prefix=".lenbot-config-", suffix=".json", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(candidate.model_dump_json(indent=2) + "\n")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.current = candidate
