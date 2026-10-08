"""Authenticated rebuild requests against local files and an embedding HTTP peer."""

import asyncio
from contextlib import AsyncExitStack
import json

import httpx
import pytest

from len_bot.next.config import load_host_config
from len_bot.next.learning.expression_selection import open_expression_service
from len_bot.next.learning.store import LearningStore
from len_bot.next.memory.local import LocalMemory, LocalMemorySettings
from len_bot.next.memory.service import open_memory
from len_bot.next.models.client import ChatModel
from len_bot.next.panel.app import create_app
from len_bot.next.persona.profile import load_persona
from len_bot.next.runtime.network import NetworkRuntime
from len_bot.next.storage.store import Store
from test_next_host_manage_permissions_config import instance


SCENE = 'onebot:group:80001'


@pytest.mark.asyncio
@pytest.mark.parametrize('failed', [False, True])
async def test_panel_rebuild_preserves_old_index_until_vectors_succeed_and_queues_writes(tmp_path, failed):
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def peer(reader, writer):
        head = (await reader.readuntil(b'\r\n\r\n')).decode()
        fields = {key.lower(): value.strip() for line in head.split('\r\n') if ':' in line
                  for key, value in [line.split(':', 1)]}
        body = json.loads(await reader.readexactly(int(fields['content-length'])))
        requests.append(body)
        entered.set()
        await release.wait()
        payload = ({'error': 'synthetic embedding unavailable'} if failed else {'data': [
            {'index': index, 'embedding': [1.0, 0.0, 1.0, 0.0]} for index in range(len(body['input']))]})
        data = json.dumps(payload).encode()
        writer.write(f'HTTP/1.1 {503 if failed else 200} Fixture\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n'.encode() + data)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(peer, '127.0.0.1', 0)
    source = instance(tmp_path)
    local = LocalMemory(LocalMemorySettings(directory=tmp_path / 'memory'))
    await local.write(SCENE, 'events/meeting.md', '周五读书会在图书馆二楼。', '合成资料')
    source['models']['providers']['vectors'] = {'api': 'openai-embeddings',
        'base_url': f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1', 'api_key': 'synthetic-key'}
    source['memory']['local']['embedding'] = {'provider': 'vectors', 'model': 'synthetic-vector', 'dimensions': 4}
    (tmp_path / 'lenbot.config.json').write_text(json.dumps(source))
    config = load_host_config(tmp_path)
    persona = load_persona(config.scenes[SCENE].persona)
    async with server, AsyncExitStack() as stack:
        store = stack.enter_context(Store(config.database))
        mind = await stack.enter_async_context(ChatModel(config.model_settings('mind')))
        memory = await stack.enter_async_context(open_memory(config, store, active_personas={scene: persona.id for scene in config.scenes}))
        runtime = NetworkRuntime(config, [(config.scene_config(scene), persona) for scene in config.scenes],
                                 store, mind, memory=memory)
        client = await stack.enter_async_context(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(config, runtime, root=tmp_path)), base_url='http://testserver'))
        assert (await client.post('/api/host/memory/reindex')).status_code == 401
        await client.post('/api/auth/login', json={'username': 'operator', 'password': 'fixture-password'})
        rebuild = asyncio.create_task(client.post('/api/host/memory/reindex'))
        try:
            async with asyncio.timeout(3):
                await entered.wait()
                state = (await client.get('/api/host/memory/state')).json()
                assert state['reindexing'] and state['index']['retrieval'] == 'text'
                assert (await client.post('/api/host/memory/reindex')).status_code == 409
                # HTTP vector generation does not hold the old index's read locks.
                hits = await client.post('/api/host/memory/search', json={'scene': SCENE, 'query': '读书会'})
                assert hits.status_code == 200 and hits.json()['hits'][0]['path'] == 'events/meeting.md'
                edit = asyncio.create_task(client.put('/api/host/memory/file', json={
                    'scene': SCENE, 'scope': 'scene', 'path': 'events/meeting.md',
                    'content': '改为图书馆三楼。', 'reason': '合成更正'}))
                await asyncio.sleep(.01)
                assert not edit.done()
                release.set()
                result = await rebuild
                assert result.status_code == (500 if failed else 200)
                if not failed:
                    assert result.json()['files'] == 1
                assert (await edit).status_code == (422 if failed else 200)
                state = (await client.get('/api/host/memory/state')).json()
                assert not state['reindexing']
                assert state['index']['needs_rebuild'] == failed
                assert (await memory.backend.read(SCENE, 'events/meeting.md')).content == (
                    '周五读书会在图书馆二楼。' if failed else '改为图书馆三楼。')
                assert len(await memory.history(SCENE, 'events/meeting.md')) == (1 if failed else 2)
        finally:
            release.set()
            if not rebuild.done():
                rebuild.cancel()
                await asyncio.gather(rebuild, return_exceptions=True)
    assert len(requests) == (1 if failed else 2)


@pytest.mark.asyncio
async def test_rebuild_buttons_are_authenticated_for_text_memory_and_scene_expression_index(tmp_path):
    source = instance(tmp_path)
    source['models']['providers']['vectors'] = {'api': 'openai-embeddings',
        'base_url': 'http://127.0.0.1:9/v1', 'api_key': 'synthetic-unused'}
    source['scenes'][SCENE]['learning'] = {'extract': False,
        'embedding': {'provider': 'vectors', 'model': 'synthetic-vector', 'dimensions': 4}}
    (tmp_path / 'lenbot.config.json').write_text(json.dumps(source))
    config = load_host_config(tmp_path)
    persona = load_persona(config.scenes[SCENE].persona)
    async with AsyncExitStack() as stack:
        store = stack.enter_context(Store(config.database))
        mind = await stack.enter_async_context(ChatModel(config.model_settings('mind')))
        memory = await stack.enter_async_context(open_memory(config, store, active_personas={scene: persona.id for scene in config.scenes}))
        expressions = await stack.enter_async_context(open_expression_service(config, store))
        runtime = NetworkRuntime(config, [(config.scene_config(scene), persona) for scene in config.scenes],
                                 store, mind, memory=memory, expression_service=expressions)
        client = await stack.enter_async_context(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(config, runtime, root=tmp_path)), base_url='http://testserver'))
        path = f'/api/host/scenes/{SCENE}/learning/reindex'
        assert (await client.post(path)).status_code == 401
        await client.post('/api/auth/login', json={'username': 'operator', 'password': 'fixture-password'})
        assert (await client.post(path)).json() == {'expressions': 0}
        assert (await client.post('/api/host/scenes/onebot:group:89999/learning/reindex')).status_code == 404
        assert (await client.post('/api/host/scenes/onebot:group:80002/learning/reindex')).status_code == 409
        await memory.write(SCENE, 'events/week.md', '周末活动。', '合成资料')
        assert (await client.post('/api/host/memory/reindex')).json() == {'files': 1}
        assert (await memory.backend.read(SCENE, 'events/week.md')).content == '周末活动。'
        assert len(await memory.history(SCENE, 'events/week.md')) == 1
        assert LearningStore(store).adopted(SCENE) == []
