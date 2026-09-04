import time

import pytest

from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.runtime.agent_runtime import AgentRuntime


async def commit_event(runtime: AgentRuntime, event: Event) -> None:
    await runtime.receive_event(event)
    actor = await runtime.scene_manager.get_or_create_actor(event.scene_id)
    await actor._queue.join()


@pytest.mark.asyncio
async def test_group_agent_session_tracks_factual_working_state(tmp_path):
    runtime = AgentRuntime(
        RuntimeConfig(
            bot_qq=42,
            db_path=str(tmp_path / "session.db"),
            debounce_idle_ms=5_000,
        )
    )
    await runtime.start()
    scene_id = "group:session"
    human = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        payload={
            "raw_text": "今晚播吗",
            "sender": {"card": "阿A", "role": "member"},
        },
    )
    sent = Event(
        event_type=EventType.MESSAGE_SENT,
        scene_id=scene_id,
        actor_id="user:42",
        payload={"raw_text": "不知道"},
    )

    await commit_event(runtime, human)
    await commit_event(runtime, sent)

    session = runtime.scene_manager.get_group_session(scene_id)
    assert session is not None
    assert session.conversation_event_ids == [human.id, sent.id]
    assert session.working_persons["user:A"].display_name == "阿A"
    assert session.self_social_state.last_bot_message_event_id == sent.id
    assert session.self_social_state.consecutive_bot_messages == 1
    assert session.last_observed_event_rowid > 0

    persisted = await runtime.event_store.load_group_agent_session(scene_id)
    assert persisted == session.model_dump(mode="json")
    await runtime.stop()


@pytest.mark.asyncio
async def test_group_agent_session_recovers_after_restart(tmp_path):
    db_path = str(tmp_path / "restart.db")
    config = RuntimeConfig(bot_qq=42, db_path=db_path, debounce_idle_ms=5_000)
    scene_id = "group:restart"
    event = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        payload={"raw_text": "昨天说九点播"},
    )

    first_runtime = AgentRuntime(config)
    await first_runtime.start()
    await commit_event(first_runtime, event)
    first_session = first_runtime.scene_manager.get_group_session(scene_id)
    await first_runtime.stop()

    second_runtime = AgentRuntime(config)
    await second_runtime.start()
    await second_runtime.scene_manager.get_or_create_actor(scene_id)
    restored = second_runtime.scene_manager.get_group_session(scene_id)

    assert restored == first_session
    assert restored.conversation_event_ids == [event.id]
    await second_runtime.stop()


@pytest.mark.asyncio
async def test_group_agent_sessions_are_isolated_by_scene(tmp_path):
    runtime = AgentRuntime(
        RuntimeConfig(
            bot_qq=42,
            db_path=str(tmp_path / "isolation.db"),
            debounce_idle_ms=5_000,
        )
    )
    await runtime.start()
    first = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:one",
        actor_id="user:A",
        payload={"raw_text": "one"},
    )
    second = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:two",
        actor_id="user:B",
        payload={"raw_text": "two"},
    )

    await commit_event(runtime, first)
    await commit_event(runtime, second)

    assert runtime.scene_manager.get_group_session("group:one").conversation_event_ids == [first.id]
    assert runtime.scene_manager.get_group_session("group:two").conversation_event_ids == [second.id]
    await runtime.stop()


@pytest.mark.asyncio
async def test_failed_event_commit_does_not_publish_candidate_session(tmp_path):
    runtime = AgentRuntime(
        RuntimeConfig(
            bot_qq=42,
            db_path=str(tmp_path / "rollback.db"),
            debounce_idle_ms=5_000,
        )
    )
    await runtime.start()
    scene_id = "group:rollback"
    first = Event(
        id="duplicate-event",
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        payload={"raw_text": "first"},
    )
    await commit_event(runtime, first)
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    published = actor.group_session.model_copy(deep=True)

    duplicate = Event(
        id=first.id,
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:B",
        timestamp=time.time(),
        payload={"raw_text": "must roll back"},
    )
    await commit_event(runtime, duplicate)

    assert actor.group_session == published
    persisted = await runtime.event_store.load_group_agent_session(scene_id)
    assert persisted == published.model_dump(mode="json")
    events = await runtime.event_store.get_recent_events(scene_id)
    assert [event.id for event in events] == [first.id]
    await runtime.stop()


@pytest.mark.asyncio
async def test_recent_context_uses_scene_commit_order(tmp_path):
    runtime = AgentRuntime(
        RuntimeConfig(
            bot_qq=42,
            db_path=str(tmp_path / "commit-order.db"),
            debounce_idle_ms=5_000,
        )
    )
    await runtime.start()
    scene_id = "group:commit-order"
    first_committed = Event(
        id="committed-first",
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        timestamp=200.0,
        payload={"raw_text": "first"},
    )
    second_committed = Event(
        id="committed-second",
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:B",
        timestamp=100.0,
        payload={"raw_text": "second"},
    )
    await commit_event(runtime, first_committed)
    await commit_event(runtime, second_committed)

    recent = await runtime.event_store.get_recent_events(scene_id)
    assert [event.id for event in recent] == ["committed-first", "committed-second"]
    await runtime.stop()
