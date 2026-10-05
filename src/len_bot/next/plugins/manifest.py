"""Plugin manifests, typed configuration and filesystem discovery; no code execution."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from importlib.metadata import version
import sys
import re
import tomllib
from typing import Annotated, Literal
from urllib.parse import quote, quote_plus

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, HttpUrl, JsonValue,
                      TypeAdapter, ValidationError, create_model, field_validator, model_validator)
from ..configuration.plugin import PLUGIN_NAME, PLUGIN_RESERVED
from ..config import HostConfig
from ..plugin import INTERFACE
from ..tools.skills import Skill, load_catalog, load_plugin_skills
from ..storage.store import encode

BUILTIN = Path(__file__).resolve().parents[1] / "builtin_plugins"
STRICT = ConfigDict(extra="forbid", strict=True)
FIELD_TYPES = {"string": str, "secret": str, "integer": int, "number": float, "boolean": bool,
               "string_list": list[str], "object_list": list[dict[str, JsonValue]]}


class ConfigItem(BaseModel):
    """One form value, also used for an object-list row's named children."""
    model_config = STRICT
    type: Literal["string", "integer", "number", "boolean", "string_list"]
    description: str = Field(min_length=1)
    default: str | int | float | bool | list[str] | list[dict[str, JsonValue]] | None = None
    options: list[str | int | float] | None = Field(default=None, min_length=1)
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)

    def annotation(self):
        constraints = Field(ge=self.minimum, le=self.maximum) if self.type in {"integer", "number"} else Field()
        value_type = Annotated[FIELD_TYPES[self.type], constraints]
        if self.options is not None:
            def choice(value):
                if value not in self.options:
                    raise ValueError(f"必须是 {self.options!r} 中的一项")
                return value
            value_type = Annotated[value_type, AfterValidator(choice), Field(json_schema_extra={"enum": self.options})]
        return value_type

    @model_validator(mode="after")
    def valid_constraints(self):
        if (self.minimum is not None or self.maximum is not None) and self.type not in {"integer", "number"}:
            raise ValueError("minimum/maximum 只用于 integer 或 number")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum 不能大于 maximum")
        if self.options is not None:
            if self.type not in {"string", "integer", "number"}:
                raise ValueError("options 只用于 string、integer 或 number")
            adapter = TypeAdapter(FIELD_TYPES[self.type], config=STRICT)
            for option in self.options:
                adapter.validate_python(option)
        if "default" in self.model_fields_set:
            TypeAdapter(self.annotation(), config=STRICT).validate_python(self.default)
        return self


class ConfigField(ConfigItem):
    type: Literal["string", "secret", "integer", "number", "boolean", "string_list", "object_list"]
    fields: dict[str, ConfigItem] = Field(default_factory=dict)

    def annotation(self):
        if self.type == "object_list" and self.fields:
            return list[config_model("PluginConfigRow", self.fields)]
        return super().annotation()

    @model_validator(mode="after")
    def object_fields(self):
        if self.fields and self.type != "object_list":
            raise ValueError("fields 只用于 object_list")
        check_field_names(self.fields)
        return self


def check_field_names(fields: Mapping[str, ConfigItem]) -> None:
    for key in fields:
        if re.fullmatch(r"[a-z][a-z0-9_]*", key) is None:
            raise ValueError(f"配置字段 {key!r} 只能使用小写字母、数字和下划线")


def config_model(name: str, fields: Mapping[str, ConfigItem]) -> type[BaseModel]:
    return create_model(name, __config__=STRICT, **{
        key: (item.annotation(), Field(item.default if "default" in item.model_fields_set else ...,
                                       description=item.description, validate_default=True))
        for key, item in fields.items()
    })


class Manifest(BaseModel):
    model_config = STRICT
    name: str
    version: str = Field(min_length=1)
    interface: int
    requires_lenbot: str
    requires_python: str
    platforms: list[Literal['linux', 'darwin', 'win32']] = Field(min_length=1)
    reload: Literal['plugin', 'host']
    authors: list[str] = Field(min_length=1)
    license: str = Field(min_length=1)
    description: str = Field(min_length=1)
    repository: HttpUrl | None = None
    homepage: HttpUrl | None = None
    dependencies: list[str] = Field(default_factory=list)
    config: dict[str, ConfigField] = Field(default_factory=dict)

    @field_validator('version')
    @classmethod
    def comparable_version(cls, value: str) -> str:
        return str(Version(value))

    @field_validator('requires_lenbot', 'requires_python')
    @classmethod
    def version_range(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('Version range must be explicit')
        return str(SpecifierSet(value))

    def require_compatible(self) -> None:
        host, python = version('len-bot'), '.'.join(map(str, sys.version_info[:3]))
        if Version(host) not in SpecifierSet(self.requires_lenbot):
            raise ValueError(f'{self.name} requires host {self.requires_lenbot}; actual={host}')
        if Version(python) not in SpecifierSet(self.requires_python):
            raise ValueError(f'{self.name} requires Python {self.requires_python}; actual={python}')
        if sys.platform not in self.platforms:
            raise ValueError(f'{self.name} requires platforms {self.platforms!r}; actual={sys.platform}')

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if PLUGIN_NAME.fullmatch(value) is None or value in PLUGIN_RESERVED:
            raise ValueError("name must use lowercase letters, digits and underscores, and not be paths/data_directory/disabled")
        return value

    @field_validator("config")
    @classmethod
    def valid_fields(cls, value: dict[str, ConfigField]) -> dict[str, ConfigField]:
        check_field_names(value)
        return value

    def values_model(self) -> type[BaseModel]:
        return config_model(f"PluginConfig_{self.name}", self.config)


def redact_values(text: str, manifest: Manifest | None, values: Mapping[str, object]) -> str:
    if manifest is None:
        return text
    secrets = set()
    for key, item in manifest.config.items():
        if item.type != "secret":
            continue
        value = values.get(key, item.default)
        if isinstance(value, str) and value:
            secrets.update((value, encode(value)[1:-1], repr(value)[1:-1], quote(value, safe=""), quote_plus(value)))
    for value in sorted(secrets, key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    return text


def parse_manifest(path: Path) -> Manifest:
    text = path.read_text(encoding="utf-8")
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path}: {error}; 原文开头：{text[:300]!r}") from error
    interface = raw.get("interface")
    if interface != INTERFACE:
        raise ValueError(f"{path}: 插件接口版本 {interface!r} 与宿主接口版本 {INTERFACE} 不一致，未加载（不做兼容）")
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as error:
        raise ValueError(f"{path}: {error}; 原文开头：{text[:300]!r}") from error
    return manifest


def read_manifest(directory: Path) -> Manifest:
    manifest = parse_manifest(directory / "plugin.toml")
    manifest.require_compatible()
    if manifest.name != directory.name:
        raise ValueError(f"{directory}/plugin.toml: name {manifest.name!r} 必须等于目录名 {directory.name!r}")
    return manifest


def discover(paths: list[Path]) -> tuple[dict[str, list[Path]], list[str]]:
    """Plugin directories by name across the builtin directory and ``plugins.paths``."""
    found: dict[str, list[Path]] = {}
    errors = []
    for base in (BUILTIN, *paths):
        if not base.is_dir():
            errors.append(f"插件目录不存在或不是目录：{base}")
            continue
        for directory in sorted(base.iterdir()):
            if (directory / "plugin.toml").is_file():
                found.setdefault(directory.name, []).append(directory)
    return found, errors


def scene_skill_catalog(config: HostConfig, scene: str) -> tuple[Skill, ...]:
    """Read a saved scene's skill sources without importing plugin executable code."""
    if config.worker is None:
        return ()
    skills = list(load_catalog(config.worker.skills_directory, scene,
                               public_browser=config.worker.public_browser))
    if config.plugins is not None:
        found, _ = discover(config.plugins.paths)
        for name in config.scenes[scene].plugins:
            if name in config.plugins.disabled:
                continue
            directories = found.get(name, [])
            if len(directories) != 1:
                raise ValueError(f"插件 {name} 的技能来源无法定位到唯一目录：{directories}")
            read_manifest(directories[0])
            skills.extend(load_plugin_skills(directories[0] / "skills", name))
    return tuple(skills)


