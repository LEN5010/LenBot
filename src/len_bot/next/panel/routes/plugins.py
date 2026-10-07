"""Panel access to loaded plugins and their saved root configuration (M14)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ...configuration.plugin import PLUGIN_NAME, PLUGIN_RESERVED, PluginCatalogSettings
from ...plugins.catalog import CatalogView, PluginCatalog
from ...config import HostConfig, _read_root
from .settings import _body
from ...configuration.editing import _read_saved
from ...runtime.network import NetworkRuntime
from ...plugins.manifest import ConfigField, ConfigItem, Manifest, discover, read_manifest, redact_values
from ...plugins.manager import plugin_manifest
from ...plugins.install import repository_url, revision_ref
from ...plugins.store import PluginStore


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


class PluginVersion(BaseModel):
    model_config = STRICT
    ref: str | None = None

    @field_validator('ref')
    @classmethod
    def selected_ref(cls, value: str | None) -> str | None:
        return None if value is None else revision_ref(value)


class PluginInstall(PluginVersion):
    url: str
    switch_source: bool = False

    @field_validator('url')
    @classmethod
    def repository(cls, value: str) -> str:
        return repository_url(value)


def _field_info(manifest: Manifest) -> list[dict]:
    def field(key: str, item: ConfigItem) -> dict:
        choices = item.choices()
        return {"key": key, "type": item.type, "description": item.description,
                "label": item.label, "placeholder": item.placeholder, "multiline": item.multiline,
                "group": item.group if isinstance(item, ConfigField) else None,
                "required": "default" not in item.model_fields_set,
                "default": None if item.type == "secret" else item.default,
                "options": None if choices is None else [choice.model_dump() for choice in choices],
                "minimum": item.minimum, "maximum": item.maximum,
                "fields": ([field(name, child) for name, child in item.fields.items()]
                           if isinstance(item, ConfigField) else [])}
    return [field(key, item) for key, item in manifest.config.items()]


def _masked(manifest: Manifest | None, values: dict, *, installed: Sequence[Manifest] = ()) -> dict:
    if manifest is None:
        return {key: {"hidden": True} for key in values}
    manifests = [manifest, *installed]
    known = {key for item in manifests for key in item.config}
    secrets = {key for item in manifests for key, field in item.config.items() if field.type == "secret"}
    return {key: ({"configured": bool(value)} if key in secrets else
                  {"value": value} if key in known else {"hidden": True}) for key, value in values.items()}


def _entry(directory: Path) -> dict:
    try:
        manifest = read_manifest(directory)
        return {"directory": str(directory), "error": None, "version": manifest.version,
                "description": manifest.description, "authors": manifest.authors,
                "license": manifest.license, "repository": manifest.repository,
                "homepage": manifest.homepage, "dependencies": manifest.dependencies,
                "requires_lenbot": manifest.requires_lenbot, "requires_python": manifest.requires_python,
                "platforms": manifest.platforms, "reload": manifest.reload,
                "fields": _field_info(manifest)}
    except (OSError, ValueError) as error:
        return {"directory": str(directory), "error": f"{type(error).__name__}: {error}"}


def _available(saved: HostConfig) -> tuple[dict[str, list[dict]], list[str]]:
    found, errors = discover([] if saved.plugins is None else saved.plugins.paths)
    return {name: [_entry(directory) for directory in directories]
            for name, directories in found.items()}, errors


def configured_values(change: PluginChange, manifest: Manifest, previous: dict, scenes) -> dict:
    values = {}
    for key, value in change.config.items():
        if key not in manifest.config:
            raise ValueError(f'插件 {manifest.name} 没有配置项 {key}')
        if value is None and manifest.config[key].type == 'secret':
            if key not in previous:
                raise ValueError(f'{key} 尚未保存过，不能留空保持原值')
            value = previous[key]
        values[key] = value
    try:
        manifest.values_model(scenes).model_validate(values)
    except ValidationError as error:
        raise ValueError(redact_values(f'plugins.{manifest.name}: {error}', manifest, values)) from None
    return values


def register_host_plugins(app: FastAPI, *, root: Path, runtime: NetworkRuntime, running: HostConfig,
                          user: Callable[[Request], str], write_lock: asyncio.Lock) -> None:
    manager = runtime.management.plugins
    catalog = PluginCatalog()

    def require_name(name: str) -> None:
        if PLUGIN_NAME.fullmatch(name) is None or name in PLUGIN_RESERVED:
            raise HTTPException(404, '不是有效的插件名')

    def state(plugin_state: dict) -> dict:
        saved = _read_saved(root)
        _, raw = _read_root(root)
        available, errors = _available(saved)
        installed = {name: [read_manifest(Path(entry['directory'])) for entry in entries
                            if entry['error'] is None] for name, entries in available.items()}
        pending = manager.installer.pending()
        for record in pending:
            available[record.name] = [_entry(manager.installer.candidates / record.name)]
        for name, entries in available.items():
            for entry in entries:
                entry['managed'] = (manager.installer.records / (name + '.json')).is_file()
        configured = {} if saved.plugins is None else dict(saved.plugins.configured)
        disabled = [] if saved.plugins is None else list(saved.plugins.disabled)
        for record in pending:
            staged = manager.installer.candidate_values(record.name)
            if staged is not None:
                configured[record.name] = staged.values
                disabled = [name for name in disabled if name != record.name]
                if not staged.enabled:
                    disabled.append(record.name)
        manifests = {}
        for name in configured:
            entries = available.get(name, [])
            manifests[name] = (read_manifest(Path(entries[0]["directory"]))
                               if len(entries) == 1 and entries[0]["error"] is None else None)
        raw_plugins = raw.get("plugins") or {}
        return {
            "running": plugin_state,
            "available": available, "discovery_errors": errors,
            "saved": {"paths": raw_plugins.get("paths", ["plugins"]),
                      "data_directory": raw_plugins.get("data_directory", "plugins/.data"),
                      "disabled": disabled,
                      "plugins": {name: _masked(manifests[name], values, installed=installed.get(name, []))
                                  for name, values in configured.items()}},
            "retained_data": ([] if saved.plugins is None or not saved.plugins.data_directory.is_dir() else
                              [{'name': path.name, 'directory': str(path)}
                               for path in sorted(saved.plugins.data_directory.iterdir()) if path.is_dir()
                               and PLUGIN_NAME.fullmatch(path.name) and path.name not in configured]),
            "scene_choices": list(saved.scenes),
            "scenes": {scene: {"saved": saved.scenes[scene].plugins if scene in saved.scenes else None,
                               "running": settings.plugins}
                       for scene, settings in running.scenes.items()},
            "restart_required": any(item.requested for item in pending) or saved.plugins != running.plugins or any(
                scene not in saved.scenes or saved.scenes[scene].plugins != settings.plugins
                for scene, settings in running.scenes.items()),
        }

    async def snapshot() -> dict:
        plugin_state = ({"plugins": [], "discovery_errors": []} if runtime.plugins is None
                        else runtime.plugins.state())
        for item in plugin_state["plugins"]:
            item["model_calls"] = PluginStore(runtime.store).plugin_calls(item["name"])
        result = await asyncio.to_thread(state, plugin_state)
        for name, entries in result['available'].items():
            for entry in entries:
                entry['source'] = None
                if entry['managed']:
                    try:
                        entry['source'] = await manager.installer.details(name)
                    except (ValueError, OSError, RuntimeError) as error:
                        entry['error'] = f"{entry['error'] or ''}\n{type(error).__name__}: {error}".strip()
        return result

    @app.get('/api/host/plugin-catalog', response_model=CatalogView)
    async def read_catalog(_: str = Depends(user)):
        try:
            saved = await asyncio.to_thread(_read_saved, root)
            return catalog.view(saved.plugin_catalog)
        except (ValueError, OSError) as error:
            raise HTTPException(422, str(error)) from error

    @app.put('/api/host/plugin-catalog', response_model=CatalogView)
    async def choose_catalog(request: Request, _: str = Depends(user)):
        change = await _body(request, PluginCatalogSettings)
        try:
            candidate = await manager.save(lambda source, _: source.update(plugin_catalog=change.model_dump()))
            running.plugin_catalog = candidate.plugin_catalog
            return catalog.view(candidate.plugin_catalog)
        except (ValueError, OSError) as error:
            raise HTTPException(422, str(error)) from error

    @app.post('/api/host/plugin-catalog/refresh', response_model=CatalogView)
    async def refresh_catalog(_: str = Depends(user)):
        try:
            saved = await asyncio.to_thread(_read_saved, root)
            return await catalog.refresh(saved.plugin_catalog)
        except (ValueError, OSError, httpx.HTTPError, httpx.InvalidURL) as error:
            raise HTTPException(502, str(error)) from error

    async def save(edit: Callable[[dict, HostConfig], None], names: Sequence[str] = ()) -> dict:
        async with manager.lock:
            try:
                saved = await manager.save(edit)
                for name in names:
                    await manager.apply(name, saved)
                return await snapshot()
            except (ValueError, OSError, RuntimeError) as error:
                raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                    f"{type(error).__name__}: {error}") from error

    async def operate(action) -> dict:
        async with manager.lock:
            try:
                result = await action()
                return {**await snapshot(), 'operation': result}
            except (ValueError, OSError, RuntimeError) as error:
                raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                    f'{type(error).__name__}: {error}') from error

    @app.post('/api/host/plugins/install')
    async def install(request: Request, _: str = Depends(user)):
        change = await _body(request, PluginInstall)
        return await operate(lambda: manager.install(change.url, ref=change.ref, switch_source=change.switch_source))

    @app.post('/api/host/plugins/zip')
    async def import_zip(file: UploadFile = File(...), switch_source: bool = Form(False), _: str = Depends(user)):
        if file.filename is None:
            raise HTTPException(422, 'ZIP 文件需要文件名')
        from ...plugins.install import MAX_ZIP_BYTES
        data = await file.read(MAX_ZIP_BYTES + 1)
        if len(data) > MAX_ZIP_BYTES:
            raise HTTPException(413, '插件 ZIP 上传不能超过 200 MiB')
        return await operate(lambda: manager.import_zip(data, file.filename, switch_source=switch_source))

    @app.post('/api/host/plugins/{name}/apply')
    async def apply_candidate(name: str, _: str = Depends(user)):
        require_name(name)
        return await operate(lambda: manager.apply_candidate(name))

    @app.post('/api/host/plugins/{name}/cancel')
    async def cancel(name: str, _: str = Depends(user)):
        require_name(name)
        return await operate(lambda: manager.cancel(name))

    @app.post('/api/host/plugins/{name}/rollback')
    async def rollback(name: str, _: str = Depends(user)):
        require_name(name)
        return await operate(lambda: manager.rollback(name))

    @app.post('/api/host/plugins/{name}/reload')
    async def reload(name: str, _: str = Depends(user)):
        require_name(name)
        return await operate(lambda: manager.reload(name))

    @app.post('/api/host/plugins/{name}/update')
    async def update(name: str, request: Request, _: str = Depends(user)):
        require_name(name)
        change = await _body(request, PluginVersion)
        return await operate(lambda: manager.update(name, ref=change.ref))

    @app.delete('/api/host/plugins/{name}')
    async def uninstall(name: str, _: str = Depends(user)):
        require_name(name)
        return await operate(lambda: manager.uninstall(name))

    @app.delete('/api/host/plugin-data/{name}')
    async def delete_data(name: str, _: str = Depends(user)):
        require_name(name)
        return await operate(lambda: manager.delete_data(name))

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
        require_name(name)
        change = await _body(request, PluginChange)

        metadata = manager.installer.records / (name + '.json')
        if metadata.exists() and manager.installer.read(name).candidate is not None:
            async def stage_values():
                saved = await asyncio.to_thread(_read_saved, root)
                manifest = plugin_manifest(saved, name, manager.installer)
                staged = manager.installer.candidate_values(name)
                previous = saved.plugins.configured[name] if staged is None else staged.values
                values = configured_values(change, manifest, previous, saved.scenes) if change.enabled else previous
                await asyncio.to_thread(manager.installer.save_candidate_values, name, values, change.enabled)
                if not change.enabled:
                    def disable(source, _):
                        disabled = source['plugins'].setdefault('disabled', [])
                        if name not in disabled:
                            disabled.append(name)
                    saved = await manager.save(disable)
                    await manager.apply(name, saved)
                return {'name': name, 'candidate_config_saved': True}
            return await operate(stage_values)

        def edit(source: dict, saved: HostConfig) -> None:
            if saved.plugins is None:
                source['plugins'] = {}
            plugins = source['plugins']
            if not change.enabled:
                if name not in plugins:
                    raise ValueError(f"插件 {name} 本来就没有加载")
                disabled = plugins.setdefault('disabled', [])
                if name not in disabled:
                    disabled.append(name)
                return
            manifest = plugin_manifest(saved, name, manager.installer)
            previous = plugins.get(name, {})
            values = configured_values(change, manifest, previous, saved.scenes)
            plugins[name] = values
            plugins['disabled'] = [item for item in plugins.get('disabled', []) if item != name]

        return await save(edit, [name])

    @app.put("/api/host/scenes/{scene}/plugins")
    async def put_scene_plugins(scene: str, request: Request, _: str = Depends(user)):
        if scene not in running.scenes:
            raise HTTPException(404, "当前宿主未配置这一场景")
        change = await _body(request, ScenePlugins)
        changed: list[str] = []

        def edit(source: dict, saved: HostConfig) -> None:
            if scene not in source["scenes"]:
                raise ValueError(f"根配置已不包含场景 {scene}")
            for name in change.plugins:
                record_path = manager.installer.records / (name + '.json')
                if record_path.is_file() and manager.installer.read(name).installed is None:
                    raise ValueError(f'插件 {name} 尚未应用源码，请先完成安装')
            changed.extend(sorted(set(saved.scenes[scene].plugins) ^ set(change.plugins)))
            source["scenes"][scene]["plugins"] = change.plugins

        return await save(edit, changed)

    @app.put("/api/host/plugin-paths")
    async def put_paths(request: Request, _: str = Depends(user)):
        change = await _body(request, PluginPaths)

        def edit(source: dict, saved: HostConfig) -> None:
            if saved.plugins is None:
                source['plugins'] = {}
            plugins = source['plugins']
            plugins["paths"] = change.paths
            plugins["data_directory"] = change.data_directory

        return await save(edit)
