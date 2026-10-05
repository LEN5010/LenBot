"""Management identity and root-file configuration boundaries, without model requests."""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import shutil

import httpx
from jsonschema import Draft202012Validator
import pytest

from len_bot.next.chat.host_manage import HostManageArguments
from len_bot.next.config import load_host_config
from len_bot.next.memory.service import open_memory
from len_bot.next.models.client import ChatModel
from len_bot.next.panel.app import create_app
from len_bot.next.persona.profile import load_persona
from len_bot.next.runtime.lifecycle import HostLifecycle
from len_bot.next.runtime.management import require_management
from len_bot.next.runtime.network import NetworkRuntime
from len_bot.next.storage.store import Store
from len_bot.web.auth import hash_password


def instance(root: Path) -> dict:
    persona = root / 'persona'
    persona.mkdir()
    (persona / 'persona.yaml').write_text(
        'id: management-fixture\nname: 配置角色\nbrief: 配置边界核对\nbehavior: 简短表达\n'
        'self_reference: [我]\naliases: []\ntools: [say, tool_search, host_manage]\nskills: []\nstyles: []\n')
    (persona / 'voice.md').write_text('简短表达。')
    (persona / 'boundaries.md').write_text('配置边界核对。')
    (persona / 'examples.yaml').write_text('[]\n')
    source = {
        'mode': 'isolated-multi', 'bot_id': 'onebot:90001', 'owners': ['onebot:70001'], 'timezone': 'UTC',
        'database': 'chat.sqlite3', 'delivery': 'simulated',
        'permissions': {'admins': ['onebot:70002'], 'whitelist': ['onebot:70004'], 'blacklist': ['onebot:70005']},
        'onebot': {'mode': 'forward_ws', 'ws_url': 'ws://127.0.0.1:9', 'access_token': 'transport-marker'},
        'panel': {'host': '127.0.0.1', 'port': 0, 'username': 'operator',
                  'password_hash': hash_password('fixture-password', salt='fixture-salt')},
        'compaction': {'input_tokens': 2000},
        'models': {'providers': {'fixture': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1',
                                            'api_key': 'model-secret-marker'}},
                   'roles': {'mind': {'provider': 'fixture', 'model': 'fixture', 'context_window_tokens': 8192}}},
        'memory': {'backend': 'local', 'local': {'directory': 'memory'}},
        'scenes': {'onebot:group:80001': {'persona': 'persona', 'permissions': {'admins': ['onebot:70003']},
                                  'attention': {'keywords': ['原关键词'], 'only_direct': False}},
                   'onebot:group:80002': {'persona': 'persona', 'permissions': {'blacklist': ['onebot:70002']}}},
    }
    (root / 'lenbot.config.json').write_text(json.dumps(source, ensure_ascii=False))
    return source


@asynccontextmanager
async def running(root: Path):
    config = load_host_config(root)
    persona = load_persona(config.scenes['onebot:group:80001'].persona)
    with Store(config.database) as store:
        async with ChatModel(config.model_settings('mind')) as mind, open_memory(config, store) as memory:
            runtime = NetworkRuntime(config, [(config.scene_config(scene), persona) for scene in config.scenes],
                                     store, mind, memory=memory, lifecycle=HostLifecycle())
            try:
                yield runtime
            finally:
                if runtime.plugins is not None:
                    await runtime.plugins.close()


def arguments(action, **values):
    return HostManageArguments.model_validate({'action': action, 'requester': 'onebot:70001', **values})


def test_only_global_owner_and_admin_have_management_permission(tmp_path):
    instance(tmp_path)
    config = load_host_config(tmp_path)
    for requester in ('onebot:70001', 'onebot:70002'):
        require_management(config, 'onebot:group:80001', requester)
    for requester in ('onebot:70003', 'onebot:70004', 'onebot:70005', 'onebot:70006', 'onebot:90001'):
        with pytest.raises(PermissionError):
            require_management(config, 'onebot:group:80001', requester)
    with pytest.raises(PermissionError):
        require_management(config, 'onebot:group:80002', 'onebot:70002')


@pytest.mark.asyncio
async def test_updates_preserve_unrelated_configuration_and_pending_panel_changes(tmp_path):
    source = instance(tmp_path)
    async with running(tmp_path) as runtime:
        manager = runtime.management
        selected = await manager.execute('onebot:group:80001', arguments('status', scene='onebot:group:80001'))
        assert selected['scenes'] == ['onebot:group:80001']
        app = create_app(runtime.config, runtime, root=tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            login = await client.post('/api/auth/login', json={'username': 'operator', 'password': 'fixture-password'})
            assert login.status_code == 200
            saved, panel = await asyncio.gather(
                manager.execute('onebot:group:80001', arguments('update', section='scene',
                    changes={'attention': {'only_direct': True, 'keywords': ['新关键词']}})),
                client.put('/api/host/settings/retention', json={'retention': {'request_days': 3}}),
            )
            assert panel.status_code == 200, panel.text
        assert saved['restart_required'] is True
        assert saved['running']['attention']['only_direct'] is False
        assert saved['saved']['attention']['only_direct'] is True
        assert manager.lifecycle.intent == 'running'
        raw = json.loads((tmp_path / 'lenbot.config.json').read_text())
        assert raw['retention']['request_days'] == 3
        assert raw['scenes']['onebot:group:80001']['attention']['keywords'] == ['新关键词']
        assert raw['scenes']['onebot:group:80002'] == source['scenes']['onebot:group:80002']
        assert raw['models'] == source['models'] and raw['onebot'] == source['onebot']

        changed = await manager.execute('onebot:group:80001', arguments('update', section='models', role='mind',
                                                                changes={'temperature': 0.3}))
        assert changed['saved']['temperature'] == 0.3
        assert 'model-secret-marker' not in json.dumps(changed)
        before = (tmp_path / 'lenbot.config.json').read_bytes()
        for section, changes, extra in (
            ('scene', {'permissions': {'admins': ['onebot:70006']}}, {}),
            ('tasks', {'manage_roles': ['member']}, {}),
            ('models', {'model': 'other'}, {'role': 'mind'}),
            ('memory', {'local': {'directory': 'other'}}, {}),
            ('chat', {'max_steps': 0}, {}),
        ):
            with pytest.raises(ValueError):
                await manager.execute('onebot:group:80001', arguments('update', section=section, changes=changes, **extra))
            assert (tmp_path / 'lenbot.config.json').read_bytes() == before
        with pytest.raises(PermissionError):
            await manager.execute('onebot:group:80001', arguments('status', requester='onebot:70003'))
        with pytest.raises(RuntimeError, match='启动器'):
            await manager.execute('onebot:group:80001', arguments('restart'))
        assert (tmp_path / 'lenbot.config.json').read_bytes() == before


@pytest.mark.asyncio
async def test_described_settings_are_valid_schemas_and_plugin_secrets_are_preserved(tmp_path):
    source = instance(tmp_path)
    plugin = tmp_path / 'plugins' / 'clock_fixture'
    original = Path(__file__).parents[1] / 'src/len_bot/next/builtin_plugins/clock'
    shutil.copytree(original, plugin, ignore=shutil.ignore_patterns('__pycache__'))
    manifest = (plugin / 'plugin.toml').read_text().replace('name = "clock"', 'name = "clock_fixture"')
    manifest += '\n[config.token]\ntype = "secret"\ndescription = "服务凭据"\n'
    (plugin / 'plugin.toml').write_text(manifest)
    source['plugins'] = {'paths': ['plugins'], 'disabled': ['clock_fixture'],
                         'clock_fixture': {'show_seconds': True, 'token': 'plugin-secret-marker'}}
    (tmp_path / 'lenbot.config.json').write_text(json.dumps(source, ensure_ascii=False))
    async with running(tmp_path) as runtime:
        manager = runtime.management
        for section in ('chat', 'scene', 'learning', 'schedules', 'tasks', 'memory', 'web_read',
                        'web_search', 'worker', 'limits', 'retention', 'scene_plugins'):
            described = await manager.execute('onebot:group:80001', arguments('describe', section=section))
            Draft202012Validator.check_schema(described['schema'])
            assert 'model-secret-marker' not in json.dumps(described)
        described = await manager.execute('onebot:group:80001', arguments('describe', section='plugin', plugin='clock_fixture'))
        Draft202012Validator.check_schema(described['schema'])
        assert 'plugin-secret-marker' not in json.dumps(described)
        saved = await manager.execute('onebot:group:80001', arguments('update', section='plugin', plugin='clock_fixture',
                                                             changes={'config': {'show_seconds': False}}))
        assert saved['applied'] is True
        assert saved['saved']['config'] == {'show_seconds': False}
        assert saved['saved']['enabled'] is False
        assert 'plugin-secret-marker' not in json.dumps(saved)
        raw = json.loads((tmp_path / 'lenbot.config.json').read_text())
        assert raw['plugins']['clock_fixture']['token'] == 'plugin-secret-marker'
        selected = await manager.execute('onebot:group:80001', arguments('update', section='scene_plugins',
                                                                 changes={'plugins': ['clock_fixture']}))
        assert selected['applied'] is True and selected['restart_required'] is False
        assert runtime.config.scenes['onebot:group:80001'].plugins == ['clock_fixture']
        before = (tmp_path / 'lenbot.config.json').read_bytes()
        with pytest.raises(ValueError, match='非秘密'):
            await manager.execute('onebot:group:80001', arguments('update', section='plugin', plugin='clock_fixture',
                changes={'config': {'token': 'replacement'}}))
        assert (tmp_path / 'lenbot.config.json').read_bytes() == before


@pytest.mark.parametrize('removed', ['memory_transfer', 'persona_memory_export', 'replay_memory'])
def test_removed_configuration_requires_explicit_offline_cleanup(tmp_path, removed):
    source = instance(tmp_path)
    source[removed] = None
    (tmp_path / 'lenbot.config.json').write_text(json.dumps(source))
    with pytest.raises(ValueError, match=removed):
        load_host_config(tmp_path)
