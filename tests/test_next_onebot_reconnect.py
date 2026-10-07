"""OneBot connections that drop while the host keeps running."""

import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from len_bot.next.config import load_host_config
from len_bot.next.configuration.onebot import OneBotForward
from len_bot.next.models.client import ChatModel
from len_bot.next.persona.profile import load_persona
from len_bot.next.platform import onebot as onebot_module
from len_bot.next.platform.onebot import OneBot
from len_bot.next.runtime.network import NetworkRuntime
from len_bot.next.storage.store import Store

SCENE = 'onebot:group:80001'
LOGIN = {'status': 'ok', 'retcode': 0, 'data': {'user_id': 90001, 'nickname': '脱敏 Bot'}}


async def _until(condition, seconds: float = 3) -> None:
    async with asyncio.timeout(seconds):
        while not condition():
            await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_forward_transport_dials_again_and_verifies_the_new_connection(monkeypatch):
    monkeypatch.setattr(onebot_module, 'RECONNECT_INITIAL_SECONDS', .05)
    connections, requests, errors = [], [], []

    async def peer(websocket):
        connections.append(websocket)
        if len(connections) == 1:
            await websocket.close(code=1012, reason='synthetic restart')
            return
        async for frame in websocket:
            request = json.loads(frame)
            requests.append(request['action'])
            data = LOGIN['data'] if request['action'] == 'get_login_info' else {'message_id': 1}
            await websocket.send(json.dumps({**LOGIN, 'data': data, 'echo': request['echo']}))

    async with serve(peer, '127.0.0.1', 0) as server:
        settings = OneBotForward(mode='forward_ws', ws_url=f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}',
                                 request_timeout_seconds=1)
        async with OneBot(settings, bot_id='onebot:90001', on_event=lambda event: None,
                          on_error=errors.append) as bot:
            await _until(lambda: len(connections) == 2 and bot.connected)
            assert await bot.identify() == 'onebot:90001'
            assert (await bot.call('get_status', {}))['status'] == 'ok'
    assert any('synthetic restart' in error for error in errors)
    assert requests == ['get_login_info', 'get_status']


@pytest.mark.asyncio
async def test_forward_close_does_not_wait_out_the_reconnect_backoff(monkeypatch):
    monkeypatch.setattr(onebot_module, 'RECONNECT_INITIAL_SECONDS', 60)
    async def peer(websocket):
        await websocket.close()

    server = await serve(peer, '127.0.0.1', 0)
    settings = OneBotForward(mode='forward_ws', ws_url=f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}',
                             request_timeout_seconds=1)
    bot = OneBot(settings, bot_id=None, on_event=lambda event: None, on_error=lambda error: None)
    try:
        await bot.start()
        await _until(lambda: not bot.connected)
    finally:
        server.close()
        await server.wait_closed()
    async with asyncio.timeout(2):
        await bot.close()
    assert not bot.connected


def _runtime_config(tmp_path: Path, onebot: dict) -> None:
    role = tmp_path / 'persona'
    role.mkdir()
    (role / 'persona.yaml').write_text(
        'id: reconnect-fixture\nname: 协议角色\nbrief: 重连协议核对\nbehavior: 旁听\n'
        'self_reference: [我]\naliases: []\ntools: []\nskills: []\nstyles: []\n')
    (role / 'voice.md').write_text('协议核对')
    (role / 'boundaries.md').write_text('协议核对')
    (role / 'examples.yaml').write_text('[]\n')
    source = {
        'mode': 'isolated-multi', 'bot_id': 'onebot:90001', 'timezone': 'UTC',
        'database': 'chat.sqlite3', 'delivery': 'simulated', 'onebot': {**onebot, 'request_timeout_seconds': 1},
        'models': {'providers': {'fixture': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1',
                                           'api_key': 'synthetic-unused'}},
                   'roles': {'mind': {'provider': 'fixture', 'model': 'synthetic-unused',
                                      'context_window_tokens': 8192}}},
        'compaction': {'input_tokens': 2000},
        'scenes': {SCENE: {'persona': 'persona', 'attention': {'only_direct': True}}},
    }
    (tmp_path / 'lenbot.config.json').write_text(json.dumps(source, ensure_ascii=False))


@pytest.mark.asyncio
async def test_forward_host_keeps_running_across_a_peer_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(onebot_module, 'RECONNECT_INITIAL_SECONDS', .05)
    connections = asyncio.Queue()

    async def peer(websocket):
        await connections.put(websocket)
        await websocket.wait_closed()

    raw = json.loads((Path(__file__).parent / 'fixtures/next/shutdown/message.json').read_text())
    async with AsyncExitStack() as resources:
        server = await resources.enter_async_context(serve(peer, '127.0.0.1', 0))
        _runtime_config(tmp_path, {'mode': 'forward_ws',
                                   'ws_url': f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}'})
        cfg = load_host_config(tmp_path)
        with Store(cfg.database) as store:
            async with ChatModel(cfg.model_settings('mind')) as model:
                runtime = NetworkRuntime(cfg, [(cfg.scene_config(SCENE), load_persona(tmp_path / 'persona'))], store, model)
                running = asyncio.create_task(runtime.run(manage_signals=False))
                first = await asyncio.wait_for(connections.get(), 3)
                await _until(lambda: runtime.status == 'running')
                await first.close()
                await _until(lambda: not runtime.accepting)
                # A runner woken while the peer is away waits for the connection instead of ending.
                runtime.runners[SCENE].changed.set()
                second = await asyncio.wait_for(connections.get(), 3)
                await _until(lambda: runtime.accepting)
                assert runtime.status == 'running' and not running.done()
                await second.send(json.dumps(raw))
                await _until(lambda: store.pending_messages(SCENE))
                runtime.stop()
                await asyncio.wait_for(running, 3)
        assert runtime.status == 'stopped'


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['forward_ws', 'reverse_ws'])
async def test_panel_disconnect_while_the_peer_is_away_stops_cleanly(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(onebot_module, 'RECONNECT_INITIAL_SECONDS', 60)
    connections = asyncio.Queue()

    async def peer(websocket):
        await connections.put(websocket)
        await websocket.wait_closed()

    async with AsyncExitStack() as resources:
        if mode == 'forward_ws':
            server = await resources.enter_async_context(serve(peer, '127.0.0.1', 0))
            onebot = {'mode': mode, 'ws_url': f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}'}
        else:
            onebot = {'mode': mode, 'listen_host': '127.0.0.1', 'listen_port': 0}
        _runtime_config(tmp_path, onebot)
        cfg = load_host_config(tmp_path)
        with Store(cfg.database) as store:
            async with ChatModel(cfg.model_settings('mind')) as model:
                runtime = NetworkRuntime(cfg, [(cfg.scene_config(SCENE), load_persona(tmp_path / 'persona'))], store, model)
                running = asyncio.create_task(runtime.run(manage_signals=False))
                if mode == 'forward_ws':
                    websocket = await asyncio.wait_for(connections.get(), 3)
                else:
                    await _until(lambda: runtime.platform.addresses)
                    websocket = await connect(f'ws://127.0.0.1:{runtime.platform.addresses[0][1]}', proxy=None,
                                              additional_headers={'X-Client-Role': 'Universal', 'X-Self-ID': '90001'})
                await _until(lambda: runtime.status == 'running')
                await websocket.close()
                await _until(lambda: not runtime.platform.connected)
                runtime.runners[SCENE].changed.set()
                await asyncio.sleep(.05)
                await runtime.disconnect()
                await asyncio.wait_for(running, 3)
        assert runtime.status == 'stopped'
