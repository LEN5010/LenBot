"""First-run configuration validation, no production connections."""
import asyncio
import json
from pathlib import Path

import httpx

from len_bot.next.config import load_host_config
from len_bot.next.persona import load_persona
from len_bot.next.setup import create_setup_app
from len_bot.web.auth import verify_password


def test_first_setup_validates_and_never_overwrites(tmp_path: Path):
    body = {
        'bot_qq':'90001','owner_qq':'70001','timezone':'Asia/Shanghai','delivery':'simulated',
        'onebot':{'mode':'forward_ws','ws_url':'ws://127.0.0.1:9','access_token':'synthetic-token'},
        'provider':{'api':'openai-chat','base_url':'http://127.0.0.1:9/v1','api_key':'synthetic-secret'},
        'mind':{'provider':'primary','model':'fixture','context_window_tokens':8192},
        'voice':{'provider':'primary','model':'fixture','context_window_tokens':8192},
        'scene':'group:80001','persona_id':'fixture','persona_name':'合成角色','brief':'测试设定',
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
            response = await client.post('/api/setup', json=body)
            assert response.status_code == 200, response.text
            assert complete.is_set() and 'synthetic-secret' not in response.text
            cfg = load_host_config(tmp_path)
            assert cfg.delivery == 'simulated' and cfg.models.roles.mind.model == 'fixture'
            assert load_persona(cfg.scenes['group:80001'].persona).name == '合成角色'
            assert verify_password(body['password'], cfg.panel.password_hash)
            before = (tmp_path/'lenbot.config.json').read_bytes()
            assert body['password'] not in before.decode()
            assert (await client.post('/api/setup', json=body)).status_code == 409
            assert (tmp_path/'lenbot.config.json').read_bytes() == before
    asyncio.run(exercise())
