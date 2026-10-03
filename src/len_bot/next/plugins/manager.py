"""User-requested plugin changes; root persistence and one-plugin runtime refresh."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
import shutil

from ..chat.tools import tool_catalog
from ..config import HostConfig
from ..configuration.plugin import PluginSettings
from ..panel.routes.settings import _prepare, _read_saved
from ..runtime.network import NetworkRuntime
from .host import PluginHost
from .install import PluginInstaller


class PluginManager:
    def __init__(self, root: Path, runtime: NetworkRuntime, running: HostConfig, write_lock: asyncio.Lock):
        self.root, self.runtime, self.running = root, runtime, running
        self.write_lock = write_lock
        # Git/pip share one environment; serialize those explicit changes, not chat or settings reads.
        self.lock = asyncio.Lock()
        self.installer = PluginInstaller(root)

    async def save(self, edit: Callable[[dict, HostConfig], None]) -> HostConfig:
        async with self.write_lock:
            path, temporary, candidate = await asyncio.to_thread(_prepare, self.root, edit)
            try:
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            return candidate

    def _binding(self, name: str, saved: HostConfig) -> None:
        current, selected = self.running.plugins, saved.plugins
        if current is None:
            current = PluginSettings(paths=selected.paths, data_directory=selected.data_directory)
        values = deepcopy(current.configured)
        disabled = [value for value in current.disabled if value != name]
        if selected is not None and name in selected.configured:
            values[name] = deepcopy(selected.configured[name])
            if name in selected.disabled:
                disabled.append(name)
        else:
            values.pop(name, None)
        self.running.plugins = PluginSettings.model_validate({
            'paths': current.paths if selected is None else selected.paths,
            'data_directory': current.data_directory, 'disabled': disabled, **values})
        for scene, settings in self.running.scenes.items():
            names = list(settings.plugins)
            if scene in saved.scenes and name in saved.scenes[scene].plugins:
                if name not in names:
                    names.append(name)
            else:
                names = [value for value in names if value != name]
            settings.plugins = names
            self.runtime.chats[scene].config.plugins = list(names)

    async def apply(self, name: str, saved: HostConfig) -> None:
        if self.runtime.plugins is None:
            self.runtime.plugins = PluginHost(self.running, core_tools={
                item['function']['name'] for item in tool_catalog(platform=True)})
            self.runtime.plugins.bind(self.runtime)
            self.runtime.plugins.on_update = self.runtime.notify
        try:
            await self.runtime.plugins.reload(name, saved)
            self._binding(name, saved)
        finally:
            self.runtime.refresh_external_tools()

    async def reload(self, name: str) -> None:
        saved = await asyncio.to_thread(_read_saved, self.root)
        if saved.plugins is None or name not in saved.plugins.configured:
            raise ValueError(f'根配置没有插件 {name}，请先配置并启用')
        await self.apply(name, saved)

    async def install(self, url: str, *, ref: str | None = None) -> dict:
        saved = await asyncio.to_thread(_read_saved, self.root)
        directory, manifest, output = await self.installer.install(
            url, [] if saved.plugins is None else saved.plugins.paths, ref=ref)
        name = manifest.name

        def register(source: dict, previous: HostConfig) -> None:
            if previous.plugins is None:
                source['plugins'] = {}
            plugins = source['plugins']
            paths = [] if previous.plugins is None else previous.plugins.paths
            if self.installer.directory not in paths:
                plugins.setdefault('paths', []).append('plugins')
            plugins[name] = {}
            plugins.setdefault('disabled', []).append(name)

        saved = await self.save(register)
        await self.apply(name, saved)
        try:
            output += '\n' + await self.installer.dependencies(manifest)
        except Exception as error:
            record = self.runtime.plugins.plugins[name]
            record.status, record.error = 'failed', self.runtime.plugins.report_error(name, '安装依赖', error)
            raise
        needs_config = any('default' not in field.model_fields_set for field in manifest.config.values())
        if not needs_config:
            def enable(source: dict, _: HostConfig) -> None:
                source['plugins']['disabled'].remove(name)
            saved = await self.save(enable)
            await self.apply(name, saved)
        return {'name': name, 'directory': str(directory), 'needs_config': needs_config, 'output': output.strip(),
                'source': await self.installer.details(name)}

    async def update(self, name: str, *, ref: str | None = None) -> dict:
        self.installer.managed_path(name)
        if self.runtime.plugins is not None and name in self.runtime.plugins.plugins:
            try:
                await self.runtime.plugins.stop_plugin(name)
            finally:
                self.runtime.refresh_external_tools()
        try:
            manifest, output = await self.installer.update(name, ref=ref)
        except Exception as error:
            if self.runtime.plugins is not None and name in self.runtime.plugins.plugins:
                record = self.runtime.plugins.plugins[name]
                record.status, record.error = 'failed', self.runtime.plugins.report_error(name, '更新', error)
            raise
        await self.reload(name)
        return {'name': name, 'version': manifest.version, 'output': output,
                'source': await self.installer.details(name)}

    async def uninstall(self, name: str) -> None:
        self.installer.managed_path(name)
        if self.runtime.plugins is not None and name in self.runtime.plugins.plugins:
            try:
                await self.runtime.plugins.stop_plugin(name)
            finally:
                self.runtime.refresh_external_tools()

        def remove(source: dict, _: HostConfig) -> None:
            source['plugins'].pop(name, None)
            source['plugins']['disabled'] = [item for item in source['plugins'].get('disabled', []) if item != name]
            for settings in source['scenes'].values():
                settings['plugins'] = [item for item in settings.get('plugins', []) if item != name]

        saved = await self.save(remove)
        await self.apply(name, saved)
        await self.installer.uninstall(name)

    async def delete_data(self, name: str) -> str:
        saved = await asyncio.to_thread(_read_saved, self.root)
        if saved.plugins is None:
            raise ValueError('根配置没有插件数据目录')
        if name in saved.plugins.configured and name not in saved.plugins.disabled:
            raise ValueError('先停用插件，再单独删除其数据')
        if self.runtime.plugins is not None and name in self.runtime.plugins.plugins:
            record = self.runtime.plugins.plugins[name]
            if record.status in {'loaded', 'running'} or record.instance is not None:
                raise ValueError('插件资源仍在运行，先停止后再删除数据')
        directory = saved.plugins.data_directory / name
        if directory.resolve() != directory:
            raise ValueError('删除插件数据不穿过目录链接')
        await asyncio.to_thread(shutil.rmtree, directory)
        return str(directory)
