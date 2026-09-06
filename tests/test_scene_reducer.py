import time
from len_bot.events.models import Event, EventType
from len_bot.scenes.reducer import SceneReducer

def test_scene_reducer_single_writer_flow():
    bot_id = "user:9999"
    now = time.time()
    
    # 1. First event from User A
    e1 = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:100",
        actor_id="user:1",
        timestamp=now,
        payload={"raw_text": "晚上直播有人看吗"}
    )
    s1 = SceneReducer.reduce(None, e1, bot_id)
    assert s1.version == 1
    assert "user:1" in s1.participants
    assert s1.consecutive_bot_messages == 0

    # 2. Event from User B
    e2 = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:100",
        actor_id="user:2",
        timestamp=now + 1,
        payload={"raw_text": "看啊"}
    )
    s2 = SceneReducer.reduce(s1, e2, bot_id)
    assert s2.version == 2
    assert "user:2" in s2.participants

    # 3. Message sent by Bot
    e3 = Event(
        event_type=EventType.MESSAGE_SENT,
        scene_id="group:100",
        actor_id=bot_id,
        timestamp=now + 2,
        payload={"content": "我也看"}
    )
    s3 = SceneReducer.reduce(s2, e3, bot_id)
    assert s3.version == 3
    assert s3.consecutive_bot_messages == 1
    assert s3.last_bot_message_at == now + 2
    assert s3.human_messages_since_bot == 0
