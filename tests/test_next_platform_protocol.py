"""OneBot response parsing for the platform query tools (shapes follow NapCat responses)."""

import pytest

import asyncio

from len_bot.next.platform.platform_tools import parse_forward, parse_member, scene_title


def _forward_response():
    return {"status": "ok", "retcode": 0, "data": {"messages": [
        {"self_id": 90001, "user_id": 70001, "time": 1790000000, "message_id": 111, "message_type": "group",
         "sender": {"user_id": 70001, "nickname": "脱敏甲", "card": ""},
         "message": [{"type": "text", "data": {"text": "第一条"}},
                     {"type": "image", "data": {"file": "abc.jpg", "url": "https://example.invalid/a.jpg", "summary": "[图片]"}}]},
        {"self_id": 90001, "user_id": 70002, "time": 1790000060, "message_id": 112, "message_type": "group",
         "sender": {"user_id": 70002, "nickname": "脱敏乙", "card": "乙的名片"},
         "message": [{"type": "forward", "data": {"id": '7400000000000000002'}}]},
    ]}}


def test_forward_nodes_keep_actual_sender_time_and_segments():
    nodes = parse_forward(_forward_response())
    assert [(node.uid, node.name, node.time) for node in nodes] == [
        ('onebot:70001', "脱敏甲", 1790000000.0), ('onebot:70002', "乙的名片", 1790000060.0)]
    assert [segment.type for segment in nodes[0].segments] == ["text", "image"]
    assert nodes[1].segments[0].data == {"id": '7400000000000000002'}


@pytest.mark.parametrize("raw, message", [
    ({"status": "failed", "retcode": 1200, "wording": "消息已过期", "data": None}, "retcode=1200"),
    ({"status": "ok", "retcode": 0, "data": {"message": [{"type": "node", "data": {"user_id": "1", "content": []}}]}},
     "messages"),
    ({"status": "ok", "retcode": 0, "data": {"messages": [{"sender": {"nickname": "x"}, "time": 1, "message": []}]}},
     "user_id"),
    ({"status": "ok", "retcode": 0, "data": {"messages": [
        {"sender": {"user_id": 1}, "time": "1790000000", "message": []}]}}, "Unix timestamp"),
    ({"status": "ok", "retcode": 0, "data": {"messages": [
        {"sender": {"user_id": 1}, "time": 1, "message": "[CQ:face,id=1]"}]}}, "segment array"),
])
def test_forward_rejects_other_shapes_with_raw_fragment(raw, message):
    with pytest.raises(ValueError, match=message) as error:
        parse_forward(raw)
    assert "raw=" in str(error.value)


def _member_response(**changes):
    data = {"group_id": 80001, "user_id": 70001, "nickname": "脱敏甲", "card": "甲的名片", "sex": "unknown",
            "age": 0, "area": "", "level": "12", "qq_level": 0, "join_time": 1700000000,
            "last_sent_time": 1790000000, "title_expire_time": 0, "unfriendly": False,
            "card_changeable": True, "is_robot": False, "shut_up_timestamp": 0, "role": "admin", "title": ""}
    data.update(changes)
    return {"status": "ok", "retcode": 0, "data": data}


def test_member_fields_are_parsed_once_and_identity_must_match():
    assert parse_member(_member_response(), group_id='80001', qq="70001") == {
        "user": "onebot:70001", "nickname": "脱敏甲", "card": "甲的名片", "role": "admin",
        "join_time": 1700000000, "last_sent_time": 1790000000, "title": "", "level": "12"}
    for changes, message in (({"user_id": 70002}, "response is for"), ({"group_id": 80002}, "response is for"),
                             ({"role": "moderator"}, "role must be"), ({"card": None}, "card must be text"),
                             ({"join_time": "1700000000"}, "join_time")):
        with pytest.raises(ValueError, match=message):
            parse_member(_member_response(**changes), group_id='80001', qq="70001")
    with pytest.raises(ValueError, match="retcode=200"):
        parse_member({"status": "failed", "retcode": 200, "wording": "群成员不存在", "data": None},
                     group_id='80001', qq="70001")


def test_scene_title_reads_group_name_and_private_nickname():
    calls = []

    async def call(action, params):
        calls.append((action, params))
        data = {"group_id": 80001, "group_name": "测试群", "member_count": 12, "max_member_count": 500} \
            if action == "get_group_info" else {"user_id": 70001, "nickname": "群友甲", "sex": "unknown", "age": 0}
        return {"status": "ok", "retcode": 0, "data": data, "echo": "1"}

    assert asyncio.run(scene_title("onebot:group:80001", call)) == ("测试群", 12)
    assert asyncio.run(scene_title("onebot:private:70001", call)) == ("群友甲", None)
    assert calls == [("get_group_info", {"group_id": 80001}), ("get_stranger_info", {"user_id": 70001})]


def test_scene_title_failure_keeps_raw_fragment():
    async def call(action, params):
        return {"status": "failed", "retcode": 1200, "wording": "不是群成员", "data": None}

    with pytest.raises(ValueError, match="不是群成员"):
        asyncio.run(scene_title("onebot:group:80001", call))


def test_recorded_bot_ban_and_lift_define_actual_send_availability(tmp_path):
    from pathlib import Path
    import json
    from len_bot.next.platform.onebot_messages import parse_notice
    from len_bot.next.storage.store import Store

    rows = json.loads((Path(__file__).parent / 'fixtures/next/notices/group-ban.json').read_text())
    moment = rows[0]['time'] + 1
    with Store(tmp_path / 'notices.sqlite3', now=lambda: moment) as store:
        store.save_notice(parse_notice(rows[0]))
        assert store.bot_muted_until('onebot:group:80001', 'onebot:90002') == rows[0]['time'] + rows[0]['duration']
        assert store.bot_muted_until('onebot:group:80001', 'onebot:90003') is None
        store.save_notice(parse_notice(rows[1]))
        assert store.bot_muted_until('onebot:group:80001', 'onebot:90002') is None


@pytest.mark.parametrize('duration', [-1, 0.5, True, None])
def test_group_ban_rejects_invalid_duration_at_protocol_entry(duration):
    from len_bot.next.platform.onebot_messages import parse_notice
    raw = {'post_type': 'notice', 'notice_type': 'group_ban', 'sub_type': 'ban', 'time': 1790000000,
           'group_id': 80001, 'user_id': 90002, 'operator_id': 70002, 'duration': duration}
    with pytest.raises(ValueError, match='invalid sub_type/duration') as failure:
        parse_notice(raw)
    assert 'group_ban' in str(failure.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('image_type', [None, 1])
async def test_onebot_outlet_encodes_qualified_scene_and_mention_without_changing_receipt(image_type):
    from dataclasses import replace
    from websockets.asyncio.server import serve
    from len_bot.next.configuration.onebot import OneBotForward
    from len_bot.next.platform.onebot import OneBot
    from len_bot.next.platform.onebot_messages import parse_message
    from len_bot.next.platform.messages import Segment
    import json
    import base64
    from io import BytesIO
    from PIL import Image
    output = BytesIO()
    Image.new('RGB', (1024, 1024), 'red').save(output, format='PNG')
    image_bytes = output.getvalue()
    image_data = {'summary': '[合成表情]', **({} if image_type is None else {'sub_type': image_type})}

    requests = []

    async def peer(websocket):
        while len(requests) < 2:
            request = json.loads(await websocket.recv())
            requests.append(request)
            data = {'user_id': 90001, 'nickname': '脱敏 Bot'} if request['action'] == 'get_login_info' else {'message_id': 12345}
            await websocket.send(json.dumps({'status': 'ok', 'retcode': 0, 'data': data, 'echo': request['echo']}))
        await websocket.wait_closed()

    async with serve(peer, '127.0.0.1', 0) as server:
        port = server.sockets[0].getsockname()[1]
        settings = OneBotForward(mode='forward_ws', ws_url=f'ws://127.0.0.1:{port}')
        async with OneBot(settings, bot_id='onebot:90001', on_event=lambda event: None,
                          on_error=lambda error: None) as bot:
            message = parse_message({'post_type': 'message', 'message_type': 'group', 'self_id': 90001,
                                     'group_id': 80001, 'user_id': 90001, 'message_id': 1, 'time': 1790000000,
                                     'sender': {'nickname': '脱敏 Bot'}, 'message': [
                                         {'type': 'at', 'data': {'qq': '70001'}},
                                         {'type': 'text', 'data': {'text': '合成回复'}},
                                     ]}, own_message_ids=set())
            for invalid in ('onebot:all', 'qq:12345', 'onebot:abc', 'onebot:0', 'onebot:007'):
                rejected = await bot.send_message(replace(message, segments=[Segment('mention', {'user': invalid})]))
                assert rejected.status == 'failed' and 'Invalid OneBot mention' in rejected.error
            message = replace(message, segments=[*message.segments, Segment('image', image_data)])
            result = await bot.send_message(message, image_bytes=image_bytes)
            assert result.status == 'sent' and result.platform_message_id == '12345'
    assert [item['action'] for item in requests] == ['get_login_info', 'send_group_msg']
    assert requests[1]['params']['group_id'] == 80001
    segments = requests[1]['params']['message']
    assert segments[:2] == [
        {'type': 'at', 'data': {'qq': '70001'}}, {'type': 'text', 'data': {'text': '合成回复'}}]
    assert {key: value for key, value in segments[2]['data'].items() if key != 'file'} == image_data
    sent = base64.b64decode(segments[2]['data']['file'].removeprefix('base64://'))
    with Image.open(BytesIO(sent)) as image:
        assert image.size == ((320, 320) if image_type == 1 else (1024, 1024))


@pytest.mark.parametrize('mention', ['onebot:all', 'qq:12345', 'onebot:abc', 'all', 'onebot:0', 'onebot:007'])
def test_model_speech_accepts_only_individual_onebot_accounts(mention):
    from pydantic import ValidationError
    from len_bot.next.chat.tools import SayArguments

    with pytest.raises(ValidationError, match='mention'):
        SayArguments(content='合成回复', mention=mention)
    assert SayArguments(content='合成回复', mention='onebot:70001').mention == 'onebot:70001'
