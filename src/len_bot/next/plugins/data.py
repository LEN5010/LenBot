"""A plugin's data version: recorded beside its data, migrated before start, put back if migration fails."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
from pathlib import Path
import shutil
import time

MARKER = '.lenbot-data.json'


def recorded_version(data_dir: Path) -> int | None:
    """The version existing data was written with; None for a new, empty data directory.

    Data written before versions were recorded counts as version 1.
    """
    marker = data_dir / MARKER
    if marker.exists():
        value = json.loads(marker.read_text(encoding='utf-8'))
        version = value.get('data_version') if isinstance(value, dict) else None
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError(f'{marker} 的 data_version 不是正整数：{value!r}')
        return version
    return None if not any(data_dir.iterdir()) else 1


def write_version(data_dir: Path, version: int) -> None:
    marker = data_dir / MARKER
    temporary = marker.with_name(marker.name + '.tmp')
    temporary.write_text(json.dumps({'data_version': version}) + '\n', encoding='utf-8')
    temporary.replace(marker)


def _restore(backup: Path, data_dir: Path) -> None:
    shutil.rmtree(data_dir)
    shutil.copytree(backup, data_dir, symlinks=True)


async def prepare_data(name: str, target: int, data_dir: Path, backups: Path,
                       migrate: Callable[[int], Awaitable[None]]) -> dict | None:
    """Bring ``data_dir`` to ``target`` before the plugin starts; returns what was migrated, if anything."""
    current = await asyncio.to_thread(recorded_version, data_dir)
    if current is None:
        await asyncio.to_thread(write_version, data_dir, target)
        return None
    if current == target:
        return None
    if current > target:
        raise ValueError(f'插件 {name} 的数据版本 {current} 比当前插件声明的 data_version {target} 新；'
                         '旧版本插件不能读新数据，回到旧版本需要同时恢复升级前的插件数据备份')
    backup = backups / f'{name}-v{current}-{time.strftime("%Y%m%d-%H%M%S", time.gmtime())}'
    backups.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.copytree, data_dir, backup, symlinks=True)
    try:
        await migrate(current)
        await asyncio.to_thread(write_version, data_dir, target)
    except BaseException as error:
        try:
            await asyncio.to_thread(_restore, backup, data_dir)
        except BaseException as restore_error:
            error.add_note(f'插件数据恢复也失败：{type(restore_error).__name__}: {restore_error}；备份在 {backup}')
        else:
            error.add_note(f'插件数据已恢复到迁移前（版本 {current}）；备份保留在 {backup}')
        raise
    return {'from_version': current, 'to_version': target, 'backup': str(backup)}


def restore_backup(name: str, data_dir: Path, backups: Path, version: int) -> Path | None:
    """Put back the newest backup taken at ``version`` when the data has since been migrated past it.

    Used when a new plugin version migrated the data but then failed, so the previous
    source can read its data again. Returns the backup used, or None if nothing was newer.
    """
    current = recorded_version(data_dir)
    if current is None or current <= version:
        return None
    taken = sorted(backups.glob(f'{name}-v{version}-*'))
    if not taken:
        raise ValueError(f'插件 {name} 的数据已是版本 {current}，{backups} 里没有版本 {version} 的备份')
    _restore(taken[-1], data_dir)
    return taken[-1]
