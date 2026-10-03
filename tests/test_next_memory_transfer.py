"""Offline memory transfer preserves exclusions after ordinary message retention."""

import asyncio
import json
import sqlite3
import time

import pytest

from len_bot.next.config import load_host_config
from len_bot.next.maintenance.transfer_memory import export_archive, check_processing_archive
from len_bot.next.memory.jobs import MemoryJobs
from len_bot.next.memory.local import LocalMemory
from len_bot.next.memory.service import MemoryService
from len_bot.next.models.client import ChatModel
from len_bot.next.persona.profile import load_persona
from len_bot.next.platform.messages import parse_message
from len_bot.next.runtime.network import NetworkRuntime
from len_bot.next.storage.store import Store


SCENE = 'group:80001'


def _root(root):
    role = root / 'role'
    role.mkdir()
    (role / 'persona.yaml').write_text(json.dumps(dict(
        id='fixture', name='合成角色', brief='离线核验', behavior='正常', self_reference=['我'],
        aliases=[], tools=['say'], skills=[], styles=[])))
    for name, content in [('voice.md', '简短'), ('boundaries.md', '正常'), ('examples.yaml', '[]')]:
        (role / name).write_text(content)
    source = dict(
        mode='isolated-multi', bot_qq='70001', timezone='Asia/Shanghai', database='state.db',
        delivery='simulated', onebot={'mode': 'forward_ws', 'ws_url': 'ws://127.0.0.1:9/unused'},
        models={'providers': {'offline': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1',
                                          'api_key': 'unused-placeholder'}},
                'roles': {name: {'provider': 'offline', 'model': name, 'context_window_tokens': 16384}
                          for name in ['mind', 'voice']}},
        memory={'backend': 'local', 'local': {'directory': 'memory'}},
        retention={'message_days': {SCENE: 1}}, scenes={SCENE: {'persona': 'role'}})
    (root / 'lenbot.config.json').write_text(json.dumps(source))
    return source


def _enqueue(store, ident, *, group=80001):
    old = time.time() - 3 * 86400
    raw = {'post_type': 'message', 'message_type': 'group', 'group_id': group, 'user_id': 90001,
           'self_id': 70001, 'message_id': ident, 'time': old, 'sender': {'nickname': '群友', 'role': 'member'},
           'message': [{'type': 'text', 'data': {'text': '读书会旧记录'}}]}
    store.enqueue(parse_message(raw, own_message_ids=set()), raw, old)


def _export_config(root, source):
    source['memory_transfer'] = {'operation': 'export', 'source': source['memory'],
                                 'archive': 'transfer', 'scenes': [SCENE], 'public_scene': SCENE}
    source['memory'] = {'backend': 'openviking', 'openviking': {
        'base_url': 'http://127.0.0.1:9', 'account_id': 'offline',
        'scenes': {SCENE: {'user_id': 'offline-group', 'api_key': 'unused-placeholder'}}}}
    (root / 'lenbot.config.json').write_text(json.dumps(source))
    return load_host_config(root)


@pytest.mark.parametrize('initialize_cursor', [False, True])
def test_transfer_preserves_exclusions_after_retention(tmp_path, initialize_cursor):
    async def run():
        source = _root(tmp_path)
        config = load_host_config(tmp_path)
        jobs_path = config.database.with_name('state.db.memory.sqlite3')
        with Store(config.database) as store, MemoryJobs(jobs_path) as jobs:
            _enqueue(store, 1)
            _enqueue(store, 2)
            turn = store.start_turn(SCENE, batch=(2, ['历史消息']))
            store.end_turn(turn, 'complete')
            store.new_context(SCENE)
            if initialize_cursor:
                jobs.initialize(SCENE, store.max_message_seq(SCENE))
            service = MemoryService(config.memory, LocalMemory(config.memory.local), jobs=jobs,
                                    store=store, active_personas={SCENE: 'fixture'})
            await service.write(SCENE, 'events/meeting.md', '读书会旧记录', '确认内容')
            await service.delete(SCENE, 'events/meeting.md', '明确遗忘', forget=True, exclude_records=[1])
            async with ChatModel(config.model_settings('mind')) as mind, \
                    ChatModel(config.model_settings('voice')) as voice:
                runtime = NetworkRuntime(config, [(config.scene_config(SCENE), load_persona(tmp_path / 'role'))],
                                         store, mind, voice, memory=service)
                store.new_context(SCENE)
                assert runtime.retention.batch()['messages'] == 1
            assert store.read_message(SCENE, 1) is None
            assert jobs.excluded_records(SCENE) == [1]

        transfer_config = _export_config(tmp_path, source)
        result = await export_archive(transfer_config)
        assert result['report']['finished'] is not None and result['report']['error'] is None
        with sqlite3.connect(tmp_path / 'transfer/memory-jobs.sqlite3') as snapshot:
            assert snapshot.execute('SELECT * FROM memory_exclusions').fetchall() == [(SCENE, 1)]
            assert bool(snapshot.execute('SELECT * FROM memory_cursors').fetchall()) == initialize_cursor
        check_processing_archive(transfer_config)
        with MemoryJobs(jobs_path) as jobs:
            jobs.exclude_records(SCENE, [2])
        with pytest.raises(ValueError, match='exclusions or extraction results changed'):
            check_processing_archive(transfer_config)
    asyncio.run(run())


def test_transfer_rejects_exclusion_for_existing_other_scene_message(tmp_path):
    source = _root(tmp_path)
    config = load_host_config(tmp_path)
    with Store(config.database) as store, MemoryJobs(config.database.with_name('state.db.memory.sqlite3')) as jobs:
        _enqueue(store, 1, group=89999)
        jobs.exclude_records(SCENE, [1])
    with pytest.raises(ValueError, match='source belongs to another scene'):
        asyncio.run(export_archive(_export_config(tmp_path, source)))
    assert not (tmp_path / 'transfer').exists()
