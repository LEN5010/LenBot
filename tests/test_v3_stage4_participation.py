import pytest
import asyncio
import time
from len_bot.scenes.models import SceneState, ParticipationThread, ThreadStatus
from len_bot.scenes.reducer import SceneReducer, THREAD_FADE_AFTER, THREAD_CLOSE_AFTER
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.attention.engine import AttentionEngine
from len_bot.attention.models import AttentionDisposition
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.web.query_service import RuntimeQueryService

def test_participation_lifecycle_close_before_fading():
    """ADR-0027, §9.1: Ensure CLOSE check precedes FADING and last_relevant_at updates only on truly relevant events."""
    base_time = 1000.0
    state = SceneState(
        scene_id="group:lifecycle",
        current_thread=ParticipationThread(
            thread_id="th_lifecycle",
            scene_id="group:lifecycle",
            topic="anime discussion",
            status=ThreadStatus.ACTIVE,
            participants=["user:101", "user:102"],
            last_relevant_at=base_time,
            intervening_messages=0
        )
    )

    # 1. Truly relevant event after 350s -> must transition to CLOSED
    ev_late = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:lifecycle",
        actor_id="user:101",
        timestamp=base_time + 350.0,
        payload={"raw_text": "anime is great"} # topic match
    )
    s1 = SceneReducer.reduce(state, ev_late, "user:bot")
    assert s1.current_thread.status == ThreadStatus.CLOSED

    # 2. Truly relevant event after 150s -> must transition to FADING
    ev_mid = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:lifecycle",
        actor_id="user:101",
        timestamp=base_time + 150.0,
        payload={"raw_text": "anime episode 2"} # topic match
    )
    s2 = SceneReducer.reduce(state, ev_mid, "user:bot")
    assert s2.current_thread.status == ThreadStatus.FADING
    assert s2.current_thread.last_relevant_at == base_time + 150.0

    # 3. Participant-only off-topic event: does NOT update last_relevant_at
    ev_off_topic = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:lifecycle",
        actor_id="user:102",
        timestamp=base_time + 50.0,
        payload={"raw_text": "中午吃什么呢今天"} # off-topic
    )
    s3 = SceneReducer.reduce(state, ev_off_topic, "user:bot")
    assert s3.current_thread.intervening_messages == 1
    # last_relevant_at remains unchanged!
    assert s3.current_thread.last_relevant_at == base_time


def test_attention_participant_alone_yields_observe():
    """ADR-0027, §9.2: Participant membership alone without topic match or adjacency yields OBSERVE (participant_off_topic)."""
    config = RuntimeConfig(bot_qq=12345678)
    engine = AttentionEngine(config=config)
    now = 1000.0
    state = SceneState(
        scene_id="group:att",
        recent_bot_message_at=now - 70.0, # not immediate adjacency (> 60s)
        current_thread=ParticipationThread(
            thread_id="th_att",
            scene_id="group:att",
            topic="database design",
            status=ThreadStatus.ACTIVE,
            participants=["user:alice"],
            last_relevant_at=now - 10.0,
            intervening_messages=2 # not immediate adjacency
        )
    )

    stimulus = Stimulus(
        stimulus_type=StimulusType.SINGLE_MESSAGE,
        scene_id="group:att",
        actor_id="user:alice", # Alice is a participant!
        source_event_ids=["ev_1"],
        combined_text="明天天气怎么样",
        events=[Event(
            id="ev_1",
            event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id="group:att",
            actor_id="user:alice",
            timestamp=now,
            payload={"raw_text": "明天天气怎么样"} # No topic match!
        )],
        timestamp=now
    )

    result = engine.evaluate(stimulus, state, [], now=now)
    assert result.disposition == AttentionDisposition.OBSERVE
    assert result.reason == "participant_off_topic"


@pytest.mark.asyncio
async def test_scene_detail_read_only_and_no_attribute_error(tmp_path):
    """ADR-0027, §22: scene_detail reads purely from event_store and has no bot_consecutive_messages crash."""
    db_file = str(tmp_path / "scene_detail.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    # Save a scene state with current_thread in DB
    scene_id = "group:read_only_scene"
    state = SceneState(
        scene_id=scene_id,
        version=5,
        activity_level="normal",
        consecutive_bot_messages=2,
        current_thread=ParticipationThread(
            thread_id="th_ro",
            scene_id=scene_id,
            topic="test topic",
            status=ThreadStatus.ACTIVE,
            participants=["user:1"],
            intervening_messages=1
        )
    )
    await runtime.event_store.save_scene_state(scene_id, 5, state.model_dump())

    qs = RuntimeQueryService(runtime)
    # Actor registry should NOT have this scene loaded in memory yet
    assert scene_id not in runtime.scene_manager._actors

    detail = await qs.scene_detail(scene_id)
    assert detail is not None
    assert detail["scene_id"] == scene_id
    assert detail["version"] == 5
    assert detail["consecutive_bot_messages"] == 2
    assert detail["current_thread"]["topic"] == "test topic"

    # Must still NOT have created an actor in memory!
    assert scene_id not in runtime.scene_manager._actors

    await runtime.stop()


@pytest.mark.asyncio
async def test_maintenance_loop_thread_decay(tmp_path):
    """ADR-0027, §9: maintenance loop scans loaded scene actors and decays active threads past thresholds."""
    db_file = str(tmp_path / "maintenance_decay.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file, maintenance_interval_seconds=0.05)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:decay_scene"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    actor.state.current_thread = ParticipationThread(
        thread_id="th_decay",
        scene_id=scene_id,
        topic="old discussion",
        status=ThreadStatus.ACTIVE,
        participants=["user:1"],
        last_relevant_at=time.time() - 150.0 # > 120s -> FADING
    )

    # Let maintenance loop run one iteration
    await asyncio.sleep(0.1)
    assert actor.state.current_thread.status == ThreadStatus.FADING

    # Set last_relevant_at to 350s ago -> CLOSED
    actor.state.current_thread.last_relevant_at = time.time() - 350.0
    await asyncio.sleep(0.1)
    assert actor.state.current_thread.status == ThreadStatus.CLOSED

    await runtime.stop()
