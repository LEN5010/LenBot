"""Local memory interface: pending material is visible manually, not selected automatically."""
import asyncio
import json
import logging
import sqlite3

import pytest

from len_bot.next.memory.local import LocalMemory, LocalMemorySettings
from len_bot.next.memory.jobs import MemoryJobs
from len_bot.next.memory.embeddings import EmbeddingBinding, EmbeddingClient, EmbeddingSettings
from len_bot.next.memory.service import LocalMemoryConfig, MemoryService
from len_bot.next.platform.onebot_messages import parse_message
from len_bot.next.storage.store import Store
from test_next_native_models import peer_server


def test_failed_index_transaction_restores_the_previous_complete_markdown(tmp_path):
    async def run():
        settings = LocalMemorySettings(directory=tmp_path / 'memory')
        memory = LocalMemory(settings)
        scene = 'onebot:group:80001'
        await memory.write(scene, 'topics/week.md', '周五交报告。', '已确认')
        with sqlite3.connect(memory.index) as database:
            database.execute("CREATE TRIGGER fail_index BEFORE UPDATE ON memory_files "
                             "BEGIN SELECT RAISE(ABORT, 'synthetic disk transaction failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match='transaction failure'):
            await memory.write(scene, 'topics/week.md', '改成周六。', '更正')
        restored = LocalMemory(settings)
        assert (await restored.read(scene, 'topics/week.md')).content == '周五交报告。'
        assert len(await restored.history(scene, 'topics/week.md')) == 1
    asyncio.run(run())


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['new_binding', 'changed_model', 'disabled'])
async def test_optional_vector_binding_keeps_text_recall_and_scope(tmp_path, caplog, mode):
    caplog.set_level(logging.WARNING)
    received = []
    body = json.dumps({'data': [{'index': 0, 'embedding': [1.0, 0.0, 0.0, 1.0]}]}).encode()
    replies = [(200, 'application/json', body)] * (4 if mode != 'new_binding' else 0)
    server = await peer_server(replies, received)
    scene = 'onebot:group:80001'
    settings = LocalMemoryConfig(backend='local', local=LocalMemorySettings(directory=tmp_path / 'memory'), recall_limit=1)
    async with server:
        embedding = EmbeddingSettings(provider='vectors', model='old-vector', dimensions=4,
                                      base_url=f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1',
                                      api_key='synthetic-vector-key')
        async with EmbeddingClient(embedding) as client:
            old_local = settings.local.model_copy(update={'embedding': None if mode == 'new_binding' else
                EmbeddingBinding(provider='vectors', model='old-vector', dimensions=4)})
            original = LocalMemory(old_local, embedding=client if mode != 'new_binding' else None)
            for scope, path, content in [
                (scene, 'events/meeting.md', '周五读书会在图书馆二楼。'),
                (scene, 'legacy-import/meeting.md', '周五读书会在图书馆二楼。'),
                (scene, 'events/metadata.md', '群友 90001 2026-10-03 20:40:00 UTC'),
                ('onebot:group:80002', 'events/meeting.md', '周五读书会在图书馆二楼。'),
            ]:
                await original.write(scope, path, content, '已确认资料')
        async with EmbeddingClient(embedding.model_copy(update={'model': 'new-vector'})) as client:
            settings.local.embedding = None if mode == 'disabled' else EmbeddingBinding(provider='vectors', model='new-vector', dimensions=4)
            backend = LocalMemory(settings.local, embedding=None if mode == 'disabled' else client)
            state = backend.index_status()
            assert state['needs_rebuild'] and state['retrieval'] == 'text'
            assert state['configured'] is None if mode == 'disabled' else state['configured']['model'] == 'new-vector'
            with Store(tmp_path / 'chat.db') as store, MemoryJobs(tmp_path / 'memory.db') as jobs:
                service = MemoryService(settings, backend, jobs=jobs, store=store, active_personas={})
                texts = ['之前说的读书会在哪里？', '对，就是那个']
                messages = [parse_message({
                    'post_type': 'message', 'message_type': 'group', 'group_id': 80001, 'user_id': 90001,
                    'self_id': 70001, 'message_id': index, 'time': 1791060000,
                    'sender': {'nickname': '群友', 'role': 'member'},
                    'message': [{'type': 'text', 'data': {'text': text}}]}, own_message_ids=set())
                    for index, text in enumerate(texts, 1)]
                result = await service.recall(scene, messages)
                assert [item['path'] for item in result['items']] == ['events/meeting.md']
                assert result['content_chars'] <= result['budget_chars']
                assert (await backend.read(scene, 'events/meeting.md')).content == '周五读书会在图书馆二楼。'
                assert len(await backend.history(scene, 'events/meeting.md')) == 1
                if mode != 'disabled':
                    assert '记忆改用全文检索' in caplog.text
                if mode == 'changed_model':
                    replies.extend([
                        (200, 'application/json', json.dumps({'data': [
                            {'index': index, 'embedding': [1.0, 0.0, 0.0, 1.0]} for index in range(3)]}).encode()),
                        (200, 'application/json', body), (200, 'application/json', body),
                    ])
                    assert await backend.reindex_embeddings() == 4
                    rebuilt = backend.index_status()
                    assert not rebuilt['needs_rebuild'] and rebuilt['retrieval'] == 'hybrid'
                    assert rebuilt['stored']['model'] == 'new-vector'
                    assert (await service.recall(scene, messages))['items'][0]['path'] == 'events/meeting.md'
                    assert len(await backend.history(scene, 'events/meeting.md')) == 1
    assert len(received) == {'new_binding': 0, 'changed_model': 7, 'disabled': 4}[mode]


@pytest.mark.asyncio
@pytest.mark.parametrize(('status', 'body'), [
    (429, b'{"error":"synthetic limit"}'),
    (200, b'not-json'),
    (200, b'{"data":[{"index":0,"embedding":[1,0]}]}'),
])
async def test_embedding_protocol_failure_uses_text_and_later_recovers(tmp_path, caplog, status, body):
    caplog.set_level(logging.WARNING)
    vector = json.dumps({'data': [{'index': 0, 'embedding': [1.0, 0.0, 0.0, 1.0]}]}).encode()
    received = []
    replies = [(200, 'application/json', vector), (status, 'application/json', body), (200, 'application/json', vector)]
    server = await peer_server(replies, received)
    async with server:
        settings = EmbeddingSettings(provider='vectors', model='synthetic-vector', dimensions=4,
                                     base_url=f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1',
                                     api_key='synthetic-vector-key')
        async with EmbeddingClient(settings) as client:
            backend = LocalMemory(LocalMemorySettings(directory=tmp_path / 'memory',
                embedding=EmbeddingBinding(provider='vectors', model='synthetic-vector', dimensions=4)), embedding=client)
            scene = 'onebot:group:80001'
            await backend.write(scene, 'events/meeting.md', '周五读书会在图书馆二楼。', '已确认资料')
            for _ in range(2):
                hits = await backend.search(scene, '群友 90001 说：读书会在哪？', automatic=True,
                                            text_query='读书会在哪？')
                assert [hit.path for hit in hits] == ['events/meeting.md']
            assert '记忆改用全文检索' in caplog.text
    assert len(received) == 3


def test_pending_files_do_not_take_automatic_search_slots(tmp_path):
    async def run():
        backend = LocalMemory(LocalMemorySettings(directory=tmp_path / 'memory'))
        scene = 'onebot:group:80001'
        for n in range(3):
            await backend.write(scene, f'legacy-import/{n}.md', '合成同一主题 abc', 'synthetic pending import')
        await backend.write(scene, 'topics/current.md', '合成同一主题 abc', 'synthetic adopted content')
        assert (await backend.search(scene, 'abc', 1))[0].path.startswith('legacy-import/')
        for query in ('abc', '合成'):
            result = await backend.search(scene, query, 1, exclude_pending=True)
            assert [hit.path for hit in result] == ['topics/current.md']
        assert (await backend.read(scene, 'legacy-import/0.md')).content == '合成同一主题 abc'
    asyncio.run(run())


def test_pending_material_cannot_enter_directory_summaries(tmp_path):
    async def run():
        backend = LocalMemory(LocalMemorySettings(directory=tmp_path / 'memory'))
        scene = 'onebot:group:80001'
        await backend.write(scene, 'legacy-import/group.md', '合成待确认内容', 'synthetic')
        # An old generated summary remains readable on disk, but cannot propagate into the root.
        await backend.write_summary(scene, 'legacy-import', '旧版本合成摘要', '旧版本合成概览')
        with pytest.raises(ValueError, match='待确认旧记忆'):
            await backend.summary_inputs(scene, 'legacy-import')
        inputs = await backend.summary_inputs(scene, '')
        assert not inputs['directories'] and not inputs['files']
    asyncio.run(run())


def test_changed_profile_is_not_current_and_empty_summary_can_be_removed(tmp_path):
    from len_bot.next.memory.local import scene_overview
    async def run():
        backend = LocalMemory(LocalMemorySettings(directory=tmp_path / 'memory'))
        scene = 'onebot:group:80001'
        await backend.write(scene, 'profile.md', '活动固定在周五。', '已确认安排')
        await backend.write_summary(scene, '', '活动时间', '活动固定在周五。')
        assert scene_overview(backend.root, scene) == '活动固定在周五。'
        await backend.write(scene, 'profile.md', '改到周六，周五取消。', '明确更正')
        old = await backend.summary(scene)
        assert old.changed_after is not None and old.overview == '活动固定在周五。'
        assert backend.summary_text_sync(scene) is None
        await backend.delete(scene, 'profile.md', '删除唯一正文')
        assert not (await backend.summary_inputs(scene, ''))['files']
        await backend.clear_summary(scene, '')
        assert (await backend.summary(scene)).overview is None
        assert scene_overview(backend.root, scene) is None
    asyncio.run(run())


def test_stale_child_summary_is_not_an_input_fact(tmp_path):
    async def run():
        backend = LocalMemory(LocalMemorySettings(directory=tmp_path / 'memory'))
        await backend.write('onebot:group:80001', 'events/a.md', '周五', '初始')
        await backend.write_summary('onebot:group:80001', 'events', '周五', '周五')
        await backend.write('onebot:group:80001', 'events/a.md', '改为周六', '更正')
        inputs = await backend.summary_inputs('onebot:group:80001', '')
        assert inputs['directories'] == [{'name': 'events', 'abstract': None}]
    asyncio.run(run())


@pytest.mark.parametrize('remove_source', [False, True])
def test_offline_reindex_clears_derived_summaries_and_preserves_history(tmp_path, remove_source):
    async def run():
        backend = LocalMemory(LocalMemorySettings(directory=tmp_path / 'memory'))
        scene = 'onebot:group:80001'
        await backend.write(scene, 'events/meeting.md', '读书会在周五。', '已确认安排')
        await backend.owner_write_public('events/meeting.md', '公开活动在周五。', '已确认安排')
        history = await backend.history(scene, 'events/meeting.md')
        for scope in ('scene', 'public'):
            await backend.write_summary(scene, 'events', '周五活动', '活动在周五。', scope=scope)
            await backend.write_summary(scene, '', '周五活动', '活动在周五。', scope=scope)
        source = backend.root / 'scenes/onebot:group:80001/events/meeting.md'
        if remove_source:
            source.unlink()
        else:
            source.write_text('周五活动取消，读书会改到周六。')

        assert backend.reindex() == (1 if remove_source else 2)
        assert await backend.history(scene, 'events/meeting.md') == history
        for scope in ('scene', 'public'):
            assert (await backend.summary(scene, '', scope=scope)).overview is None
            assert (await backend.summary(scene, 'events', scope=scope)).abstract is None
            assert (await backend.summary_inputs(scene, '', scope=scope))['directories'] == [
                {'name': 'events', 'abstract': None}]
        assert backend.summary_text_sync(scene) is None
        if not remove_source:
            assert (await backend.read(scene, 'events/meeting.md')).content == '周五活动取消，读书会改到周六。'
            await backend.write_summary(scene, '', '周六读书会', '读书会改到周六。')
            assert backend.summary_text_sync(scene) == '读书会改到周六。'
    asyncio.run(run())


@pytest.mark.parametrize(('texts', 'content'), [
    (['之前说的读书会在哪里？', '对，就是那个'], '周五读书会在图书馆二楼。'),
    (['Where does the BOOK CLUB meet?', 'The one on Friday'], 'The book club meets in the library.'),
    (['聚餐'], '周五聚餐在二楼。'),
    (['聚餐', '哪里'], '周五聚餐在二楼。'),
    (['聚餐', '对，就是那个'], '周五聚餐在二楼。'),
    (['之前说的读书会在哪里？', '好'], '周五读书会在图书馆二楼。'),
])
def test_text_only_automatic_recall_uses_chat_topics_without_message_metadata(tmp_path, texts, content):
    async def run():
        settings = LocalMemoryConfig(backend='local', local=LocalMemorySettings(directory=tmp_path / 'memory'),
                                     recall_limit=1)
        with Store(tmp_path / 'chat.db') as store, MemoryJobs(tmp_path / 'memory.db') as jobs:
            backend = LocalMemory(settings.local)
            service = MemoryService(settings, backend, jobs=jobs, store=store, active_personas={})
            scene = 'onebot:group:80001'
            await service.write(scene, 'events/meeting.md', content, '记录已确认活动地点')
            await backend.write(scene, 'legacy-import/meeting.md', content, '待确认材料')
            await backend.write('onebot:group:80002', 'events/other.md', content, '其他场景资料')
            await backend.write(scene, 'events/common.md', '大家好。', '日常问候')
            await backend.write(scene, 'events/metadata.md', '群友 90001 2026-10-03 20:40:00 UTC', '身份资料')
            messages = [parse_message({
                'post_type': 'message', 'message_type': 'group', 'group_id': 80001, 'user_id': 90001,
                'self_id': 70001, 'message_id': index, 'time': 1791060000,
                'sender': {'nickname': '群友', 'role': 'member'},
                'message': [{'type': 'text', 'data': {'text': text}}]}, own_message_ids=set())
                for index, text in enumerate(texts, 1)]
            result = await service.recall(scene, messages)
            assert result['query'] == '\n'.join(texts)
            assert [item['path'] for item in result['items']] == ['events/meeting.md']
            assert result['content_chars'] <= result['budget_chars']
            # Explicit search retains its complete literal query, without automatic expansion.
            assert await service.search(scene, '读书会在哪里？ BOOK CLUB meet?', 5) == []
    asyncio.run(run())
