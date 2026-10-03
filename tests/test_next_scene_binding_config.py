"""Scene configuration writes through authenticated public HTTP boundaries."""
import asyncio
import json
import shutil

import httpx

from len_bot.next.config import load_host_config
from len_bot.next.panel.app import create_app
from len_bot.next.models.client import ChatModel
from len_bot.next.runtime.network import NetworkRuntime
from len_bot.next.persona.profile import load_persona
from len_bot.next.panel.setup import FirstSetup, initialize
from len_bot.next.storage.store import Store


def test_scene_bindings_validate_before_save_and_keep_running_snapshot(tmp_path):
    binding = {'provider': 'primary', 'model': 'fixture', 'context_window_tokens': 16000}
    initialize(tmp_path, FirstSetup.model_validate_json(json.dumps({
        'bot_qq': '90001', 'owner_qq': '70001', 'timezone': 'UTC', 'delivery': 'simulated',
        'onebot': {'mode': 'forward_ws', 'ws_url': 'ws://127.0.0.1:9'},
        'provider': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1', 'api_key': 'synthetic-unused'},
        'mind': binding, 'voice': binding, 'scene': 'group:80001', 'persona_id': 'fixture',
        'persona_name': '合成角色', 'brief': '合成资料', 'voice_text': '简短', 'boundaries': '',
        'panel_port': 8088, 'username': 'fixture', 'password': 'synthetic-password',
    })))
    shutil.copytree(tmp_path/'personas/fixture', tmp_path/'personas/other')
    config = load_host_config(tmp_path)
    async def run():
        with Store(config.database) as store:
            async with ChatModel(config.model_settings('mind')) as mind, ChatModel(config.model_settings('voice')) as voice:
                runtime = NetworkRuntime(config, [(config.scene_config('group:80001'), load_persona(config.scenes['group:80001'].persona))], store, mind, voice)
                app = create_app(config, runtime, root=tmp_path)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                    endpoint = '/api/host/settings/scenes'
                    for method, path in [('POST', endpoint), ('PUT', endpoint+'/group:80001/persona'), ('DELETE', endpoint+'/group:80001')]:
                        assert (await client.request(method, path, json={})).status_code == 401
                    for path in ['/api/host/usage', '/api/host/log-files', '/api/host/scenes/group:80001/notices',
                                 '/api/host/scenes/group:80001/turns/absent/export', '/api/host/tasks/1/export?scene=group:80001']:
                        assert (await client.get(path)).status_code == 401
                    assert (await client.put('/api/host/settings/processing', json={})).status_code == 401
                    assert (await client.post('/api/host/scenes/group:80001/history/new-context?confirmed=true')).status_code == 401
                    assert (await client.post('/api/auth/login', json={'username': 'fixture', 'password': 'synthetic-password'})).status_code == 200
                    processing = (await client.get('/api/host/settings')).json()['saved']['processing']
                    before_processing = (tmp_path/'lenbot.config.json').read_bytes()
                    invalid = json.loads(json.dumps(processing)); invalid['compaction']['trigger_ratio'] = 1
                    assert (await client.put('/api/host/settings/processing', json=invalid)).status_code == 422
                    invalid = json.loads(json.dumps(processing)); invalid['logging'] = {'directory': '../outside', 'retention_days': 14, 'level': 'INFO'}
                    assert (await client.put('/api/host/settings/processing', json=invalid)).status_code == 422
                    assert (tmp_path/'lenbot.config.json').read_bytes() == before_processing
                    processing['audio']['wait_seconds'] = 3
                    saved_processing = await client.put('/api/host/settings/processing', json=processing)
                    assert saved_processing.status_code == 200, saved_processing.text
                    assert saved_processing.json()['restart_required']['processing'] is True
                    assert saved_processing.json()['running']['processing']['audio']['wait_seconds'] == 2
                    assert (await client.get('/api/host/log-files/../../lenbot.config.json')).status_code != 200
                    assert (await client.post('/api/host/scenes/group:80001/history/new-context')).status_code == 422
                    async with runtime.runners['group:80001'].execution:
                        assert (await client.post('/api/host/scenes/group:80001/history/new-context?confirmed=true')).status_code == 409
                    knowledge = tmp_path/'personas/fixture/knowledge'
                    knowledge.mkdir(); (knowledge/'facts.md').write_text('合成新资料')
                    files = await client.get('/api/host/scenes/group:80001/persona-files')
                    assert files.status_code == 200 and files.json()['restart_required'] is True
                    before = (tmp_path/'lenbot.config.json').read_bytes()
                    assert (await client.post(endpoint, json={'scene': 'bad', 'persona': 'personas/fixture'})).status_code == 422
                    assert (await client.post(endpoint, json={'scene': 'group:80002', 'persona': 'missing'})).status_code == 500
                    assert (tmp_path/'lenbot.config.json').read_bytes() == before
                    created = await client.post(endpoint, json={'scene': 'group:80002', 'persona': 'personas/fixture'})
                    assert created.status_code == 200, created.text
                    snapshot = created.json()
                    assert 'group:80002' not in snapshot['running']['scenes']
                    assert snapshot['saved']['scenes']['group:80002']['attention']['only_direct'] is True
                    assert snapshot['restart_required']['scenes']['group:80002'] is True
                    assert (await client.post(endpoint, json={'scene': 'group:80002', 'persona': 'personas/fixture'})).status_code == 422
                    rebound = await client.put(endpoint+'/group:80001/persona', json={'persona': 'personas/other'})
                    assert rebound.status_code == 200, rebound.text
                    assert rebound.json()['running']['scenes']['group:80001']['persona'].endswith('/fixture')
                    assert rebound.json()['saved']['scenes']['group:80001']['persona'].endswith('/other')
                    assert rebound.json()['restart_required']['scenes']['group:80001'] is True
                    assert (await client.delete(endpoint+'/group:80001')).status_code == 200
                    removed = await client.get('/api/host/scenes/group:80001/persona-files')
                    assert removed.status_code == 422 and '移除' in removed.text
                    assert (await client.get('/api/host/settings/scenes/group:80001/learning')).status_code == 404
                    before_last = (tmp_path/'lenbot.config.json').read_bytes()
                    assert (await client.delete(endpoint+'/group:80002')).status_code == 422
                    assert (tmp_path/'lenbot.config.json').read_bytes() == before_last
                    assert (tmp_path/'personas/fixture/persona.yaml').exists()
                    assert runtime.chats['group:80001'].persona.id == 'fixture'
    asyncio.run(run())
