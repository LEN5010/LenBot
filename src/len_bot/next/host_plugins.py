"""Panel access to loaded plugins and their saved root configuration (M14)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .config import PLUGIN_NAME, PLUGIN_RESERVED, HostConfig, _read_root
from .host_settings import _body, _prepare, _read_saved
from .network import NetworkRuntime
from .plugin_host import ConfigField, ConfigItem, Manifest, discover, read_manifest, redact_values


STRICT = ConfigDict(extra="forbid", strict=True)


class PluginChange(BaseModel):
    model_config = STRICT
    enabled: bool
    # Secret fields may be null to keep the saved value unchanged.
    config: dict[str, object] = Field(default_factory=dict)


class ScenePlugins(BaseModel):
    model_config = STRICT
    plugins: list[str]

    @field_validator("plugins")
    @classmethod
    def distinct(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("插件名不能重复")
        return value


class PluginPaths(BaseModel):
    model_config = STRICT
    paths: list[str]
    data_directory: str = Field(min_length=1)


def _field_info(manifest: Manifest) -> list[dict]:
    def field(key: str, item: ConfigItem) -> dict:
        return {"key": key, "type": item.type, "description": item.description,
                "required": "default" not in item.model_fields_set,
                "default": None if item.type == "secret" else item.default,
                "options": item.options, "minimum": item.minimum, "maximum": item.maximum,
                "fields": ([field(name, child) for name, child in item.fields.items()]
                           if isinstance(item, ConfigField) else [])}
    return [field(key, item) for key, item in manifest.config.items()]


def _masked(manifest: Manifest | None, values: dict) -> dict:
    if manifest is None:
        return {key: {"hidden": True} for key in values}
    return {key: ({"configured": bool(value)} if key in manifest.config and manifest.config[key].type == "secret"
                  else {"value": value}) for key, value in values.items()}


def _available(saved: HostConfig) -> tuple[dict[str, list[dict]], list[str]]:
    found, errors = discover([] if saved.plugins is None else saved.plugins.paths)
    available = {}
    for name, directories in found.items():
        entries = []
        for directory in directories:
            try:
                manifest = read_manifest(directory)
                entries.append({"directory": str(directory), "error": None, "version": manifest.version,
                                "description": manifest.description, "authors": manifest.authors,
                                "license": manifest.license, "repository": manifest.repository,
                                "homepage": manifest.homepage, "fields": _field_info(manifest)})
            except (OSError, ValueError) as error:
                entries.append({"directory": str(directory), "error": f"{type(error).__name__}: {error}"})
        available[name] = entries
    return available, errors


def _manifest(saved: HostConfig, name: str) -> Manifest:
    found, _ = discover([] if saved.plugins is None else saved.plugins.paths)
    directories = found.get(name, [])
    if len(directories) != 1:
        raise ValueError(f"插件 {name} 未找到" if not directories else
                         f"插件 {name} 在多个目录出现：{[str(item) for item in directories]}")
    return read_manifest(directories[0])


def register_host_plugins(app: FastAPI, *, root: Path, runtime: NetworkRuntime, running: HostConfig,
                          user: Callable[[Request], str], write_lock: asyncio.Lock) -> None:
    def state(plugin_state: dict) -> dict:
        saved = _read_saved(root)
        _, raw = _read_root(root)
        available, errors = _available(saved)
        configured = {} if saved.plugins is None else saved.plugins.configured
        manifests = {}
        for name in configured:
            entries = available.get(name, [])
            manifests[name] = (read_manifest(Path(entries[0]["directory"]))
                               if len(entries) == 1 and entries[0]["error"] is None else None)
        raw_plugins = raw.get("plugins") or {}
        return {
            "running": plugin_state,
            "available": available, "discovery_errors": errors,
            "saved": {"paths": raw_plugins.get("paths", []),
                      "data_directory": raw_plugins.get("data_directory", "plugin-data"),
                      "plugins": {name: _masked(manifests[name], values) for name, values in configured.items()}},
            "scenes": {scene: {"saved": saved.scenes[scene].plugins if scene in saved.scenes else None,
                               "running": settings.plugins}
                       for scene, settings in running.scenes.items()},
            "restart_required": saved.plugins != running.plugins or any(
                scene not in saved.scenes or saved.scenes[scene].plugins != settings.plugins
                for scene, settings in running.scenes.items()),
        }

    async def snapshot() -> dict:
        plugin_state = ({"plugins": [], "discovery_errors": []} if runtime.plugins is None
                        else runtime.plugins.state())
        for item in plugin_state["plugins"]:
            item["model_calls"] = runtime.store.plugin_calls(item["name"])
        return await asyncio.to_thread(state, plugin_state)

    async def save(edit: Callable[[dict, HostConfig], None]) -> dict:
        async with write_lock:
            try:
                path, temporary, _ = await asyncio.to_thread(_prepare, root, edit)
                try:
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
                return await snapshot()
            except (ValueError, OSError) as error:
                raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                    f"{type(error).__name__}: {error}") from error

    @app.get("/api/host/plugins")
    async def plugins(_: str = Depends(user)):
        async with write_lock:
            try:
                return await snapshot()
            except (ValueError, OSError) as error:
                raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                    f"{type(error).__name__}: {error}") from error

    @app.put("/api/host/plugins/{name}")
    async def put_plugin(name: str, request: Request, _: str = Depends(user)):
        if PLUGIN_NAME.fullmatch(name) is None or name in PLUGIN_RESERVED:
            raise HTTPException(404, "不是有效的插件名")
        change = await _body(request, PluginChange)

        def edit(source: dict, saved: HostConfig) -> None:
            plugins = source.setdefault("plugins", {})
            if not change.enabled:
                if name not in plugins:
                    raise ValueError(f"插件 {name} 本来就没有加载")
                del plugins[name]
                return
            manifest = _manifest(saved, name)
            previous = plugins.get(name, {})
            values = {}
            for key, value in change.config.items():
                if key not in manifest.config:
                    raise ValueError(f"插件 {name} 没有配置项 {key}")
                if value is None and manifest.config[key].type == "secret":
                    if key not in previous:
                        raise ValueError(f"{key} 尚未保存过，不能留空保持原值")
                    value = previous[key]
                values[key] = value
            try:
                manifest.values_model().model_validate(values)
            except ValidationError as error:
                raise ValueError(redact_values(f"plugins.{name}: {error}", manifest, values)) from None
            plugins[name] = values

        return await save(edit)

    @app.put("/api/host/scenes/{scene}/plugins")
    async def put_scene_plugins(scene: str, request: Request, _: str = Depends(user)):
        if scene not in running.scenes:
            raise HTTPException(404, "当前宿主未配置这一场景")
        change = await _body(request, ScenePlugins)

        def edit(source: dict, saved: HostConfig) -> None:
            if scene not in source["scenes"]:
                raise ValueError(f"根配置已不包含场景 {scene}")
            source["scenes"][scene]["plugins"] = change.plugins

        return await save(edit)

    @app.put("/api/host/plugin-paths")
    async def put_paths(request: Request, _: str = Depends(user)):
        change = await _body(request, PluginPaths)

        def edit(source: dict, saved: HostConfig) -> None:
            plugins = source.setdefault("plugins", {})
            plugins["paths"] = change.paths
            plugins["data_directory"] = change.data_directory

        return await save(edit)
