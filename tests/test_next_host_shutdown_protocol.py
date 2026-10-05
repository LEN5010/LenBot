"""Real OneBot connections with accepted input pending at host shutdown."""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import asdict
import json
from pathlib import Path
import time

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from len_bot.next.chat.attention import AttentionState
from len_bot.next.config import load_host_config
from len_bot.next.models.client import ChatModel
from len_bot.next.persona.profile import load_persona
from len_bot.next.runtime.network import NetworkRuntime
from len_bot.next.storage.store import Store


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['forward_ws', 'reverse_ws'])
async def test_stop_releases_onebot_without_consuming_waiting_ambient_input(tmp_path: Path, mode: str):
    connections = asyncio.Queue()

    async def peer(websocket):
        await connections.put(websocket)
        await websocket.wait_closed()

    role = tmp_path / 'persona'
    role.mkdir()
    (role / 'persona.yaml').write_text(
        'id: shutdown-fixture\nname: 协议角色\nbrief: 停机协议核对\nbehavior: 旁听\n'
        'self_reference: [我]\naliases: []\ntools: []\nskills: []\nstyles: []\n')
    (role / 'voice.md').write_text('协议核对')
    (role / 'boundaries.md').write_text('协议核对')
    (role / 'examples.yaml').write_text('[]\n')
    raw = json.loads((Path(__file__).parent / 'fixtures/next/shutdown/message.json').read_text())

    async with AsyncExitStack() as resources:
        onebot = {'mode': mode, 'request_timeout_seconds': 1}
        if mode == 'forward_ws':
            server = await resources.enter_async_context(serve(peer, '127.0.0.1', 0))
            onebot['ws_url'] = f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}'
        else:
            onebot.update(listen_host='127.0.0.1', listen_port=0)
        source = {
            'mode': 'isolated-multi', 'bot_id': 'onebot:90001', 'timezone': 'UTC',
            'database': 'chat.sqlite3', 'delivery': 'simulated', 'onebot': onebot,
            'models': {'providers': {'fixture': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1',
                                               'api_key': 'synthetic-unused'}},
                       'roles': {'mind': {'provider': 'fixture', 'model': 'synthetic-unused',
                                          'context_window_tokens': 8192}}},
            'compaction': {'input_tokens': 2000},
            'scenes': {'onebot:group:80001': {'persona': 'persona', 'attention': {
                'activity': 1, 'ambient_threshold': .1,
                'ambient_min_interval_seconds': 60, 'ambient_max_interval_seconds': 900}}},
        }
        (tmp_path / 'lenbot.config.json').write_text(json.dumps(source, ensure_ascii=False))
        cfg = load_host_config(tmp_path)
        with Store(cfg.database) as store:
            # The recorded failure had silence_level=4, which leaves a 900s gap.
            store.save_attention('onebot:group:80001', asdict(AttentionState(
                ambient_last_at=time.time(), silence_level=4)))
            async with ChatModel(cfg.model_settings('mind')) as model:
                runtime = NetworkRuntime(cfg, [(cfg.scene_config('onebot:group:80001'), load_persona(role))], store, model)
                async with asyncio.TaskGroup() as tasks:
                    running = tasks.create_task(runtime.run(manage_signals=False))
                    if mode == 'reverse_ws':
                        async with asyncio.timeout(3):
                            while not runtime.platform.addresses:
                                await asyncio.sleep(.01)
                        websocket = await resources.enter_async_context(connect(
                            f'ws://127.0.0.1:{runtime.platform.addresses[0][1]}',
                            additional_headers={'X-Client-Role': 'Universal', 'X-Self-ID': '90001'}, proxy=None))
                    else:
                        websocket = await connections.get()
                    async with asyncio.timeout(3):
                        while runtime.status != 'running':
                            await asyncio.sleep(.01)
                    await websocket.send(json.dumps(raw))
                    async with asyncio.timeout(3):
                        while not store.pending_messages('onebot:group:80001'):
                            await asyncio.sleep(.01)
                    runtime.stop()
                    await asyncio.wait_for(running, 3)
                    await asyncio.wait_for(websocket.wait_closed(), 3)
                assert runtime.status == 'stopped'
                assert not runtime.platform.connected
                assert [message.platform_message_id for _, message, _ in store.pending_messages('onebot:group:80001')] == ['-10001']
                assert store.mind_history_page('onebot:group:80001', before=None, limit=1, active_only=True)['last_message_seq'] == 0
                assert store.db.execute('SELECT count(*) FROM turns').fetchone()[0] == 0
                assert store.db.execute('SELECT count(*) FROM model_calls').fetchone()[0] == 0
