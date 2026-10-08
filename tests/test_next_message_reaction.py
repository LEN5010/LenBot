"""Shared NapCat/SnowLuma action envelopes over a real loopback OneBot connection."""

import json

import pytest
from websockets.asyncio.server import serve

from len_bot.next.chat.session import Chat
from len_bot.next.chat.tools import build_tools
from len_bot.next.config import load_host_config
from len_bot.next.configuration.onebot import OneBotForward
from len_bot.next.models.client import ChatModel
from len_bot.next.persona.profile import load_persona
from len_bot.next.platform.onebot import OneBot
from len_bot.next.platform.onebot_messages import parse_message
from len_bot.next.platform.platform_tools import MessageReactionArguments, message_reaction
from len_bot.next.storage.store import Store
from test_next_host_manage_permissions_config import instance
from test_next_model import recorded_response, recorded_text_response
from test_next_native_models import peer_server


SCENE = 'onebot:group:80001'


def incoming(group=80001):
    return {'post_type': 'message', 'message_type': 'group', 'self_id': 90001,
            'group_id': group, 'user_id': 70001, 'message_id': -492111658, 'time': 1790000000,
            'sender': {'nickname': '脱敏群友', 'role': 'member'},
            'message': [{'type': 'text', 'data': {'text': '接个梗'}}]}


@pytest.mark.asyncio
@pytest.mark.parametrize('envelope', [
    {'status': 'ok', 'retcode': 0, 'data': None},
    {'status': 'ok', 'retcode': 0, 'data': {'result': 0, 'errMsg': ''}},
    {'status': 'ok', 'retcode': 0, 'data': {'result': True}},
    {'status': 'failed', 'retcode': 1200, 'data': None, 'wording': 'message not found'},
    {'status': 'ok', 'retcode': 0, 'data': {'result': -1, 'errMsg': 'operation failed'}},
])
async def test_reaction_receipt_finishes_turn_without_sending_a_new_message(tmp_path, envelope):
    requests, models = [], []
    first = recorded_response()
    tool = first['choices'][0]['message']['tool_calls'][0]
    tool['function'] = {'name': 'message_reaction', 'arguments': json.dumps({'message': '-492111658', 'emoji_id': '66'})}
    model_server = await peer_server([(200, 'application/json', json.dumps(body).encode())
                                    for body in [first, recorded_text_response()]], models)

    async def peer(websocket):
        async for frame in websocket:
            request = json.loads(frame)
            requests.append(request)
            reply = ({'status': 'ok', 'retcode': 0, 'data': {'user_id': 90001, 'nickname': '脱敏 Bot'}}
                     if request['action'] == 'get_login_info' else envelope)
            await websocket.send(json.dumps({**reply, 'echo': request['echo']}))

    async with model_server, serve(peer, '127.0.0.1', 0) as server:
        source = instance(tmp_path)
        source.pop('memory')
        source['delivery'] = 'onebot'
        source['compaction']['input_tokens'] = 6000
        source['models']['providers']['fixture']['base_url'] = f'http://127.0.0.1:{model_server.sockets[0].getsockname()[1]}/v1'
        source['onebot']['ws_url'] = f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}'
        (tmp_path / 'lenbot.config.json').write_text(json.dumps(source))
        role = tmp_path / 'persona' / 'persona.yaml'
        role.write_text(role.read_text().replace('[say, tool_search, host_manage]', '[say, tool_search, message_reaction]'))
        config = load_host_config(tmp_path).scene_config(SCENE)
        persona = load_persona(config.persona)
        with Store(config.database) as store:
            raw = incoming()
            seq = store.enqueue(parse_message(raw, own_message_ids=set()), raw, store.now())
            store.save_discovered_tools(SCENE, ['message_reaction'])
            async with ChatModel(config.model_settings('mind')) as mind, OneBot(
                OneBotForward(**source['onebot']), bot_id=config.bot_id,
                on_event=lambda event: None, on_error=lambda error: None,
            ) as bot:
                chat = Chat(config, persona, store, mind, send_message=bot.send_message, platform_call=bot.call)

                async def append_new(called, turn):
                    return False

                async def wait_for_messages(seconds):
                    return '没有新消息'

                result = await chat.run_turn(batch=(seq, ['接个梗']), append_new=append_new,
                    wait_for_messages=wait_for_messages, attention_state={})
            success = envelope['status'] == 'ok' and envelope['data'] != {'result': -1, 'errMsg': 'operation failed'}
            assert result['status'] == 'settled' and result['failed_tools'] == (0 if success else 1), result
            assert len(models) == (1 if success else 2)
            assert len(store.recent(SCENE, 20)) == 1
            turn = store.turn_detail(SCENE, result['turn_id'])['turn']
            assert (turn['first_expression_at'] is not None) == success
    assert requests[-1]['action'] == 'set_msg_emoji_like'
    assert requests[-1]['params'] == {'message_id': -492111658, 'emoji_id': '66', 'set': True}
    assert all(request['action'] != 'send_group_msg' for request in requests)


@pytest.mark.asyncio
async def test_reaction_target_and_role_are_scene_scoped(tmp_path):
    source = instance(tmp_path)
    source['delivery'] = 'onebot'
    (tmp_path / 'lenbot.config.json').write_text(json.dumps(source))
    config = load_host_config(tmp_path).scene_config(SCENE)
    persona = load_persona(config.persona)
    allowed = persona.model_copy(update={'tools': ['message_reaction', 'tool_search']})
    assert 'message_reaction' in {tool['function']['name'] for tool in build_tools(config, allowed, platform=True)}
    for settings, role in [(config, persona), (config.model_copy(update={'scene': 'onebot:private:70001'}), allowed),
                           (config.model_copy(update={'delivery': 'simulated'}), allowed)]:
        assert 'message_reaction' not in {tool['function']['name'] for tool in build_tools(settings, role, platform=True)}
    with Store(config.database) as store:
        raw = incoming(80002)
        store.enqueue(parse_message(raw, own_message_ids=set()), raw, store.now())
        with pytest.raises(ValueError, match='当前场景没有平台消息'):
            await message_reaction(store, SCENE, MessageReactionArguments(message='-492111658', emoji_id='76'), None)
