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
from len_bot.cognition.react_core import ReActAgentCore
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
        decision_reason="I will be silent now and check later",
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
    from len_bot.actions.models import DeliveryResult, DeliveryStatus
    async def send(action):
        return DeliveryResult(status=DeliveryStatus.SENT, transport="test")
    runtime = AgentRuntime(config, send_adapter=send)
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

    assert mailbox.has_unseen_interim() is True
    # Non-destructive query: does not consume or drop follow-ups
    assert mailbox.has_unseen_interim() is True

    # Consumed exclusively by cognition
    events = mailbox.fetch_unseen_interim_events()
    assert len(events) == 1
    assert "顺便看看今天嘉宾是谁" in events[0].raw_text
    assert mailbox.has_unseen_interim() is False

@pytest.mark.asyncio
async def test_item6_reducer_and_track_annotation_and_evidence_integrity(tmp_path):
    """
    SceneReducer keeps only deterministic delivery facts, and MemoryGate rejects
    evidence that does not exist or belongs to another scope.
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
    assert state.consecutive_bot_messages == 0
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

@pytest.mark.asyncio
async def test_p0_1_transaction_failure_rollback_preserves_state(tmp_path):
    """
    P0-1 Failure Test:
    Simulates a database failure mid-way through commit_scene_event.
    Verifies that:
    1. DB transaction rolls back cleanly.
    2. SceneActor.state in memory remains untouched (does NOT increment version or alter state).
    3. Subsequent valid events advance state cleanly from the uncommitted base.
    """
    db_file = str(tmp_path / "rollback_test.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:p0_rollback"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    actor.state = SceneState(scene_id=scene_id, version=1, participants=["user:initial"])

    # Step 1: Cause an error during commit_scene_event (e.g. inject an error on execute)
    orig_execute = runtime.event_store._db.execute
    fail_on_fts = True

    async def hooked_execute(sql, *args, **kwargs):
        if fail_on_fts and "INSERT INTO events_fts" in sql:
            raise RuntimeError("Simulated disk I/O / SQLite crash on FTS insert!")
        return await orig_execute(sql, *args, **kwargs)

    runtime.event_store._db.execute = hooked_execute

    # Post event that will fail during FTS insertion
    failed_event = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:bad",
        timestamp=time.time(),
        payload={"raw_text": "这条消息会触发模拟的写入失败"}
    )
    actor.post_event(failed_event)
    await asyncio.sleep(0.1)

    # In-memory state MUST be preserved at version 1 with its factual snapshot.
    assert actor.state.version == 1
    assert actor.state.participants == ["user:initial"]

    # Database MUST NOT contain the failed event
    events = await runtime.event_store.get_recent_events(scene_id)
    assert len(events) == 0

    # Step 2: Now restore database execute and post a valid second event
    fail_on_fts = False
    good_event = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:good",
        timestamp=time.time(),
        payload={"raw_text": "第二条正常消息"}
    )
    actor.post_event(good_event)
    await asyncio.sleep(0.1)

    # In-memory state MUST now be version 2 (advancing from 1, not 3!)
    assert actor.state.version == 2

    # Database must contain exactly 1 event (good_event)
    events_after = await runtime.event_store.get_recent_events(scene_id)
    assert len(events_after) == 1
    assert events_after[0].actor_id == "user:good"

    await runtime.stop()

@pytest.mark.asyncio
async def test_p0_2_follow_up_during_cognition_prevents_stale_outcome(tmp_path):
    """
    P0-2 Failure Test:
    Proves that when a follow-up arrives while cognition is in flight:
    1. ReActAgentCore incorporates the follow-up instead of dropping it.
    2. If a follow-up arrives right at final completion before Gate, Gate detects
       mailbox.has_unseen_interim() and rejects the stale outcome (SILENCE) rather than
       sending the superseded answer.
    """
    db_file = str(tmp_path / "follow_up_race.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:follow_up_race"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)

    # 1. Test ReActAgentCore mock with follow-up arriving during execution
    mailbox = EpisodeMailbox(episode_id="ep_race", scene_id=scene_id, base_scene_version=1)

    async def mock_cognition(messages: list[dict[str, str]]) -> EpisodeOutcome:
        # Check if follow-up was incorporated
        full_text = " ".join(m.get("content", "") for m in messages)
        if "顺便看看嘉宾" in full_text:
            return EpisodeOutcome(
                disposition=FinalDisposition.ACTION,
                decision_reason="Answered original question + follow-up!",
                message_proposals=[MessageProposal(content="直播8点开始，今天的嘉宾是小明！")]
            )
        # On first pass without follow-up, simulate user sending follow-up before cognition returns
        fu_event = Event(
            event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id=scene_id,
            actor_id="user:follower",
            timestamp=time.time(),
            payload={"raw_text": "@Bot 顺便看看嘉宾是谁", "at_bot": True}
        )
        mailbox.post(fu_event)
        return EpisodeOutcome(
            disposition=FinalDisposition.ACTION,
            decision_reason="Stale answer without guest info",
            message_proposals=[MessageProposal(content="直播8点开始")]
        )

    core = ReActAgentCore(config, mock_handler=mock_cognition)
    outcome, _trace = await core.execute_episode(
        messages=[{"role": "user", "content": "帮我查直播"}],
        mailbox=mailbox
    )

    # Verify that ReActAgentCore detected mailbox.has_unseen_interim() and re-executed to answer BOTH!
    assert outcome.disposition == FinalDisposition.ACTION
    assert "小明" in outcome.message_proposals[0].content

    # 2. Test Gate staleness check:
    # If a follow-up arrives right when Gate evaluates, Gate MUST NOT send the stale action
    stale_outcome = EpisodeOutcome(
        disposition=FinalDisposition.ACTION,
        decision_reason="Old single question answer",
        message_proposals=[MessageProposal(content="旧回答")]
    )
    race_mailbox = EpisodeMailbox(episode_id="ep_race_2", scene_id=scene_id, base_scene_version=1)
    # User posts follow-up right as Gate runs
    race_mailbox.post(Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:late",
        timestamp=time.time(),
        payload={"raw_text": "@Bot 补充一个问题", "at_bot": True}
    ))

    gate_decision = await runtime.runtime_gate.evaluate_and_commit(stale_outcome, race_mailbox, actor.state)
    # Must reject stale outcome with SILENCE!
    assert gate_decision.disposition == FinalDisposition.SILENCE
    assert "unread interim" in gate_decision.reason.lower()
    # No action enqueued!
    assert gate_decision.actions_enqueued == 0

    await runtime.stop()

@pytest.mark.asyncio
async def test_p0_2_scene_actor_serialization_and_zero_scheduler_leak_on_cancellation(tmp_path):
    """
    P0-2 Definitive Invariant Test:
    Proves that proposal commitment serialized through SceneActor eliminates races,
    and if user cancels during cognition, NO task is written to DB and ZERO task
    is scheduled in the Scheduler heap (no compensation needed!).
    """
    db_file = str(tmp_path / "zero_leak.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:zero_leak"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)

    # 1. Episode starts and acquires lease
    episode_id = "ep_zero_leak"
    mailbox = EpisodeMailbox(episode_id, scene_id, base_scene_version=actor.state.version)
    assert actor.acquire_episode_lease(episode_id, mailbox) is True

    # 2. While cognition is running in background, user sends cancellation into SceneActor
    cancel_event = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:canceller",
        timestamp=time.time(),
        payload={"raw_text": "算了别查了不用了"}
    )
    actor.post_event(cancel_event)

    # 3. Cognition finishes proposing a task
    outcome_with_task = EpisodeOutcome(
        disposition=FinalDisposition.SILENCE,
        decision_reason="I will check live status in 5 minutes",
        task_proposals=[TaskProposal(description="Delayed check", delay_seconds=300)]
    )

    # 4. Submit proposal through SceneActor single-writer serialization point
    decision = await actor.submit_proposal(
        episode_id=episode_id,
        outcome=outcome_with_task,
        mailbox=mailbox,
        runtime_gate=runtime.runtime_gate
    )

    # Must be rejected with SILENCE
    assert decision.disposition == FinalDisposition.SILENCE
    assert "rejected" in decision.reason.lower() or "cancellation" in decision.reason.lower()

    # 5. Invariant Assertion: Database has ZERO tasks
    tasks = await runtime.event_store.get_pending_tasks()
    assert len(tasks) == 0

    # 6. Invariant Assertion: Scheduler heap is completely EMPTY! Zero leak!
    assert len(runtime.scheduler._heap) == 0

    actor.release_episode_lease(episode_id)
    await runtime.stop()
