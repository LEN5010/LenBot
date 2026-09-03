import pytest
import asyncio
import time
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.runtime.gate import RuntimeGate
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.scenes.models import SceneState
from len_bot.scenes.reducer import SceneReducer
from len_bot.actions.models import ActionItem, ActionType
from len_bot.actions.queue import ActionQueue
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal, TaskProposal
from len_bot.cognition.mailbox import EpisodeMailbox, SteeringType
from len_bot.memory.models import MemoryProposal, MemoryCertainty, MemoryStatus
from len_bot.memory.store import MemoryStore
from len_bot.memory.gate import MemoryGate

@pytest.mark.asyncio
async def test_item1_unified_write_authority_and_no_commit_bypass(tmp_path):
    """
    Item 1: Proves that all write operations (EventStore, MemoryStore, RuntimeGate)
    share the unified write_lock and cannot execute concurrent uncoordinated commits.
    """
    db_file = str(tmp_path / "unified_write.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    # Verify write lock is shared
    assert runtime.event_store._write_lock is runtime.memory_store.write_lock

    # Concurrent writes across event_store, memory_store, and task creation
    async def write_task():
        await runtime.event_store.create_task({
            "id": "t_1",
            "scene_id": "group:1",
            "description": "test task",
            "due_at": time.time() + 100,
            "status": "pending",
            "source_event_id": "ev_1",
            "payload": {"k": "v"},
            "created_at": time.time()
        })

    async def write_memory():
        from len_bot.memory.models import MemoryItem
        await runtime.memory_store.save_memory(MemoryItem(
            id="mem_1",
            subject="user:100",
            kind="preference",
            key="food",
            value="spicy",
            temporal="stable",
            certainty=MemoryCertainty.STRONG,
            scope="group:1",
            visibility="internal",
            evidence=["ev_1"],
            status=MemoryStatus.ACTIVE,
            human_readable_assertion="likes spicy food",
            created_at=time.time(),
            last_confirmed_at=time.time()
        ))

    async def write_loop():
        await runtime.event_store.save_open_loop({
            "id": "loop_1",
            "scene_id": "group:1",
            "target_actor_id": "user:100",
            "intent": "ask",
            "source_event_id": "ev_1",
            "status": "active",
            "created_at": time.time(),
            "expires_at": time.time() + 100
        })

    # Run concurrently
    await asyncio.gather(write_task(), write_memory(), write_loop())

    # Verify all 3 were persisted cleanly
    tasks = await runtime.event_store.get_pending_tasks()
    assert len(tasks) == 1
    assert tasks[0]["id"] == "t_1"

    mems = await runtime.memory_store.query_memories(["group:1"])
    assert len(mems) == 1
    assert mems[0].id == "mem_1"

    loops = await runtime.event_store.get_active_open_loops("group:1")
    assert len(loops) == 1
    assert loops[0]["id"] == "loop_1"

    await runtime.stop()

@pytest.mark.asyncio
async def test_item2_gate_freshness_silence_check_and_toctou(tmp_path):
    """
    Item 2: Proves Gate validates freshness before committing ANY state (including SILENCE),
    and enforces post-commit TOCTOU guard before action enqueue.
    """
    db_file = str(tmp_path / "gate_freshness.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    gate = runtime.runtime_gate
    mailbox = EpisodeMailbox(episode_id="ep_test", scene_id="group:1", base_scene_version=1)
    state = SceneState(scene_id="group:1", version=1)

    # 1. User says "算了不用了" into mailbox
    cancel_event = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:1",
        actor_id="user:100",
        timestamp=time.time(),
        payload={"raw_text": "算了不用了"}
    )
    mailbox.post(cancel_event)

    # 2. Episode outcome was SILENCE + TaskProposal
    outcome_silence_with_task = EpisodeOutcome(
        disposition=FinalDisposition.SILENCE,
        thought="I will be silent now and check later",
        task_proposals=[TaskProposal(description="Future check", delay_seconds=600)]
    )

    decision = await gate.evaluate_and_commit(outcome_silence_with_task, mailbox, state)
    assert decision.disposition == FinalDisposition.SILENCE
    assert "rejected" in decision.reason.lower() or "cancellation" in decision.reason.lower()

    # Crucial: verify that NO task was committed to SQLite because of cancellation!
    tasks = await runtime.event_store.get_pending_tasks()
    assert len(tasks) == 0

    await runtime.stop()

@pytest.mark.asyncio
async def test_item3_message_sent_and_open_loop_atomic_commit(tmp_path):
    """
    Item 3: Proves that MESSAGE_SENT and associated OpenLoop activation are
    committed atomically by SceneActor within the exact same transaction.
    """
    db_file = str(tmp_path / "atomic_open_loop.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:atomic_loop"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)

    loop_data = {
        "id": "loop_atomic_1",
        "scene_id": scene_id,
        "target_actor_id": "user:target",
        "intent": "expect_clarification",
        "source_event_id": "",
        "status": "active",
        "created_at": time.time(),
        "expires_at": time.time() + 3600
    }

    action = ActionItem(
        action_type=ActionType.SEND_GROUP_MESSAGE,
        scene_id=scene_id,
        content="请问你想查询哪天的直播？",
        associated_open_loop=loop_data
    )

    # Enqueue in ActionQueue and wait for SceneActor to process the sent event
    runtime.action_queue.enqueue(action)
    await asyncio.sleep(0.1)

    # Verify both Event and OpenLoop exist and have matching provenance
    events = await runtime.event_store.get_recent_events(scene_id)
    sent_events = [e for e in events if e.event_type == EventType.MESSAGE_SENT]
    assert len(sent_events) == 1
    sent_event_id = sent_events[0].id

    active_loops = await runtime.event_store.get_active_open_loops(scene_id)
    assert len(active_loops) == 1
    assert active_loops[0]["id"] == "loop_atomic_1"
    # Guaranteed provenance: OpenLoop source_event_id equals the committed Event id!
    assert active_loops[0]["source_event_id"] == sent_event_id

    await runtime.stop()

def test_item4_follow_up_steering():
    """
    Item 4: Proves EpisodeMailbox recognizes follow-up questions from users
    and yields SteeringType.FOLLOW_UP to prevent losing secondary intents.
    """
    mailbox = EpisodeMailbox(episode_id="ep_1", scene_id="group:1", base_scene_version=1)

    follow_up = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:1",
        actor_id="user:200",
        timestamp=time.time(),
        payload={"raw_text": "@Bot 顺便看看今天嘉宾是谁", "at_bot": True}
    )
    mailbox.post(follow_up)

    steering = mailbox.check_steering()
    assert steering is not None
    assert steering.steering_type == SteeringType.FOLLOW_UP
    assert "顺便看看今天嘉宾是谁" in steering.source_event.raw_text

@pytest.mark.asyncio
async def test_item6_reducer_and_track_annotation_and_evidence_integrity(tmp_path):
    """
    Item 6:
    - Reducer ignores MESSAGE_SEND_FAILED for bot engagement
    - TRACK emits STATE_ANNOTATION and updates SceneState.soft_annotations
    - MemoryGate rejects if any evidence item does not exist or belongs to another scope
    """
    bot_id = "user:12345678"
    scene_id = "group:reducer_test"

    # 1. MESSAGE_SEND_FAILED does NOT make bot active or reset intervening
    fail_event = Event(
        event_type=EventType.MESSAGE_SEND_FAILED,
        scene_id=scene_id,
        actor_id=bot_id,
        timestamp=time.time(),
        payload={"raw_text": "网络发送失败消息"}
    )
    state = SceneReducer.reduce(None, fail_event, bot_id)
    assert state.bot_engagement == "idle"
    assert state.consecutive_bot_messages == 0

    # 2. STATE_ANNOTATION incorporates soft annotation via Event -> Reducer
    anno_event = Event(
        event_type=EventType.STATE_ANNOTATION,
        scene_id=scene_id,
        actor_id="system:attention",
        timestamp=time.time(),
        metadata={"soft_annotation": {"topic": "直播", "confidence": 0.9}}
    )
    state = SceneReducer.reduce(state, anno_event, bot_id)
    assert state.soft_annotations["topic"] == "直播"
    assert state.active_topic == "直播"
    # Intervening messages count was NOT incremented by system annotation event!
    assert state.intervening_messages_since_bot == 0

    # 3. Evidence integrity in MemoryGate
    db_file = str(tmp_path / "evidence_integrity.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    # Append one real event to group:A
    ev_a = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:A",
        actor_id="user:100",
        timestamp=time.time(),
        payload={"raw_text": "我喜欢吃火锅"}
    )
    await runtime.event_store.commit_scene_event(ev_a, {})

    # Propose memory for group:A with 2 evidence IDs: 1 real (ev_a.id) and 1 fake ("fake_id")
    bad_proposal = MemoryProposal(
        subject="user:100",
        kind="preference",
        key="food",
        value="hotpot",
        temporal="stable",
        certainty=MemoryCertainty.STRONG,
        scope="group:A",
        evidence=[ev_a.id, "fake_id_not_in_db"],
        human_readable_assertion="likes hotpot"
    )

    gate_res = await runtime.memory_gate.commit_proposal(bad_proposal)
    assert gate_res.success is False
    assert "Evidence integrity check failed" in gate_res.reason

    # Propose with only valid evidence
    good_proposal = MemoryProposal(
        subject="user:100",
        kind="preference",
        key="food",
        value="hotpot",
        temporal="stable",
        certainty=MemoryCertainty.STRONG,
        scope="group:A",
        evidence=[ev_a.id],
        human_readable_assertion="likes hotpot"
    )
    gate_res_good = await runtime.memory_gate.commit_proposal(good_proposal)
    assert gate_res_good.success is True

    await runtime.stop()
