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

    # 4. Test Materialized Scene State (ADR-0001)
    await store.save_scene_state("group:1001", version=5, state_data={"topic": "live"})
    loaded = await store.load_scene_state("group:1001")
    assert loaded is not None
    assert loaded["version"] == 5
    assert loaded["topic"] == "live"

    await store.close()
