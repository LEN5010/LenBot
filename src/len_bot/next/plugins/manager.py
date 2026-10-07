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
from ..configuration.editing import _read_saved, save_config
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..runtime.network import NetworkRuntime
from .data import restore_backup
from .host import PluginHost
from .install import PluginInstaller
from .manifest import Manifest, discover, read_manifest


def plugin_manifest(saved: HostConfig, name: str, installer: PluginInstaller | None = None) -> Manifest:
    if installer is not None and (installer.records / (name + '.json')).is_file():
        record = installer.read(name)
        if record.candidate is not None:
            return read_manifest(installer.candidates / name)
    found, _ = discover([] if saved.plugins is None else saved.plugins.paths)
    directories = found.get(name, [])
    if len(directories) != 1:
        raise ValueError(f"插件 {name} 未找到" if not directories else
                         f"插件 {name} 在多个目录出现：{[str(item) for item in directories]}")
    return read_manifest(directories[0])


class PluginManager:
    def __init__(self, root: Path, runtime: NetworkRuntime, running: HostConfig, write_lock: asyncio.Lock):
        self.root, self.runtime, self.running = root, runtime, running
        self.write_lock = write_lock
        # Git/pip share one environment; serialize those explicit changes, not chat or settings reads.
        self.lock = asyncio.Lock()
        self.installer = PluginInstaller(root)

    async def save(self, edit: Callable[[dict, HostConfig], None]) -> HostConfig:
        async with self.write_lock:
            return await asyncio.to_thread(save_config, self.root, self.running, edit)

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
        metadata = self.installer.records / (name + '.json')
        if metadata.is_file():
            record = self.installer.read(name)
            if record.candidate is not None:
                if (saved.plugins is None or name in saved.plugins.disabled) and self.runtime.plugins is not None:
                    if name in self.runtime.plugins.plugins:
                        await self.runtime.plugins.stop_plugin(name)
                        self.runtime.refresh_external_tools()
                return
        if saved.plugins is not None and name in saved.plugins.configured and name not in saved.plugins.disabled:
            if plugin_manifest(saved, name).reload == 'host':
                return
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
        metadata = self.installer.records / (name + '.json')
        if metadata.is_file() and self.installer.read(name).candidate is not None:
            raise ValueError('先应用或取消候选版本，再重载已安装源码')
        if plugin_manifest(saved, name).reload == 'host':
            raise ValueError('这个插件声明需要宿主重启，请使用重启入口')
        await self.apply(name, saved)

    async def _register_candidate(self, manifest: Manifest, output: str) -> dict:
        name = manifest.name

        def register(source: dict, previous: HostConfig) -> None:
            if previous.plugins is None:
                source['plugins'] = {}
            plugins = source['plugins']
            paths = [] if previous.plugins is None else previous.plugins.paths
            if self.installer.directory not in paths:
                plugins.setdefault('paths', []).append('plugins')
            if name not in plugins:
                plugins[name] = {}
                plugins.setdefault('disabled', []).append(name)

        self.installer.directory.mkdir(exist_ok=True)
        await self.save(register)
        return {'name': name, 'version': manifest.version,
                'needs_config': any('default' not in field.model_fields_set for field in manifest.config.values()),
                'output': output.strip(), 'source': await self.installer.details(name)}

    async def install(self, url: str, *, ref: str | None = None, switch_source: bool = False) -> dict:
        saved = await asyncio.to_thread(_read_saved, self.root)
        manifest, _, output = await self.installer.prepare_git(
            url, [] if saved.plugins is None else saved.plugins.paths, ref=ref, switch_source=switch_source)
        return await self._register_candidate(manifest, output)

    async def import_zip(self, data: bytes, filename: str, *, switch_source: bool = False) -> dict:
        saved = await asyncio.to_thread(_read_saved, self.root)
        manifest, _, output = await self.installer.prepare_zip(
            data, filename, [] if saved.plugins is None else saved.plugins.paths, switch_source=switch_source)
        return await self._register_candidate(manifest, output)

    async def update(self, name: str, *, ref: str | None = None) -> dict:
        saved = await asyncio.to_thread(_read_saved, self.root)
        manifest, _, output = await self.installer.prepare_update(
            name, [] if saved.plugins is None else saved.plugins.paths, ref=ref)
        return await self._register_candidate(manifest, output)

    async def apply_candidate(self, name: str) -> dict:
        """Apply a prepared version; if it fails to load, start or migrate its data, the previous source returns."""
        saved = await asyncio.to_thread(_read_saved, self.root)
        record = self.installer.read(name)
        candidate_values = self.installer.candidate_values(name)
        values = saved.plugins.configured[name] if candidate_values is None else candidate_values.values
        applied = False
        try:
            await self.installer.check_apply(name, values, saved.scenes)
            if record.application == 'host':
                record.requested, record.error = True, None
                self.installer.write(record)
                return {'name': name, 'restart_required': True}
            if self.runtime.plugins is not None and name in self.runtime.plugins.plugins:
                try:
                    await self.runtime.plugins.stop_plugin(name)
                finally:
                    self.runtime.refresh_external_tools()
            async with self.write_lock:
                await asyncio.to_thread(self.installer.apply_files, name)
            applied = True
            saved = await asyncio.to_thread(_read_saved, self.root)
            await self.apply(name, saved)
            loaded = self.runtime.plugins.plugins[name]
            if loaded.error is not None:
                raise RuntimeError(loaded.error)
        except Exception as error:
            applied = applied or self.installer.read(name).installed != record.installed
            if applied and self.installer.read(name).previous is not None:
                try:
                    result = await self.rollback(name, restore_data=True)
                except Exception as rollback_error:
                    error.add_note(f'回到上一版本也失败：{type(rollback_error).__name__}: {rollback_error}')
                else:
                    restored = result['restored_data']
                    error.add_note('已回到上一版本源码' + (f'，插件数据已从 {restored} 恢复' if restored else ''))
            self.installer.failed(name, error)
            raise
        return {'name': name, 'restart_required': False}

    async def rollback(self, name: str, *, restore_data: bool = False) -> dict:
        """Return to the source replaced by the last apply.

        Source and configuration return together. By default data migrated by the newer version stays, and the older plugin refuses it until
        its backup is restored by hand. ``restore_data`` puts that backup back; the automatic rollback
        after a failed apply uses it, because the failed version never wrote anything worth keeping.
        """
        saved = await asyncio.to_thread(_read_saved, self.root)
        if self.runtime.plugins is not None and name in self.runtime.plugins.plugins:
            try:
                await self.runtime.plugins.stop_plugin(name)
            finally:
                self.runtime.refresh_external_tools()
        async with self.write_lock:
            await asyncio.to_thread(self.installer.rollback_files, name)
        saved = await asyncio.to_thread(_read_saved, self.root)
        restored = None
        if restore_data and saved.plugins is not None:
            data_dir = saved.plugins.data_directory / name
            version = read_manifest(self.installer.directory / name).data_version
            if data_dir.is_dir():
                restored = await asyncio.to_thread(restore_backup, name, data_dir, data_dir.parent / '.backups', version)
        await self.apply(name, saved)
        return {'name': name, 'source': await self.installer.details(name), 'restored_data': restored and str(restored)}

    async def cancel(self, name: str) -> dict:
        if self.installer.read(name).installed is None:
            await self.uninstall(name)
            return {'name': name}
        await self.installer.cancel(name)
        saved = await asyncio.to_thread(_read_saved, self.root)
        if self.installer.read(name).installed is not None:
            await self.apply(name, saved)
        return {'name': name}

    async def uninstall(self, name: str) -> None:
        self.installer.read(name)
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
