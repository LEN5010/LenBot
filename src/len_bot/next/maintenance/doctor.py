"""Read-only instance check: configuration, data formats, database integrity and installed plugins.

Run from the instance directory; safe while the bot is running because every
database is opened read-only. Prints one JSON line per check and exits 1 when
any check fails. Components that are not configured report ``disabled``.
"""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sys

from ..config import HostConfig, load_host_config
from ..memory.jobs import FORMAT_VERSION as MEMORY_JOBS_FORMAT
from ..memory.local import FORMAT_VERSION as LOCAL_INDEX_FORMAT, _APPLICATION_ID as LOCAL_INDEX_APPLICATION, _INDEX_NAME
from ..plugins.manifest import discover, read_manifest
from ..storage.sqlite import connect
from ..storage.store import FORMAT_VERSION as BUSINESS_FORMAT
from ..runtime.logs import run_maintenance

BUSINESS_APPLICATION = 0x4C424E31
MEMORY_JOBS_APPLICATION = 0x4C424D4A


def _database(path: Path, application: int, format_version: int, migrate: str,
              relations: tuple[tuple[str, str], ...] = ()) -> dict:
    if not path.exists():
        return {'status': 'disabled', 'detail': f'{path} 尚未创建'}
    with closing(connect(path, readonly=True)) as db:
        actual = (db.execute('PRAGMA application_id').fetchone()[0], db.execute('PRAGMA user_version').fetchone()[0])
        if actual != (application, format_version):
            return {'status': 'error', 'detail': f'{path} 格式 {actual!r}，当前需要 {(application, format_version)!r}；'
                                                 f'停机执行 python -m len_bot.next.maintenance.{migrate}'}
        problems = [row[0] for row in db.execute('PRAGMA quick_check').fetchall() if row[0] != 'ok']
        for name, query in relations:
            count = db.execute(query).fetchone()[0]
            if count:
                problems.append(f'{name}: {count}')
    if problems:
        return {'status': 'error', 'detail': f'{path}: {problems[:10]!r}'}
    return {'status': 'ok', 'detail': f'{path} 格式 {format_version}'}


BUSINESS_RELATIONS = (
    ('搜索索引指向不存在的消息', 'SELECT COUNT(*) FROM message_search s WHERE NOT EXISTS (SELECT 1 FROM messages m WHERE m.seq=s.rowid)'),
    ('消息图片指向不存在的消息', 'SELECT COUNT(*) FROM message_media mm WHERE NOT EXISTS (SELECT 1 FROM messages m WHERE m.seq=mm.message_seq)'),
    ('消息图片指向不存在的媒体', 'SELECT COUNT(*) FROM message_media mm WHERE NOT EXISTS (SELECT 1 FROM media m WHERE m.id=mm.media_id)'),
)


def _plugins(config: HostConfig) -> dict:
    if config.plugins is None:
        return {'status': 'disabled', 'detail': '未配置插件'}
    found, problems = discover(config.plugins.paths)
    for name in config.plugins.configured:
        directories = found.get(name, [])
        if len(directories) != 1:
            problems.append(f'{name}: 找到 {len(directories)} 个安装目录')
            continue
        try:
            read_manifest(directories[0])
        except ValueError as error:
            problems.append(f'{name}: {error}')
    for scene, settings in config.scenes.items():
        missing = sorted(set(settings.plugins) - set(config.plugins.configured))
        if missing:
            problems.append(f'{scene} 选用了未配置的插件 {missing}')
    if problems:
        return {'status': 'error', 'detail': problems}
    return {'status': 'ok', 'detail': sorted(config.plugins.configured)}


def check(root: Path) -> list[dict]:
    try:
        config = load_host_config(root)
    except ValueError as error:
        return [{'check': 'config', 'status': 'error', 'detail': str(error)}]
    results = [{'check': 'config', 'status': 'ok', 'detail': f'配置格式 {config.config_version}'}]
    database = config.database
    results.append({'check': 'business_database', **_database(
        database, BUSINESS_APPLICATION, BUSINESS_FORMAT, 'migrate', BUSINESS_RELATIONS)})
    results.append({'check': 'memory_jobs', **_database(
        database.with_name(database.name + '.memory.sqlite3'), MEMORY_JOBS_APPLICATION, MEMORY_JOBS_FORMAT,
        'migrate_memory_jobs')})
    if config.memory is None:
        results.append({'check': 'local_memory_index', 'status': 'disabled', 'detail': '未配置记忆'})
    else:
        index = config.memory.local.directory.expanduser().resolve() / _INDEX_NAME
        results.append({'check': 'local_memory_index', **_database(
            index, LOCAL_INDEX_APPLICATION, LOCAL_INDEX_FORMAT, 'migrate_local_memory')})
    results.append({'check': 'plugins', **_plugins(config)})
    return results


def main() -> None:
    results = check(Path.cwd())
    for item in results:
        print(json.dumps(item, ensure_ascii=False))
    if any(item['status'] == 'error' for item in results):
        sys.exit(1)


if __name__ == '__main__':
    run_maintenance(main, 'doctor')
