"""OneBot response parsing for the platform query tools (shapes follow NapCat responses)."""

import pytest

from len_bot.next.platform.platform_tools import parse_forward, parse_member


def _forward_response():
    return {"status": "ok", "retcode": 0, "data": {"messages": [
        {"self_id": 90001, "user_id": 70001, "time": 1790000000, "message_id": 111, "message_type": "group",
         "sender": {"user_id": 70001, "nickname": "脱敏甲", "card": ""},
         "message": [{"type": "text", "data": {"text": "第一条"}},
                     {"type": "image", "data": {"file": "abc.jpg", "url": "https://example.invalid/a.jpg", "summary": "[图片]"}}]},
        {"self_id": 90001, "user_id": 70002, "time": 1790000060, "message_id": 112, "message_type": "group",
         "sender": {"user_id": 70002, "nickname": "脱敏乙", "card": "乙的名片"},
         "message": [{"type": "forward", "data": {"id": "7400000000000000002"}}]},
    ]}}


def test_forward_nodes_keep_actual_sender_time_and_segments():
    nodes = parse_forward(_forward_response())
    assert [(node.uid, node.name, node.time) for node in nodes] == [
        ("70001", "脱敏甲", 1790000000.0), ("70002", "乙的名片", 1790000060.0)]
    assert [segment.type for segment in nodes[0].segments] == ["text", "image"]
    assert nodes[1].segments[0].data == {"id": "7400000000000000002"}


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
    assert parse_member(_member_response(), group_id="80001", qq="70001") == {
        "qq": "70001", "nickname": "脱敏甲", "card": "甲的名片", "role": "admin",
        "join_time": 1700000000, "last_sent_time": 1790000000, "title": "", "level": "12"}
    for changes, message in (({"user_id": 70002}, "response is for"), ({"group_id": 80002}, "response is for"),
                             ({"role": "moderator"}, "role must be"), ({"card": None}, "card must be text"),
                             ({"join_time": "1700000000"}, "join_time")):
        with pytest.raises(ValueError, match=message):
            parse_member(_member_response(**changes), group_id="80001", qq="70001")
    with pytest.raises(ValueError, match="retcode=200"):
        parse_member({"status": "failed", "retcode": 200, "wording": "群成员不存在", "data": None},
                     group_id="80001", qq="70001")


def test_recorded_bot_ban_and_lift_define_actual_send_availability(tmp_path):
    from pathlib import Path
    import json
    from len_bot.next.platform.messages import parse_notice
    from len_bot.next.storage.store import Store

    rows = json.loads((Path(__file__).parent / 'fixtures/next/notices/group-ban.json').read_text())
    moment = rows[0]['time'] + 1
    with Store(tmp_path / 'notices.sqlite3', now=lambda: moment) as store:
        store.save_notice(parse_notice(rows[0]))
        assert store.bot_muted_until('group:80001', '90002') == rows[0]['time'] + rows[0]['duration']
        assert store.bot_muted_until('group:80001', '90003') is None
        store.save_notice(parse_notice(rows[1]))
        assert store.bot_muted_until('group:80001', '90002') is None


@pytest.mark.parametrize('duration', [-1, 0.5, True, None])
def test_group_ban_rejects_invalid_duration_at_protocol_entry(duration):
    from len_bot.next.platform.messages import parse_notice
    raw = {'post_type': 'notice', 'notice_type': 'group_ban', 'sub_type': 'ban', 'time': 1790000000,
           'group_id': 80001, 'user_id': 90002, 'operator_id': 70002, 'duration': duration}
    with pytest.raises(ValueError, match='invalid sub_type/duration') as failure:
        parse_notice(raw)
    assert 'group_ban' in str(failure.value)
