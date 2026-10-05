"""First-run configuration validation, no production connections."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from len_bot.next.config import load_host_config
from len_bot.next.persona.profile import load_persona
from len_bot.next.panel.setup import create_setup_app
from len_bot.web.auth import verify_password


@pytest.mark.parametrize('voice_mode', ['direct'])
def test_first_setup_validates_and_never_overwrites(tmp_path: Path, voice_mode: str):
    body = {
        'bot_id':'onebot:90001','owners':['onebot:70001'],'timezone':'Asia/Shanghai','delivery':'simulated',
        'onebot':{'mode':'forward_ws','ws_url':'ws://127.0.0.1:9','access_token':'synthetic-token'},
        'provider':{'api':'openai-chat','base_url':'http://127.0.0.1:9/v1','api_key':'synthetic-secret'},
        'mind':{'provider':'primary','model':'fixture','context_window_tokens':8192},

        'voice_mode': voice_mode, 'compaction': {'input_tokens': 6000},
        'scene':'onebot:group:80001','persona_id':'fixture','persona_name':'合成角色','brief':'测试设定',
        'voice_text':'简短','boundaries':'合成场景','panel_port':8088,'username':'fixture',
        'password':'synthetic-password',
    }
    async def exercise():
        complete = asyncio.Event()
        app = create_setup_app(tmp_path, 'fixture-token', complete)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1') as client:
            assert (await client.post('/api/setup', json=body)).status_code == 401
            client.headers['X-Setup-Token'] = 'fixture-token'
            invalid = await client.post('/api/setup', json={**body, 'scene':'invalid'})
            assert invalid.status_code == 422 and not (tmp_path/'lenbot.config.json').exists()
            invalid = await client.post('/api/setup', json={**body, 'password':42})
            assert invalid.status_code == 422 and 'synthetic-secret' not in invalid.text
            invalid = await client.post('/api/setup', json={**body, 'voice_mode':'unknown'})
            assert invalid.status_code == 422 and not (tmp_path/'lenbot.config.json').exists()
            response = await client.post('/api/setup', json=body)
            assert response.status_code == 200, response.text
            assert complete.is_set() and 'synthetic-secret' not in response.text
            cfg = load_host_config(tmp_path)
            assert cfg.delivery == 'simulated' and cfg.models.roles.mind.model == 'fixture'
            assert cfg.scenes['onebot:group:80001'].voice_mode == voice_mode
            assert response.json()['voice_mode'] == voice_mode
            assert load_persona(cfg.scenes['onebot:group:80001'].persona).name == '合成角色'
            assert verify_password(body['password'], cfg.panel.password_hash)
            before = (tmp_path/'lenbot.config.json').read_bytes()
            assert body['password'] not in before.decode()
            assert (await client.post('/api/setup', json=body)).status_code == 409
            assert (tmp_path/'lenbot.config.json').read_bytes() == before
    asyncio.run(exercise())


@pytest.mark.asyncio
async def test_setup_reads_account_from_onebot_before_configuration(tmp_path):
    from websockets.asyncio.server import serve

    requests = []

    async def peer(websocket):
        packet = json.loads(await websocket.recv())
        requests.append(packet)
        await websocket.send(json.dumps({'status': 'ok', 'retcode': 0,
                                         'data': {'user_id': 90001, 'nickname': '脱敏 Bot'},
                                         'echo': packet['echo']}))
        await websocket.wait_closed()

    async with serve(peer, '127.0.0.1', 0) as server:
        port = server.sockets[0].getsockname()[1]
        app = create_setup_app(tmp_path, 'fixture-token', asyncio.Event())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1') as client:
            body = {'onebot': {'mode': 'forward_ws', 'ws_url': f'ws://127.0.0.1:{port}'}}
            assert (await client.post('/api/setup/platform', json=body)).status_code == 401
            client.headers['X-Setup-Token'] = 'fixture-token'
            result = await client.post('/api/setup/platform', json=body)
            assert result.status_code == 200, result.text
            assert result.json() == {'bot_id': 'onebot:90001'}
    assert len(requests) == 1 and requests[0]['action'] == 'get_login_info'
    assert not (tmp_path / 'lenbot.config.json').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 429])
async def test_setup_model_probe_makes_one_request_and_returns_original_error(tmp_path, status):
    response = (Path(__file__).parent / 'fixtures/next/model/response-text.json').read_bytes()
    requests = []

    async def peer(reader, writer):
        header = (await reader.readuntil(b'\r\n\r\n')).decode()
        fields = dict(line.split(': ', 1) for line in header.split('\r\n')[1:] if ': ' in line)
        requests.append(json.loads(await reader.readexactly(int(fields['Content-Length']))))
        body = response if status == 200 else b'{"error":{"message":"fixture provider rejected probe"}}'
        writer.write(f'HTTP/1.1 {status} Probe\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(peer, '127.0.0.1', 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        app = create_setup_app(tmp_path, 'fixture-token', asyncio.Event())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1',
                                     headers={'X-Setup-Token': 'fixture-token'}) as client:
            result = await client.post('/api/setup/model', json={
                'provider': {'api': 'openai-chat', 'base_url': f'http://127.0.0.1:{port}/v1', 'api_key': 'fixture'},
                'mind': {'provider': 'primary', 'model': 'fixture', 'context_window_tokens': 8192},
            })
            if status == 200:
                assert result.status_code == 200, result.text
                assert result.json()['text'] == json.loads(response)['choices'][0]['message']['content']
            else:
                assert result.status_code == 422 and 'fixture provider rejected probe' in result.text
    assert len(requests) == 1 and requests[0]['model'] == 'fixture'
    assert not (tmp_path / 'lenbot.config.json').exists()
