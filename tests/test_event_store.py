import pytest
import os
import time
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore

@pytest.mark.asyncio
async def test_event_store_lifecycle(tmp_path):
    db_file = str(tmp_path / "test.db")
    store = EventStore(db_file)
    await store.initialize()

    # 1. Append Event
    e1 = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:1001",
        actor_id="user:2001",
        timestamp=time.time(),
        payload={"raw_text": "晚上直播有人看吗"}
    )
    await store.append_event(e1)

    # 2. Append Private Event
    e2 = Event(
        event_type=EventType.PRIVATE_MESSAGE_RECEIVED,
        scene_id="private:2002",
        actor_id="user:2002",
        timestamp=time.time(),
        payload={"raw_text": "私聊测试消息直播"}
    )
    await store.append_event(e2)

    # 3. Test FTS5 Trigram CJK Search with ADR-0006 Execution Scope
    # Searching within group:1001 must NOT find private:2002
    results_group = await store.search_messages("直播", allowed_scopes=["group:1001"])
    assert len(results_group) == 1
    assert results_group[0]["actor_id"] == "user:2001"

    # Searching with private scope
    results_private = await store.search_messages("直播", allowed_scopes=["private:2002"])
    assert len(results_private) == 1
    assert results_private[0]["actor_id"] == "user:2002"

    # Facts are persisted together with their source event.
    from len_bot.scenes.models import SceneSession
    e3=Event(event_type=EventType.GROUP_MESSAGE_RECEIVED,scene_id="group:1001",actor_id="user:2001",payload={"raw_text":"新的原话"})
    session=SceneSession(scene_id=e3.scene_id,version=1)
    rowid=await store.commit_scene_event(e3,session.model_dump())
    loaded=await store.load_scene_session(e3.scene_id)
    assert loaded["version"]==1 and loaded["last_observed_event_rowid"]==rowid
    await store.close()
