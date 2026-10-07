"""Model → host boundary of native privileged tools: the requester is read from a chosen real message."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from len_bot.next.chat.host_manage import HOST_MANAGE_TOOL
from len_bot.next.chat.scene_control import SCENE_CONTROL_TOOL
from len_bot.next.chat.schedule import SCHEDULE_TOOLS
from len_bot.next.chat.session import Chat
from len_bot.next.config import load_config
from len_bot.next.models.client import ChatModel, ToolCall
from len_bot.next.persona.profile import load_persona
from len_bot.next.platform.onebot_messages import parse_message
from len_bot.next.storage.store import Store
from len_bot.next.work.tools import DELEGATE_TOOL, TASK_TOOL

SCENE = 'onebot:group:80001'


def _instance(root: Path):
    role = root / 'role'
    role.mkdir(parents=True)
    (role / 'persona.yaml').write_text(json.dumps({
        'id': 'synthetic', 'name': '合成角色', 'brief': '仅用于工具边界。', 'behavior': '正常对话。',
        'self_reference': ['我'], 'aliases': [], 'tools': 'all', 'skills': [], 'styles': []}, ensure_ascii=False))
    for name, body in [('voice.md', '简短。'), ('boundaries.md', '合成。'), ('examples.yaml', '[]\n')]:
        (role / name).write_text(body, encoding='utf-8')
    (root / 'lenbot.config.json').write_text(json.dumps({
        'compaction': {'input_tokens': 2000}, 'mode': 'isolated', 'scene': SCENE, 'bot_id': 'onebot:90001',
        'owners': ['onebot:70001'], 'permissions': {'blacklist': ['onebot:70005']}, 'timezone': 'UTC',
        'database': 'state.sqlite3', 'persona': str(role), 'voice_mode': 'direct',
        'models': {'providers': {'synthetic': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1',
                                               'api_key': 'synthetic-unused-key'}},
                   'roles': {'mind': {'provider': 'synthetic', 'model': 'synthetic-mind', 'context_window_tokens': 8192}}},
    }, ensure_ascii=False), encoding='utf-8')
    return load_config(root), load_persona(role)


def _receive(store: Store, user: int, message_id: int, text: str = '合成请求') -> None:
    raw = {'post_type': 'message', 'message_type': 'group', 'self_id': 90001, 'group_id': 80001,
           'user_id': user, 'message_id': message_id, 'time': 1790000000,
           'sender': {'nickname': '合成成员', 'role': 'member'}, 'message': [{'type': 'text', 'data': {'text': text}}]}
    store.enqueue(parse_message(raw, own_message_ids=set()), raw, 1790000000)


async def _unused_wait(_: float) -> str:
    return 'unused'


def _discover(chat: Chat, *names: str) -> None:
    chat.store.save_discovered_tools(SCENE, list(names))
    chat.toolset.discovered_tools = set(chat.store.load_discovered_tools(SCENE))


@pytest.mark.parametrize('tool', [HOST_MANAGE_TOOL, SCENE_CONTROL_TOOL, *SCHEDULE_TOOLS[::2], DELEGATE_TOOL, TASK_TOOL],
                         ids=lambda tool: tool['function']['name'])
def test_model_facing_schemas_ask_for_a_message_not_an_account(tool):
    properties = tool['function']['parameters']['properties']
    assert 'requester' not in properties and 'source_message_id' in properties


@pytest.mark.asyncio
async def test_schedule_requester_is_the_chosen_message_sender(tmp_path):
    config, persona = _instance(tmp_path)
    with Store(config.database) as store:
        _receive(store, 70001, 50001)
        _receive(store, 70005, 50005)
        async with ChatModel(config.model_settings('mind')) as mind:
            chat = Chat(config, persona, store, mind)
            _discover(chat, 'schedule', 'schedule_list')
            create = {'when': '2099-01-01T00:00:00+00:00', 'note': '合成提醒', 'for': 'self'}
            result, _, _ = await chat.toolset.execute(
                'turn', ToolCall('c1', 'schedule', {**create, 'source_message_id': '50001'}), _unused_wait)
            assert '账号 onebot:70001' in result

            with pytest.raises(ValueError, match='requester'):
                await chat.toolset.execute(
                    'turn', ToolCall('c2', 'schedule', {**create, 'requester': 'onebot:70001'}), _unused_wait)
            with pytest.raises(ValueError, match='source_message_id'):
                await chat.toolset.execute(
                    'turn', ToolCall('c3', 'schedule', {**create, 'source_message_id': '59999'}), _unused_wait)
            with pytest.raises(PermissionError, match='黑名单'):
                await chat.toolset.execute(
                    'turn', ToolCall('c4', 'schedule', {**create, 'source_message_id': '50005'}), _unused_wait)

            autonomous, _, _ = await chat.toolset.execute(
                'turn', ToolCall('c5', 'schedule', {**create, 'source_message_id': None}), _unused_wait)
            assert 'Bot 自主' in autonomous


@pytest.mark.asyncio
async def test_control_and_management_receive_the_host_read_requester(tmp_path):
    config, persona = _instance(tmp_path)
    with Store(config.database) as store:
        _receive(store, 70002, 50002)
        async with ChatModel(config.model_settings('mind')) as mind:
            chat = Chat(config, persona, store, mind)
            seen = []

            class Management:
                async def execute(self, scene, arguments):
                    seen.append(('host_manage', arguments.requester))
                    return {'action': arguments.action}

            chat.toolset.scene_control = lambda arguments: seen.append(('scene_control', arguments.requester)) or {}
            chat.toolset.host_management = Management()
            chat.set_external_tools([])
            _discover(chat, 'scene_control', 'host_manage')

            await chat.toolset.execute('turn', ToolCall(
                'c1', 'scene_control', {'action': 'quiet', 'seconds': 60, 'source_message_id': '50002'}), _unused_wait)
            await chat.toolset.execute('turn', ToolCall(
                'c2', 'host_manage', {'action': 'status', 'source_message_id': '50002'}), _unused_wait)
            assert seen == [('scene_control', 'onebot:70002'), ('host_manage', 'onebot:70002')]

            # Without a chosen message there is no requester; the error names the missing field.
            with pytest.raises(ValueError, match='requester'):
                await chat.toolset.execute('turn', ToolCall('c3', 'host_manage', {'action': 'status'}), _unused_wait)
            with pytest.raises(ValidationError, match='source_message_id'):
                await chat.toolset.execute('turn', ToolCall('c4', 'scene_control', {'action': 'resume'}), _unused_wait)
