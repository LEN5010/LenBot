"""Install explicitly selected packaged official plugins before the first host starts."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..plugins.catalog import CatalogEntry, PluginCatalog
from ..plugins.install import PluginInstaller, install_dependencies


def official_plugins() -> dict[str, CatalogEntry]:
    return {entry.name: entry for entry in PluginCatalog().builtin.entries
            if entry.repository.startswith('https://github.com/lendevs/')}


def plugin_choices(root: Path) -> list[dict]:
    installer = PluginInstaller(root)
    choices = []
    for entry in official_plugins().values():
        record = installer.read(entry.name) if (installer.records / (entry.name + '.json')).exists() else None
        choices.append({**entry.model_dump(), 'installed': record is not None and record.installed is not None,
                        'prepared': record is not None and record.candidate is not None,
                        'error': None if record is None else record.error})
    return choices


async def install_official(root: Path, entry: CatalogEntry) -> dict:
    installer = PluginInstaller(root)
    metadata = installer.records / (entry.name + '.json')
    if metadata.exists():
        record = installer.read(entry.name)
        if record.installed is not None:
            installer.managed_path(entry.name)
            return {'name': entry.name, 'installed': True, 'version': record.installed.version}
    try:
        async with asyncio.timeout(180):
            manifest, record, _ = await installer.prepare_git(entry.repository, [installer.directory], ref=entry.ref)
            if manifest.name != entry.name:
                raise ValueError(f'目录条目 {entry.name} 与仓库清单 {manifest.name} 不一致')
            await install_dependencies(manifest.dependencies)
            # Installation does not activate a plugin. Required service credentials
            # are filled in the normal panel before enabling it for a scene.
            await asyncio.to_thread(installer.apply_files, entry.name)
            return {'name': entry.name, 'installed': True, 'version': manifest.version}
    except Exception as error:
        if metadata.exists():
            installer.failed(entry.name, error)
        raise
