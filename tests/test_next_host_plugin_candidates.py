"""Candidate configuration is published with its source through panel HTTP operations."""

import asyncio
from io import BytesIO
import json
from pathlib import Path
import zipfile

import httpx

from len_bot.next.config import load_host_config
from len_bot.next.models.client import ChatModel
from len_bot.next.panel.app import create_app
from len_bot.next.panel.setup import FirstSetup, initialize
from len_bot.next.persona.profile import load_persona
from len_bot.next.runtime.network import NetworkRuntime
from len_bot.next.storage.store import Store


SAMPLE = Path(__file__).parent / 'fixtures/plugins/sample'


def package(version, *, text_value=False):
    manifest = (SAMPLE / 'plugin.toml').read_text().replace('version = "1.0.0"', f'version = "{version}"')
    if text_value:
        manifest = manifest.replace('type = "boolean"', 'type = "string"').replace('default = true', 'default = "brief"')
    content = BytesIO()
    with zipfile.ZipFile(content, 'w') as archive:
        archive.writestr('plugin.toml', manifest)
        archive.writestr('__init__.py', (SAMPLE / '__init__.py').read_bytes())
    return content.getvalue()


def test_candidate_cancel_disable_apply_and_rollback_preserve_matching_config(tmp_path):
    binding = {'provider': 'primary', 'model': 'fixture', 'context_window_tokens': 16000}
    initialize(tmp_path, FirstSetup.model_validate_json(json.dumps({
        'bot_id': 'onebot:90001', 'owners': ['onebot:70001'], 'timezone': 'UTC', 'delivery': 'simulated',
        'onebot': {'mode': 'forward_ws', 'ws_url': 'ws://127.0.0.1:9'},
        'provider': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1', 'api_key': 'synthetic-unused'},
        'compaction': {'input_tokens': 2000}, 'mind': binding, 'scene': 'onebot:group:80001',
        'panel_port': 8088, 'username': 'fixture', 'password': 'synthetic-password',
    })))
    config = load_host_config(tmp_path)

    async def run():
        with Store(config.database) as store:
            async with ChatModel(config.model_settings('mind')) as mind:
                scene = config.scene_config('onebot:group:80001')
                runtime = NetworkRuntime(config, [(scene, load_persona(scene.persona))], store, mind)
                app = create_app(config, runtime, root=tmp_path)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                    assert (await client.post('/api/auth/login', json={'username': 'fixture', 'password': 'synthetic-password'})).status_code == 200
                    endpoint = '/api/host/plugins/sample'

                    async def upload(version, *, text_value=False):
                        result = await client.post('/api/host/plugins/zip', files={'file': ('sample.zip', package(version, text_value=text_value))})
                        assert result.status_code == 200, result.text

                    async def configure(values, enabled=True):
                        result = await client.put(endpoint, json={'enabled': enabled, 'config': values})
                        assert result.status_code == 200, result.text

                    async def operate(action):
                        result = await client.post(endpoint + '/' + action)
                        assert result.status_code == 200, result.text
                        return result.json()

                    await upload('1.0.0')
                    await configure({'show_details': False})
                    await operate('apply')
                    await upload('2.0.0', text_value=True)
                    await configure({'show_details': 'expanded'})
                    assert load_host_config(tmp_path).plugins.configured['sample'] == {'show_details': False}
                    await operate('cancel')
                    assert load_host_config(tmp_path).plugins.configured['sample'] == {'show_details': False}
                    await upload('2.0.0', text_value=True)
                    await configure({'show_details': 'expanded'})
                    await configure({}, enabled=False)
                    applied = await operate('apply')
                    assert 'sample' in applied['saved']['disabled']
                    assert load_host_config(tmp_path).plugins.configured['sample'] == {'show_details': 'expanded'}
                    await upload('3.0.0', text_value=True)
                    await operate('apply')
                    await operate('rollback')
                    saved = load_host_config(tmp_path)
                    assert saved.plugins.configured['sample'] == {'show_details': 'expanded'}
                    assert 'sample' in saved.plugins.disabled
                if runtime.plugins is not None:
                    await runtime.plugins.close()

    asyncio.run(run())
