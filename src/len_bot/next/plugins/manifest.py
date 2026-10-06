"""Plugin manifests, typed configuration and filesystem discovery; no code execution."""
from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from importlib.metadata import version
import sys
import re
import tomllib
from typing import Annotated, Literal
from urllib.parse import quote, quote_plus, urlsplit

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, HttpUrl, JsonValue,
                      TypeAdapter, ValidationError, create_model, field_validator, model_validator)
from ..configuration.plugin import PLUGIN_NAME, PLUGIN_RESERVED
from ..config import HostConfig
from ..plugin import INTERFACE
from ..platform.identity import validate_scene
from ..tools.skills import Skill, load_catalog, load_plugin_skills
from ..storage.store import encode

BUILTIN = Path(__file__).resolve().parents[1] / "builtin_plugins"
STRICT = ConfigDict(extra="forbid", strict=True)
FIELD_TYPES = {"string": str, "secret": str, "integer": int, "number": float, "boolean": bool,
               "string_list": list[str], "object_list": list[dict[str, JsonValue]],
               "scene": str, "scene_list": list[str], "path": str, "url": str}
TEXT_TYPES = {"string", "secret", "integer", "number", "path", "url"}


def _absolute_path(value: str) -> str:
    if not Path(value).is_absolute():
        raise ValueError(f"必须是绝对路径；raw={value!r}")
    return value


def _web_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"必须是 http 或 https 地址；raw={value!r}")
    return value


def _configured_scene(scenes: Collection[str]):
    def check(value: str) -> str:
        validate_scene(value)
        if value not in scenes:
            raise ValueError(f"群 {value} 不在宿主配置的场景里")
        return value
    return check


class Option(BaseModel):
    """A choice shown as ``label`` and saved as ``value``."""
    model_config = STRICT
    value: str | int | float
    label: str = Field(min_length=1)


class ConfigItem(BaseModel):
    """One form value, also used for an object-list row's named children."""
    model_config = STRICT
    type: Literal["string", "integer", "number", "boolean", "string_list", "scene", "scene_list", "path", "url"]
    description: str = Field(min_length=1)
    label: str | None = Field(default=None, min_length=1)
    placeholder: str | None = Field(default=None, min_length=1)
    multiline: bool = False
    default: str | int | float | bool | list[str] | list[dict[str, JsonValue]] | None = None
    options: list[str | int | float | Option] | None = Field(default=None, min_length=1)
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)

    def choices(self) -> list[Option] | None:
        if self.options is None:
            return None
        return [option if isinstance(option, Option) else Option(value=option, label=str(option))
                for option in self.options]

    def annotation(self, scenes: Collection[str]):
        constraints = Field(ge=self.minimum, le=self.maximum) if self.type in {"integer", "number"} else Field()
        value_type = Annotated[FIELD_TYPES[self.type], constraints]
        if self.type in {"path", "url"}:
            check = _absolute_path if self.type == "path" else _web_url
            # ``default = ""`` declares the field may stay empty.
            optional = "default" in self.model_fields_set and self.default == ""
            value_type = Annotated[value_type, AfterValidator(lambda value: value if optional and value == "" else check(value))]
        elif self.type == "scene":
            value_type = Annotated[value_type, AfterValidator(_configured_scene(scenes))]
        elif self.type == "scene_list":
            value_type = list[Annotated[str, AfterValidator(_configured_scene(scenes))]]
        if self.options is not None:
            values = [option.value for option in self.choices()]
            def choice(value):
                if value not in values:
                    raise ValueError(f"必须是 {values!r} 中的一项")
                return value
            value_type = Annotated[value_type, AfterValidator(choice), Field(json_schema_extra={"enum": values})]
        return value_type

    @model_validator(mode="after")
    def valid_constraints(self):
        if (self.minimum is not None or self.maximum is not None) and self.type not in {"integer", "number"}:
            raise ValueError("minimum/maximum 只用于 integer 或 number")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum 不能大于 maximum")
        if self.multiline and self.type != "string":
            raise ValueError("multiline 只用于 string")
        if self.placeholder is not None and self.type not in TEXT_TYPES:
            raise ValueError(f"placeholder 只用于 {sorted(TEXT_TYPES)}")
        if self.options is not None:
            if self.type not in {"string", "integer", "number"}:
                raise ValueError("options 只用于 string、integer 或 number")
            adapter = TypeAdapter(FIELD_TYPES[self.type], config=STRICT)
            values = [option.value for option in self.choices()]
            for value in values:
                adapter.validate_python(value)
            if len(set(values)) != len(values):
                raise ValueError("options 的 value 不能重复")
        if "default" in self.model_fields_set:
            if self.type == "scene" or (self.type == "scene_list" and self.default != []):
                raise ValueError("scene/scene_list 不能预设群；scene_list 的默认值只能是 []")
            TypeAdapter(self.annotation(()), config=STRICT).validate_python(self.default)
        return self


class ConfigField(ConfigItem):
    type: Literal["string", "secret", "integer", "number", "boolean", "string_list", "object_list",
                  "scene", "scene_list", "path", "url"]
    group: str | None = Field(default=None, min_length=1)
    fields: dict[str, ConfigItem] = Field(default_factory=dict)

    def annotation(self, scenes: Collection[str]):
        if self.type == "object_list" and self.fields:
            return list[config_model("PluginConfigRow", self.fields, scenes)]
        return super().annotation(scenes)

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


def config_model(name: str, fields: Mapping[str, ConfigItem], scenes: Collection[str]) -> type[BaseModel]:
    return create_model(name, __config__=STRICT, **{
        key: (item.annotation(scenes), Field(item.default if "default" in item.model_fields_set else ...,
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

    def require_compatible(self, host: str | None = None, python: str | None = None) -> None:
        host = version('len-bot') if host is None else host
        python = '.'.join(map(str, sys.version_info[:3])) if python is None else python
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

    def values_model(self, scenes: Collection[str]) -> type[BaseModel]:
        """Values model; ``scene`` and ``scene_list`` values must be among ``scenes`` (the host's configured scenes)."""
        return config_model(f"PluginConfig_{self.name}", self.config, scenes)


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

