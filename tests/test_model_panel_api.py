import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from len_bot.cognition.gateway import GatewayResponse, ToolCall
from len_bot.cognition.providers import ProviderConfig, ProviderRegistry
from len_bot.web.auth import get_current_user
from len_bot.web.routes import models


class ConfigStore:
    def __init__(self):
        self.saved = []

    async def save_dynamic_config(self, key, value):
        self.saved.append((key, value))


def panel(registry):
    app = FastAPI()
    app.state.runtime = SimpleNamespace(provider_registry=registry, config_update_lock=asyncio.Lock(), event_store=ConfigStore(),
        query_service=SimpleNamespace(providers=registry.snapshot, provider_models=registry.list_models, metrics=lambda: {}))
    app.include_router(models.router)
    return app


@pytest.mark.asyncio
async def test_model_panel_accepts_unconfigured_provider_and_only_new_routing():
    registry = ProviderRegistry()
    app = panel(registry)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/models/providers')).status_code == 401
        app.dependency_overrides[get_current_user] = lambda: 'operator:test'
        response = await client.post('/api/models/providers', json={'id': 'fixture', 'base_url': 'https://example.invalid/v1', 'api_key': 'fixture-key-not-real'})
        assert response.status_code == 200 and registry.export()['routing'] is None
        public = (await client.get('/api/models/providers')).json()
        assert 'fixture-key-not-real' not in json.dumps(public)
        assert (await client.post('/api/models/routing', json={'normal_provider_id': 'fixture', 'normal_model': 'old'})).status_code == 422
        routing = {'conversation': {'provider_id': 'fixture', 'model': 'chat', 'reasoning_effort': 'low'},
                   'work': {'provider_id': 'fixture', 'model': 'work', 'reasoning_effort': 'high'}}
        assert (await client.post('/api/models/routing', json=routing)).status_code == 200
        assert (await client.get('/api/models/routing')).json() == routing
        assert (await client.delete('/api/models/providers/fixture')).status_code == 409
        selected = await client.post('/api/models/providers/fixture/models', json={'models': []})
        assert selected.json()['models'] == ['chat', 'work']


@pytest.mark.asyncio
async def test_native_capability_check_keeps_opaque_continuation_without_scene_access(monkeypatch):
    registry = ProviderRegistry()
    await registry.apply_update([ProviderConfig(id='fixture', base_url='https://example.invalid/v1', api_key='fixture-key')], None)
    app = panel(registry)
    app.dependency_overrides[get_current_user] = lambda: 'operator:test'
    continuation = {'role': 'assistant', 'tool_calls': [{'id': 'call-1', 'type': 'function',
        'function': {'name': 'record_image_number', 'arguments': '{"number":37}'},
        'extra_content': {'google': {'thought_signature': 'opaque_fixture'}}}]}
    closed = []
    class Client:
        async def close(self):
            closed.append(True)
    class Gateway:
        def __init__(self, binding, max_output_tokens):
            assert binding.reasoning_effort == 'low'
            self.calls = 0
        async def complete(self, messages, tools, tool_choice):
            self.calls += 1
            if self.calls == 1:
                assert messages[0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
                assert tool_choice['function']['name'] == 'record_image_number'
                return GatewayResponse(continuation, (ToolCall('call-1', 'record_image_number', '{"number":37}'),), 'tool_calls', {}, 1)
            assert messages[1] == continuation
            receipt = json.loads(messages[2]['content'])
            assert messages[2]['tool_call_id'] == 'call-1'
            assert tool_choice['function']['name'] == 'finish_probe'
            return GatewayResponse({'role': 'assistant'}, (ToolCall('call-2', 'finish_probe', json.dumps({'number': 37, **receipt})),), 'tool_calls', {}, 1)
    monkeypatch.setattr(models, 'AsyncOpenAI', lambda **kwargs: Client())
    monkeypatch.setattr(models, 'ModelGateway', Gateway)
    monkeypatch.setattr(models.secrets, 'randbelow', lambda limit: 27)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/models/test', json={'provider_id': 'fixture', 'model': 'fixture-model', 'reasoning_effort': 'low'})
    assert response.json()['success'] and all(response.json()['checks'].values())
    assert 'opaque_fixture' not in response.text and 'fixture-key' not in response.text
    assert closed == [True] and app.state.runtime.event_store.saved == []


@pytest.mark.asyncio
async def test_model_probe_error_redacts_credentials(monkeypatch):
    registry = ProviderRegistry()
    await registry.apply_update([ProviderConfig(id='fixture', base_url='https://example.invalid/v1', api_key='fixture-secret-value')], None)
    app = panel(registry)
    app.dependency_overrides[get_current_user] = lambda: 'operator:test'
    class Client:
        async def close(self):
            pass
    class BrokenGateway:
        def __init__(self, *args, **kwargs):
            pass
        async def complete(self, *args, **kwargs):
            raise RuntimeError('provider rejected fixture-secret-value')
    monkeypatch.setattr(models, 'AsyncOpenAI', lambda **kwargs: Client())
    monkeypatch.setattr(models, 'ModelGateway', BrokenGateway)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/models/test', json={'provider_id': 'fixture', 'model': 'test'})
    assert response.status_code == 200 and not response.json()['success']
    assert 'fixture-secret-value' not in response.text
    assert '[已隐藏密钥]' in response.json()['error']
