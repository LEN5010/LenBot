"""One root file for operator-owned runtime settings."""
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import os
import tempfile

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StringConstraints, ValidationError, ValidationInfo, field_validator, model_validator

from len_bot.config import RuntimeConfig
from len_bot.cognition.budget import ReservationPolicy
from len_bot.cognition.providers import ProviderConfig, RoutingConfig, RetrievalRouting
from len_bot.plugins.catalog import PluginCatalog
from len_bot.runtime.capabilities import CapabilityGrant, validate_grants


GroupSceneId = Annotated[str, StringConstraints(pattern=r"^group:[1-9][0-9]*$")]
PositiveUid = Annotated[int, Field(gt=0)]
MemberName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ClockTime = Annotated[str, StringConstraints(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    providers: list[ProviderConfig]
    routing: RoutingConfig | None
    retrieval: RetrievalRouting = Field(default_factory=RetrievalRouting)

    @model_validator(mode="after")
    def known_profiles(self):
        ids = [provider.id for provider in self.providers]
        if len(ids) != len(set(ids)):
            raise ValueError("models.providers contains duplicate IDs")
        if self.routing:
            for role, profile in self.routing:
                if profile is not None and profile.provider_id not in ids:
                    raise ValueError(f"models.routing.{role} references an unknown provider")
        for profile in (self.retrieval.embedding, self.retrieval.rerank):
            if profile is not None and profile.provider_id not in ids:
                raise ValueError(f"models.retrieval references an unknown provider: {profile.provider_id}")
        if self.retrieval.rerank is not None and self.retrieval.rerank.protocol is None:
            raise ValueError("models.retrieval.rerank requires an explicitly confirmed protocol")
        return self


class DeliverySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    shadow: bool


class AccessSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    qq_reply_whitelist: list[PositiveUid]
    # The engine's own new capabilities stay off until an operator writes an
    # explicit grant here.  This is the only editable access-control list; the
    # database does not keep a second copy.
    capability_grants: list[CapabilityGrant] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct_grants(self):
        validate_grants(self.capability_grants)
        return self


class ResourceSettings(BaseModel):
    """Named quota policies.  A grant references one by name; no layer copies it.

    The same shape as `access`: one editable list, no second copy in the
    database.  Defaults stay absent so an upgrade introduces no new limit —
    only an operator adds a policy and points a grant at it.
    """
    model_config = ConfigDict(extra="forbid", strict=True)
    policies: dict[str, ReservationPolicy] = Field(default_factory=dict)


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


class ScenePluginSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool
    config: dict
    _parsed_config: BaseModel | None = PrivateAttr(default=None)

    @property
    def parsed_config(self) -> BaseModel:
        return self._parsed_config


class SceneSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    chat: bool
    semantic_retrieval: bool = Field(default=False, description='允许本群文本发送给已配置的语义检索提供方')
    plugins: dict[str, ScenePluginSettings]


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


class RootConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    runtime: RuntimeConfig
    models: ModelSettings
    delivery: DeliverySettings
    access: AccessSettings
    resources: ResourceSettings = Field(default_factory=ResourceSettings)
    scenes: dict[GroupSceneId, SceneSettings]
    time: TimeSettings | None
    members: list[MemberSettings]
    plugin_directories: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]]
    plugins: dict[str, PluginSettings]
    _catalog: PluginCatalog = PrivateAttr()

    @model_validator(mode="after")
    def plugin_parameters(self, info: ValidationInfo):
        if not info.context or not isinstance(info.context.get('plugin_catalog'), PluginCatalog):
            raise ValueError('Plugin descriptions must be discovered before parsing root settings')
        self._catalog = info.context['plugin_catalog']
        unknown = set(self.plugins) - set(self._catalog.entries)
        if unknown:
            raise ValueError('plugins references unknown plugin IDs: ' + ', '.join(sorted(unknown)))
        for name, setting in self.plugins.items():
            if setting.config is None:
                continue
            spec = self._catalog.entries[name].spec
            try:
                parsed = spec.config_model.model_validate(setting.config, strict=True)
            except ValidationError as error:
                details = "; ".join(".".join(map(str,item["loc"])) + ": " + item["msg"] for item in error.errors())
                raise ValueError(f"plugins.{name}.config: {details}") from None
            setting.config = parsed.model_dump()
            setting._parsed_config = parsed
            if spec.validate_config:
                try:
                    spec.validate_config(parsed, self)
                except ValueError as error:
                    raise ValueError(f'plugins.{name}: {error}') from None
        for scene_id, scene in self.scenes.items():
            for name, setting in scene.plugins.items():
                if name not in self.plugins:
                    raise ValueError(f'scenes.{scene_id}.plugins references unknown configured plugin: {name}')
                spec = self._catalog.entries[name].spec
                try:
                    parsed = spec.scene_config_model.model_validate(setting.config, strict=True)
                    if spec.validate_scene_config:
                        spec.validate_scene_config(parsed, self)
                except (ValidationError, ValueError) as error:
                    raise ValueError(f'scenes.{scene_id}.plugins.{name}.config: {error}') from None
                setting.config = parsed.model_dump()
                setting._parsed_config = parsed
        return self

    @model_validator(mode="after")
    def member_references(self):
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
        return self


class ConfigStore:
    """The Runtime's existing config_update_lock owns every save."""
    def __init__(self, path: Path, current: RootConfig):
        self.path = path
        self.current = current
        self.catalog = current._catalog

    def parse(self, data: dict) -> RootConfig:
        candidate = RootConfig.model_validate(data, context={'plugin_catalog': self.catalog})
        if candidate.plugin_directories != self.current.plugin_directories:
            raise ValueError('Changing plugin_directories requires an offline configuration update and restart')
        return candidate

    @classmethod
    def load(cls):
        path = Path.cwd() / "lenbot.config.json"
        if not path.is_file():
            raise FileNotFoundError(f"Missing {path}; create it from lenbot.config.example.json before starting")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            class PluginDirectories(BaseModel):
                model_config = ConfigDict(extra='ignore', strict=True)
                plugin_directories: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]]
            locations = PluginDirectories.model_validate(data)
            catalog = PluginCatalog.discover(locations.plugin_directories)
            current = RootConfig.model_validate(data, context={'plugin_catalog': catalog})
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
