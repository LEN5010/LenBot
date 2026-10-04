"""Role permission writes remain bound to the persona directory the operator read."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from len_bot.next.config import load_host_config
from len_bot.next.models.client import ChatModel
from len_bot.next.panel.app import create_app
from len_bot.next.persona.profile import load_persona
from len_bot.next.runtime.network import NetworkRuntime
from len_bot.next.storage.store import Store
from len_bot.web.auth import hash_password


@pytest.mark.parametrize('field', ['tools', 'skills'])
def test_permission_write_uses_selected_persona(tmp_path: Path, field: str) -> None:
    for name in ('first', 'second'):
        role = tmp_path / name
        role.mkdir()
        (role / 'persona.yaml').write_text(
            f'id: {name}\nname: {name}\nbrief: 合成角色\nbehavior: 隔离测试\n'
            'self_reference: [我]\naliases: []\ntools: []\nskills: []\nstyles: []\n', encoding='utf-8')
        (role / 'voice.md').write_text('简短。', encoding='utf-8')
        (role / 'boundaries.md').write_text('隔离测试。', encoding='utf-8')
        (role / 'examples.yaml').write_text('[]\n', encoding='utf-8')
    binding = {'provider': 'fixture', 'model': 'unused', 'context_window_tokens': 8192}
    source = {"compaction": {"input_tokens": 2000},
        'mode': 'isolated-multi', 'bot_qq': '90001', 'timezone': 'UTC',
        'database': 'state.sqlite3', 'delivery': 'simulated',
        'onebot': {'mode': 'forward_ws', 'ws_url': 'ws://127.0.0.1:9'},
        'panel': {'host': '127.0.0.1', 'port': 0, 'username': 'fixture',
                  'password_hash': hash_password('synthetic-password')},
        'models': {'providers': {'fixture': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1',
                                             'api_key': 'synthetic-unused'}},
                   'roles': {'mind': binding}},
        'scenes': {'group:80001': {'persona': 'first'}, 'group:80002': {'persona': 'second'}},
    }
    (tmp_path / 'lenbot.config.json').write_text(json.dumps(source), encoding='utf-8')
    config = load_host_config(tmp_path)

    async def exercise() -> None:
        with Store(config.database) as store:
            async with ChatModel(config.model_settings('mind')) as mind:
                runtime = NetworkRuntime(config, [
                    (config.scene_config(scene), load_persona(settings.persona))
                    for scene, settings in config.scenes.items()
                ], store, mind)
                app = create_app(config, runtime, root=tmp_path)
                try:
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as client:
                        assert (await client.post('/api/auth/login', json={
                            'username': 'fixture', 'password': 'synthetic-password'})).status_code == 200
                        read_url = f'/api/host/{"capabilities" if field == "tools" else "skills"}?scene=group:80001'
                        write_url = f'/api/host/scenes/group:80001/role-{field}'
                        selected = (await client.get(read_url)).json()[f'role_{field}']
                        assert selected['persona']['name'] == 'first'
                        assert selected['restart_required'] is False
                        assert (await client.put('/api/host/settings/scenes/group:80001/persona',
                                                 json={'persona': 'second'})).status_code == 200
                        before = {name: (tmp_path / name / 'persona.yaml').read_bytes() for name in ('first', 'second')}
                        stale = await client.put(write_url, json={'directory': selected['directory'], field: 'all'})
                        assert stale.status_code == 422
                        assert {name: (tmp_path / name / 'persona.yaml').read_bytes() for name in before} == before
                        current = (await client.get(read_url)).json()[f'role_{field}']
                        assert current['persona']['name'] == 'second'
                        assert current['restart_required'] is True
                        saved = await client.put(write_url, json={'directory': current['directory'], field: 'all'})
                        assert saved.status_code == 200, saved.text
                        assert saved.json()['persona']['name'] == 'second'
                        assert saved.json()['affected_scenes'] == ['group:80001', 'group:80002']
                        assert getattr(load_persona(tmp_path / 'first'), field) == []
                        assert getattr(load_persona(tmp_path / 'second'), field) == 'all'
                finally:
                    await app.state.trials.close()
                    await runtime.platform.close()

    asyncio.run(exercise())
