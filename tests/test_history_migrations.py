"""Instances written by released-format code upgrade to exactly what a fresh instance creates.

Fixtures come from scripts/build_history_fixtures.py, which runs the old commits' own code.
"""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import shutil
import sqlite3
import regex

import pytest

from len_bot.next.chat.schedule_store import ScheduleStore
from len_bot.next.config import CONFIG_VERSION
from len_bot.next.maintenance.doctor import check
from len_bot.next.maintenance.upgrade import migrate_instance
from len_bot.next.memory.jobs import MemoryJobs
from len_bot.next.memory.local import LocalMemory, LocalMemorySettings
from len_bot.next.storage.codec import decode_message
from len_bot.next.storage.store import Store
from len_bot.next.work.store import TaskStore, _task

HISTORY = Path(__file__).parent / 'fixtures' / 'history'
FIXTURES = sorted(path.name for path in HISTORY.iterdir() if path.is_dir())
SCENE = 'onebot:group:80001'


def _schema(path: Path) -> list[tuple]:
    """Every table, index and trigger with its columns; SQL text normalized for whitespace."""
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                          "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        result = []
        for kind, name, table, sql in rows:
            columns = None
            constraints = None
            if kind == 'table':
                columns = sorted(tuple(row[1:]) for row in db.execute(f'PRAGMA table_xinfo("{name}")'))
                checks = sorted(re.sub(r'\s+', ' ', check).strip() for check in
                                regex.findall(r'\bCHECK\s*(\((?:[^()]|(?1))*\))', sql or '', flags=regex.I))
                foreign_keys = sorted(tuple(row[1:]) for row in db.execute(f'PRAGMA foreign_key_list("{name}")'))
                unique = sorted(tuple(col[2] for col in db.execute(f'PRAGMA index_info("{index[1]}")'))
                                for index in db.execute(f'PRAGMA index_list("{name}")') if index[2] and index[3] == 'u')
                constraints = checks, foreign_keys, unique
                sql = None  # ALTER TABLE leaves different text for the same columns.
            result.append((kind, name, table, None if sql is None else re.sub(r'\s+', ' ', sql), columns, constraints))
        identity = (db.execute('PRAGMA application_id').fetchone()[0], db.execute('PRAGMA user_version').fetchone()[0])
    return [identity, *result]


def _fresh(root: Path) -> Path:
    fresh = root.parent / 'fresh'
    fresh.mkdir()
    Store(fresh / 'state.db').db.close()
    MemoryJobs(fresh / 'state.db.memory.sqlite3').close()
    LocalMemory(LocalMemorySettings(directory=fresh / 'memory'))
    return fresh


@pytest.mark.parametrize('fixture', FIXTURES)
def test_history_fixture_upgrades_to_the_fresh_schema_and_every_body_decodes(tmp_path, fixture):
    root = tmp_path / 'instance'
    shutil.copytree(HISTORY / fixture, root)
    migrate_instance(root)

    fresh = _fresh(root)
    for name in ('state.db', 'state.db.memory.sqlite3', 'memory/.memory-index.sqlite3'):
        assert _schema(root / name) == _schema(fresh / name), name
    assert json.loads((root / 'lenbot.config.json').read_text())['config_version'] == CONFIG_VERSION

    with Store(root / 'state.db') as store:
        bodies = [row[0] for row in store.db.execute('SELECT body FROM messages')]
        assert [decode_message(body).segments[0].data['text'] for body in bodies] == ['明天提醒我交报告', '收到']
        tasks = [_task(row) for row in store.db.execute('SELECT * FROM tasks')]
        assert [(task.status, task.summary) for task in tasks] == [('done', '完成')]
        assert [file.name for file in TaskStore(store).list_files(SCENE, tasks[0].id)] == ['report.md']
        schedules = ScheduleStore(store).list_schedules(SCENE)
        assert [schedule.target for schedule in schedules] == ['onebot:70001']
        tokens = [json.loads(row[0]) for row in store.db.execute('SELECT tokens FROM model_calls')]
        assert all(record is not None for record in tokens)
        event = json.loads(store.db.execute("SELECT body FROM task_events WHERE kind='model_call'").fetchone()[0])
        assert 'cost' not in event['response'] and event['response']['tokens'] is not None
    with MemoryJobs(root / 'state.db.memory.sqlite3') as jobs:
        assert jobs.latest(SCENE)['details']['calls'][0]['tokens'] is not None

    summaries = root / 'memory/scenes/onebot-group-80001'
    assert not (summaries / '.abstract.md').exists() and (summaries / 'people.md').exists()
    assert all(item['status'] != 'error' for item in check(root)), check(root)
    assert not list(root.rglob('*.v[0-9]*.bak')), 'the upgrade command relies on its snapshot, not per-file copies'


def test_local_index_starts_only_from_an_empty_file_or_its_own_format(tmp_path):
    directory = tmp_path / 'memory'
    directory.mkdir()
    with closing(sqlite3.connect(directory / '.memory-index.sqlite3')) as db:
        db.execute('PRAGMA user_version=3')
    with pytest.raises(ValueError, match='application_id=0, user_version=3'):
        LocalMemory(LocalMemorySettings(directory=directory))
    (directory / '.memory-index.sqlite3').unlink()
    LocalMemory(LocalMemorySettings(directory=directory))
    assert _schema(directory / '.memory-index.sqlite3')[0] == (0x4C424D31, 3)
