import pytest
import asyncio
import time
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.scenes.models import SceneState
from len_bot.scenes.reducer import SceneReducer
from len_bot.attention.engine import AttentionEngine
from len_bot.attention.models import AttentionDisposition
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition
from len_bot.cognition.pi_core import PiAgentCore

def test_p1_scene_engagement_lifecycle_decay():
    """
    P1: Verifies that bot_engagement does not stay 'active' forever.
    It decays naturally as other participants speak without addressing the bot.
    """
    bot_id = "user:12345678"
    scene_id = "group:decay_test"
    t0 = time.time()

    # 1. Bot speaks -> engagement becomes active
    bot_event = Event(
        event_type=EventType.MESSAGE_SENT,
        scene_id=scene_id,
        actor_id=bot_id,
        timestamp=t0,
        payload={"raw_text": "我来回答"}
    )
    state = SceneReducer.reduce(None, bot_event, bot_id)
    assert state.bot_engagement == "active"
    assert state.intervening_messages_since_bot == 0

    # 2. 1 to 4 intervening messages from others -> remains active
    for i in range(1, 5):
        human_event = Event(
            event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id=scene_id,
            actor_id="user:999",
            timestamp=t0 + i * 10,
            payload={"raw_text": f"闲聊消息 {i}"}
        )
        state = SceneReducer.reduce(state, human_event, bot_id)
        assert state.intervening_messages_since_bot == i

    assert state.bot_engagement == "active"

    # 3. 5th intervening message -> decays to 'observing'
    human_5 = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:999",
        timestamp=t0 + 50,
        payload={"raw_text": "第5条消息，转场了"}
    )
    state = SceneReducer.reduce(state, human_5, bot_id)
    assert state.intervening_messages_since_bot == 5
    assert state.bot_engagement == "observing"

    # 4. AttentionEngine no longer triggers active_conversation_engagement for 'observing'
    config = RuntimeConfig(bot_qq=12345678)
    engine = AttentionEngine(config)
    st = Stimulus(
        scene_id=scene_id,
        stimulus_type=StimulusType.SINGLE_MESSAGE,
        source_event_ids=["e6"],
        actor_id="user:999",
        combined_text="无关的路人对话"
    )
    att_res = engine.evaluate(st, state, [])
    assert att_res.disposition != AttentionDisposition.WAKE

    # 5. 15th intervening message -> decays to 'idle'
    for i in range(6, 16):
        e = Event(
            event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id=scene_id,
            actor_id="user:999",
            timestamp=t0 + i * 10,
            payload={"raw_text": f"刷屏 {i}"}
        )
        state = SceneReducer.reduce(state, e, bot_id)

    assert state.intervening_messages_since_bot == 15
    assert state.bot_engagement == "idle"

def test_p1_pi_core_outcome_parser():
    """
    P1: Verifies single-pass structured outcome parsing without beta parse or double inference.
    Handles standard JSON, markdown fenced JSON, and plain text gracefully.
    """
    config = RuntimeConfig()
    core = PiAgentCore(config)

    # 1. Markdown fenced JSON
    fenced_json = """```json
    {
        "disposition": "ACTION",
        "thought": "Direct response",
        "message_proposals": [{"content": "测试回复"}]
    }
    ```"""
    outcome1 = core._parse_outcome(fenced_json)
    assert outcome1.disposition == FinalDisposition.ACTION
    assert len(outcome1.message_proposals) == 1
    assert outcome1.message_proposals[0].content == "测试回复"

    # 2. Plain JSON
    plain_json = '{"disposition": "SILENCE", "thought": "No need to talk"}'
    outcome2 = core._parse_outcome(plain_json)
    assert outcome2.disposition == FinalDisposition.SILENCE

    # 3. Plain text / malformed string (Item 5: Zero fail-open, strict contract enforcement)
    plain_text = "直接输出的纯文本消息"
    outcome3 = core._parse_outcome(plain_text)
    assert outcome3.disposition == FinalDisposition.SILENCE
    assert len(outcome3.message_proposals) == 0

@pytest.mark.asyncio
async def test_p1_open_loop_ttl_sweep(tmp_path):
    """
    P1: Verifies background heartbeat sweeps expired open loops.
    """
    db_file = str(tmp_path / "p1_openloop.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:loop_test"
    now = time.time()

    # Save an already expired open loop (expired 10 seconds ago)
    await runtime.event_store.save_open_loop({
        "id": "loop_expired_1",
        "scene_id": scene_id,
        "target_actor_id": "user:123",
        "intent": "question",
        "source_event_id": "ev_1",
        "status": "active",
        "created_at": now - 100,
        "expires_at": now - 10
    })

    # Save an active non-expired open loop
    await runtime.event_store.save_open_loop({
        "id": "loop_active_1",
        "scene_id": scene_id,
        "target_actor_id": "user:456",
        "intent": "question",
        "source_event_id": "ev_2",
        "status": "active",
        "created_at": now,
        "expires_at": now + 3600
    })

    # Sweep TTL expiration
    expired_ids = await runtime.open_loop_manager.sweep_ttl_expiration()
    assert "loop_expired_1" in expired_ids
    assert "loop_active_1" not in expired_ids

    # Active loops for scene should now only have loop_active_1
    active = await runtime.event_store.get_active_open_loops(scene_id)
    assert len(active) == 1
    assert active[0]["id"] == "loop_active_1"

    await runtime.stop()
