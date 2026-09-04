import json
import time

import pytest

from len_bot.cognition.session import (
    GroupAgentSession,
    GroupAgentSessionReducer,
    RetainedAttentionItem,
    RetainedAttentionProposal,
    SelfSocialStateUpdate,
    SocialCognitionResult,
    SocialDecision,
    SocialDecisionAction,
    SocialPerception,
    SocialWorldState,
)
from len_bot.cognition.social_core import SocialCoreContextAssembler
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
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


def _retained_item(summary: str, expires_at: float | None) -> RetainedAttentionItem:
    return RetainedAttentionItem(
        id=f"retained:{summary}",
        summary=summary,
        source_event_ids=[],
        expires_at=expires_at,
    )


def _silent_cognition_result(
    retained: list[RetainedAttentionProposal] | None = None,
) -> SocialCognitionResult:
    return SocialCognitionResult(
        perception=SocialPerception(summary="understood", world_state=SocialWorldState()),
        self_state=SelfSocialStateUpdate(
            engagement="observing",
            social_position="observer",
            current_interest="low",
            inclination_to_speak="low",
        ),
        decision=SocialDecision(action=SocialDecisionAction.SILENCE, reason="no opening"),
        retained_attention=retained or [],
    )


def test_event_commit_prunes_expired_retained_attention():
    """ADR-0034: the session reducer prunes expired retained attention inside the
    lawful Event -> Runtime State commit path (using the event timestamp)."""
    now = 1_000_000.0
    session = GroupAgentSession(
        scene_id="group:expiry",
        retained_attention=[
            _retained_item("stale", now - 1),
            _retained_item("fresh", now + 60),
            _retained_item("immortal", None),
        ],
    )
    event = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:expiry",
        actor_id="user:A",
        timestamp=now,
        payload={"raw_text": "hi"},
    )

    reduced = GroupAgentSessionReducer.reduce(session, event, "user:42")

    assert [item.summary for item in reduced.retained_attention] == ["fresh", "immortal"]
    # Pure functional transition: the input session is never mutated.
    assert len(session.retained_attention) == 3


def test_cognition_commit_prunes_expired_retained_attention():
    """Expired entries are pruned before new cognition proposals are appended."""
    now = time.time()
    session = GroupAgentSession(
        scene_id="group:expiry",
        retained_attention=[
            _retained_item("stale", now - 1),
            _retained_item("fresh", now + 60),
        ],
    )

    updated = GroupAgentSessionReducer.apply_cognition(
        session,
        _silent_cognition_result(
            retained=[
                RetainedAttentionProposal(summary="new", source_event_ids=[], expires_at=now + 300)
            ]
        ),
        through_event_rowid=7,
    )

    assert [item.summary for item in updated.retained_attention] == ["fresh", "new"]


@pytest.mark.asyncio
async def test_persisted_expired_retained_attention_pruned_on_first_commit(tmp_path):
    """A session restored from DB keeps raw state at load; the first lawful commit
    prunes expired retained attention durably, and downstream context assembly only
    ever sees the live item."""
    now = time.time()
    db_path = str(tmp_path / "retained-restart.db")
    config = RuntimeConfig(bot_qq=42, db_path=db_path, debounce_idle_ms=5_000)
    scene_id = "group:retained"

    first_runtime = AgentRuntime(config)
    await first_runtime.start()
    await commit_event(first_runtime, Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        payload={"raw_text": "昨天说九点播"},
    ))

    # Simulate a session persisted with one expired and one live retained item.
    persisted = await first_runtime.event_store.load_group_agent_session(scene_id)
    persisted["retained_attention"] = [
        _retained_item("EXPIRED_MARKER", now - 1).model_dump(mode="json"),
        _retained_item("FRESH_MARKER", now + 600).model_dump(mode="json"),
    ]
    await first_runtime.event_store._db.execute(
        "UPDATE group_agent_sessions SET state_json = ? WHERE scene_id = ?;",
        (json.dumps(persisted, ensure_ascii=False), scene_id),
    )
    await first_runtime.event_store._db.commit()
    await first_runtime.stop()

    second_runtime = AgentRuntime(config)
    await second_runtime.start()
    actor = await second_runtime.scene_manager.get_or_create_actor(scene_id)
    assert {item.summary for item in actor.group_session.retained_attention} == {
        "EXPIRED_MARKER",
        "FRESH_MARKER",
    }

    await commit_event(second_runtime, Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:B",
        payload={"raw_text": "新消息来了"},
    ))

    assert [item.summary for item in actor.group_session.retained_attention] == ["FRESH_MARKER"]
    persisted_after = await second_runtime.event_store.load_group_agent_session(scene_id)
    assert [item["summary"] for item in persisted_after["retained_attention"]] == ["FRESH_MARKER"]

    context = SocialCoreContextAssembler(second_runtime.config).assemble(
        session=actor.group_session,
        burst=Stimulus(
            scene_id=scene_id,
            stimulus_type=StimulusType.SINGLE_MESSAGE,
            source_event_ids=[],
            actor_id="user:B",
            combined_text="新消息来了",
        ),
        raw_events=[],
        active_open_loops=[],
    )
    situation = context[1]["content"]
    assert "FRESH_MARKER" in situation
    assert "EXPIRED_MARKER" not in situation
    await second_runtime.stop()
