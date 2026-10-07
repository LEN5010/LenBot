"""Plugin data versions: migrated before start, put back on failure, never read by an older plugin."""

from __future__ import annotations

import asyncio
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from len_bot.next.config import load_host_config
from len_bot.next.plugins.data import MARKER, prepare_data, recorded_version
from len_bot.next.plugins.install import Installation, PluginInstaller, Source
from len_bot.next.plugins.manager import PluginManager
from len_bot.next.panel.setup import FirstSetup, initialize
from len_bot.plugin_testing import PluginTest

MANIFEST = '''name = "{name}"
version = "{version}"
interface = 1
data_version = {data_version}
requires_lenbot = ">=0.2,<1"
requires_python = ">=3.13"
platforms = ["linux", "darwin", "win32"]
reload = "plugin"
authors = ["合成作者"]
license = "MIT"
description = "数据版本合成插件"
'''

SOURCE = '''from len_bot.plugin import Invocation, Plugin, tool


class Notes(Plugin):
    async def migrate_data(self, from_version: int) -> None:
        old = self.ctx.data_dir / 'notes.txt'
        lines = old.read_text(encoding='utf-8').splitlines()
        await self.ctx.set_kv('notes', lines)
        old.unlink()
        self.ctx.log.info('migrated %d notes from version %d', len(lines), from_version)

    async def start(self) -> None:
        if {fail_start}:
            raise RuntimeError('synthetic start failure')

    @tool('notes_read', '读取合成笔记', summary='读取合成笔记')
    async def read(self, ctx: Invocation) -> dict:
        return {{'notes': await ctx.get_kv('notes', [])}}
'''


def _package(root: Path, *, data_version: int, version: str = '1.0.0', name: str = 'notes',
             fail_start: bool = False) -> Path:
    package = root / name
    package.mkdir(parents=True)
    (package / 'plugin.toml').write_text(MANIFEST.format(name=name, version=version, data_version=data_version), encoding='utf-8')
    (package / '__init__.py').write_text(SOURCE.format(fail_start=fail_start), encoding='utf-8')
    return package


def _zip(package: Path) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name in ('plugin.toml', '__init__.py'):
            archive.writestr(name, (package / name).read_bytes())
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_upgrade_migrates_existing_data_before_start(tmp_path):
    data = tmp_path / 'old-data'
    data.mkdir()
    (data / 'notes.txt').write_text('第一条\n第二条\n', encoding='utf-8')
    async with PluginTest(_package(tmp_path, data_version=2), data=data) as test:
        result = json.loads(await test.tool('notes_read', {}))
        assert result == {'notes': ['第一条', '第二条']}
        data_dir = test.host.plugins['notes'].context.data_dir
        assert recorded_version(data_dir) == 2 and not (data_dir / 'notes.txt').exists()
        backup, = (data_dir.parent / '.backups').iterdir()
        assert backup.name.startswith('notes-v1-') and (backup / 'notes.txt').exists()


@pytest.mark.asyncio
async def test_new_data_records_the_current_version_without_migration(tmp_path):
    async with PluginTest(_package(tmp_path, data_version=3)) as test:
        assert json.loads(await test.tool('notes_read', {})) == {'notes': []}
        data_dir = test.host.plugins['notes'].context.data_dir
        assert json.loads((data_dir / MARKER).read_text()) == {'data_version': 3}


@pytest.mark.asyncio
async def test_failed_migration_puts_the_data_back(tmp_path):
    data_dir = tmp_path / 'data' / 'notes'
    data_dir.mkdir(parents=True)
    (data_dir / 'notes.txt').write_text('原样\n', encoding='utf-8')

    async def broken(from_version: int) -> None:
        (data_dir / 'notes.txt').write_text('写到一半', encoding='utf-8')
        (data_dir / 'partial.json').write_text('{}', encoding='utf-8')
        raise RuntimeError('synthetic migration failure')

    with pytest.raises(RuntimeError, match='synthetic migration failure') as caught:
        await prepare_data('notes', 2, data_dir, tmp_path / 'data' / '.backups', broken)
    assert '已恢复到迁移前' in str(caught.value.__notes__)
    assert (data_dir / 'notes.txt').read_text(encoding='utf-8') == '原样\n'
    assert not (data_dir / 'partial.json').exists() and recorded_version(data_dir) == 1


@pytest.mark.asyncio
async def test_older_plugin_refuses_newer_data(tmp_path):
    data = tmp_path / 'new-data'
    data.mkdir()
    (data / 'kv.json').write_text('{}', encoding='utf-8')
    with pytest.raises(RuntimeError, match='数据版本 3'):
        async with PluginTest(_package(tmp_path, data_version=2), data=data, data_version=3):
            pass


def test_apply_keeps_the_replaced_source_for_one_rollback(tmp_path):
    initialize(tmp_path, FirstSetup.model_validate_json(json.dumps({
        'bot_id': 'onebot:90001', 'owners': ['onebot:70001'], 'timezone': 'UTC', 'delivery': 'simulated',
        'onebot': {'mode': 'forward_ws', 'ws_url': 'ws://127.0.0.1:9'},
        'provider': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1', 'api_key': 'synthetic-unused'},
        'compaction': {'input_tokens': 2000},
        'mind': {'provider': 'primary', 'model': 'fixture', 'context_window_tokens': 16000},
        'scene': 'onebot:group:80001', 'panel_port': 8088, 'username': 'fixture', 'password': 'synthetic-password',
    })))
    path = tmp_path / 'lenbot.config.json'
    config = json.loads(path.read_text())
    config['plugins']['notes'] = {}
    path.write_text(json.dumps(config))
    installer = PluginInstaller(tmp_path)
    installer.records.mkdir(parents=True)
    old = _package(installer.directory, data_version=1, version='1.0.0')
    new = _package(installer.candidates, data_version=2, version='2.0.0')
    source = lambda version: Source(kind='zip', location='notes.zip', ref=None, branch=None, revision=version, version=version)
    installer.write(Installation(name='notes', installed=source('1.0.0'), candidate=source('2.0.0'),
                                 application='plugin', requested=False, error=None))
    installer.apply_files('notes')
    assert 'version = "2.0.0"' in (old / 'plugin.toml').read_text()
    assert installer.read('notes').previous.version == '1.0.0' and not new.exists()

    installer.rollback_files('notes')
    record = installer.read('notes')
    assert 'version = "1.0.0"' in (old / 'plugin.toml').read_text()
    assert record.installed.version == '1.0.0' and record.previous is None
    with pytest.raises(ValueError, match='没有保留上一版本'):
        installer.rollback_files('notes')


@pytest.mark.asyncio
async def test_failed_apply_returns_to_the_previous_source_and_its_data(tmp_path):
    root = tmp_path / 'host'
    role = root / 'role'
    role.mkdir(parents=True)
    (role / 'persona.yaml').write_text(json.dumps({
        'id': 'synthetic', 'name': '合成角色', 'brief': '仅用于插件回退。', 'behavior': '正常对话。',
        'self_reference': ['我'], 'aliases': [], 'tools': 'all', 'skills': [], 'styles': []}, ensure_ascii=False))
    for name, body in [('voice.md', '简短。'), ('boundaries.md', '合成。'), ('examples.yaml', '[]\n')]:
        (role / name).write_text(body, encoding='utf-8')
    scene = 'onebot:group:80001'
    (root / 'lenbot.config.json').write_text(json.dumps({
        'compaction': {'input_tokens': 2000}, 'mode': 'isolated-multi', 'bot_id': 'onebot:90001', 'timezone': 'Asia/Shanghai', 'database': 'state.db',
        'onebot': {'mode': 'reverse_ws', 'listen_host': '127.0.0.1', 'listen_port': 0},
        'models': {'providers': {'sample': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1', 'api_key': 'synthetic'}},
                   'roles': {'mind': {'provider': 'sample', 'model': 'mind', 'context_window_tokens': 8192}}},
        'scenes': {scene: {'persona': 'role'}}, 'plugins': {'paths': ['plugins']},
    }), encoding='utf-8')
    runtime = SimpleNamespace(plugins=None, mcp=None, accepting=True, notify=lambda: None,
                              refresh_external_tools=lambda: None,
                              chats={scene: SimpleNamespace(config=SimpleNamespace(plugins=[]))})
    manager = PluginManager(root, runtime, load_host_config(root), asyncio.Lock())
    await manager.import_zip(_zip(_package(tmp_path / 'v1', data_version=1)), 'notes.zip')
    await manager.save(lambda source, _: source['plugins'].update(disabled=[]))
    await manager.apply_candidate('notes')
    data_dir = runtime.plugins.plugins['notes'].context.data_dir
    (data_dir / 'notes.txt').write_text('第一条\n', encoding='utf-8')

    await manager.import_zip(_zip(_package(tmp_path / 'v2', data_version=2, version='2.0.0', fail_start=True)), 'notes.zip')
    with pytest.raises(RuntimeError, match='synthetic start failure') as caught:
        await manager.apply_candidate('notes')
    try:
        assert '插件数据已从' in str(caught.value.__notes__)
        record = manager.installer.read('notes')
        assert record.installed.version == '1.0.0' and record.previous is None
        assert runtime.plugins.plugins['notes'].error is None
        assert recorded_version(data_dir) == 1
        assert (data_dir / 'notes.txt').read_text(encoding='utf-8') == '第一条\n'
    finally:
        await runtime.plugins.close()


def test_doctor_reports_data_newer_than_the_plugin_and_foreign_kv_files(tmp_path):
    import sqlite3
    from len_bot.next.maintenance.doctor import _plugin_data
    from len_bot.next.plugins.data import write_version
    from len_bot.next.plugins.kv import PluginKV

    data_dir = tmp_path / 'notes'
    data_dir.mkdir()
    PluginKV(data_dir).set('notes', ['第一条'])
    write_version(data_dir, 2)
    assert _plugin_data(data_dir, 2) == [] and _plugin_data(tmp_path / 'missing', 1) == []
    assert '数据版本 2 比已安装插件的 data_version 1 新' in _plugin_data(data_dir, 1)[0]
    with sqlite3.connect(data_dir / 'kv.sqlite3') as db:
        db.execute('PRAGMA user_version=7')
    assert 'kv.sqlite3 不是插件 KV 格式 1' in _plugin_data(data_dir, 2)[0]
