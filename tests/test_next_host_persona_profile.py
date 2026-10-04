"""The panel role form writes the four role files through the authenticated HTTP boundary."""
import asyncio
import json

import httpx
import yaml

from len_bot.next.config import load_host_config
from len_bot.next.panel.app import create_app
from len_bot.next.models.client import ChatModel
from len_bot.next.runtime.network import NetworkRuntime
from len_bot.next.persona.profile import load_persona
from len_bot.next.panel.setup import FirstSetup, initialize
from len_bot.next.storage.store import Store


def test_profile_form_rewrites_role_files_and_rejects_invalid_values(tmp_path):
    binding = {'provider': 'primary', 'model': 'fixture', 'context_window_tokens': 16000}
    initialize(tmp_path, FirstSetup.model_validate_json(json.dumps({
        'bot_qq': '90001', 'owner_qq': '70001', 'timezone': 'UTC', 'delivery': 'simulated',
        'onebot': {'mode': 'forward_ws', 'ws_url': 'ws://127.0.0.1:9'},
        'provider': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1', 'api_key': 'synthetic-unused'},
        'compaction': {'input_tokens': 2000}, 'mind': binding,  'scene': 'group:80001', 'persona_id': 'fixture',
        'persona_name': '合成角色', 'brief': '合成资料', 'voice_text': '简短', 'boundaries': '',
        'panel_port': 8088, 'username': 'fixture', 'password': 'synthetic-password',
    })))
    config = load_host_config(tmp_path)
    role = tmp_path/'personas/fixture'

    async def run():
        with Store(config.database) as store:
            async with ChatModel(config.model_settings('mind')) as mind:
                runtime = NetworkRuntime(config, [(config.scene_config('group:80001'), load_persona(config.scenes['group:80001'].persona))], store, mind)
                app = create_app(config, runtime, root=tmp_path)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                    endpoint = '/api/host/scenes/group:80001/persona-profile'
                    assert (await client.get(endpoint)).status_code == 401
                    assert (await client.post('/api/auth/login', json={'username': 'fixture', 'password': 'synthetic-password'})).status_code == 200
                    current = (await client.get(endpoint)).json()
                    assert current['profile']['name'] == '合成角色' and current['id'] == 'fixture'
                    tools = yaml.safe_load((role/'persona.yaml').read_text())['tools']
                    profile = {**current['profile'], 'name': '新名字', 'aliases': ['小新'], 'voice': '说话慢一点。\n',
                               'styles': [{'name': '温和', 'weight': 1.0}],
                               'examples': [{'context': '有人问好', 'line': '你好呀', 'tags': []}]}
                    saved = await client.put(endpoint, json={'directory': current['directory'], 'profile': profile})
                    assert saved.status_code == 200, saved.text
                    assert saved.json()['profile']['name'] == '新名字'
                    metadata = yaml.safe_load((role/'persona.yaml').read_text())
                    assert metadata['name'] == '新名字' and metadata['id'] == 'fixture' and metadata['tools'] == tools
                    assert (role/'voice.md').read_text() == '说话慢一点。\n'
                    assert yaml.safe_load((role/'examples.yaml').read_text()) == [{'context': '有人问好', 'line': '你好呀'}]
                    assert runtime.chats['group:80001'].persona.name == '合成角色'
                    draft = await client.post(endpoint + '/draft', json={'directory': current['directory'], 'profile': {**profile, 'name': '草稿名'}})
                    assert draft.status_code == 200 and yaml.safe_load(draft.json()['files']['persona.yaml'])['name'] == '草稿名'
                    assert yaml.safe_load((role/'persona.yaml').read_text())['name'] == '新名字'
                    before = {name: (role/name).read_text() for name in ('persona.yaml', 'voice.md', 'examples.yaml')}
                    blank = await client.put(endpoint, json={'directory': current['directory'], 'profile': {**profile, 'name': ' '}})
                    assert blank.status_code == 422
                    moved = await client.put(endpoint, json={'directory': str(tmp_path/'elsewhere'), 'profile': profile})
                    assert moved.status_code == 422
                    assert {name: (role/name).read_text() for name in before} == before
    asyncio.run(run())
