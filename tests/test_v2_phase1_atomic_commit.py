import pytest
import asyncio
import time
import uuid
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.runtime.gate import RuntimeGate, ProposalCommit, CommittedProposal
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.actions.models import ActionItem, ActionType
from len_bot.actions.queue import ActionQueue
from len_bot.scenes.models import SceneState
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal, TaskProposal
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.memory.models import MemoryProposal, MemoryCertainty, MemoryStatus
from len_bot.memory.store import MemoryStore
from len_bot.scheduler.engine import TaskScheduler

@pytest.mark.asyncio
async def test_atomic_proposal_commit_all_or_nothing_on_evidence_failure(tmp_path):
    """
    V2 Phase 1 Invariant Test:
    Proves that when a proposal contains tasks, open loop resolutions, and a memory proposal
    with INVALID evidence, the entire SQLite transaction rolls back.
    - Zero tasks are created
    - The open loop remains ACTIVE (not resolved)
    - Zero memories are created
    - Zero tasks leaked to scheduler heap
    - Zero actions enqueued
    """
    db_file = str(tmp_path / "atomic_evidence_fail.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:atomic_test"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)

    # 1. Pre-seed a valid historical event
    valid_ev = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:100",
        timestamp=time.time(),
        payload={"raw_text": "今天下雨了"}
    )
    await runtime.event_store.commit_scene_event(valid_ev, actor.state.model_dump())

    # 2. Pre-seed an active OpenLoop
    loop_id = "loop_to_preserve"
    await runtime.event_store.save_open_loop({
        "id": loop_id,
        "scene_id": scene_id,
        "target_actor_id": "user:100",
        "intent": "check_weather",
        "source_event_id": valid_ev.id,
        "status": "active",
        "created_at": time.time(),
        "expires_at": time.time() + 3600
    })

    # Verify initial state
    active_loops_before = await runtime.event_store.get_active_open_loops(scene_id)
    assert len(active_loops_before) == 1
    assert active_loops_before[0]["id"] == loop_id

    # 3. Create Episode Outcome containing:
    # - Valid TaskProposal
    # - OpenLoop resolution
    # - MemoryProposal with ONE valid evidence and ONE fake/non-existent evidence
    # - MessageProposal
    outcome_with_invalid_evidence = EpisodeOutcome(
        disposition=FinalDisposition.ACTION,
        decision_reason="I should answer, schedule task, resolve loop, and save memory",
        message_proposals=[MessageProposal(content="我来回答你")],
        task_proposals=[TaskProposal(description="Should rollback task", delay_seconds=300)],
        resolve_open_loop_ids=[loop_id],
        memory_proposals=[
            MemoryProposal(
                subject="user:100",
                kind="habit",
                key="weather",
                value="rain_watcher",
                evidence=[valid_ev.id, "fake_non_existent_event_id_xyz"],
                human_readable_assertion="watches rain"
            )
        ]
    )

    mailbox = EpisodeMailbox("ep_fail_test", scene_id, base_scene_version=actor.state.version)

    # 4. Submit proposal through SceneActor single-writer serialization
    decision = await actor.submit_proposal(
        episode_id="ep_fail_test",
        outcome=outcome_with_invalid_evidence,
        mailbox=mailbox,
        runtime_gate=runtime.runtime_gate
    )

    # 5. Gate MUST reject with SILENCE due to atomic transaction rollback
    assert decision.disposition == FinalDisposition.SILENCE
    assert "rollback" in decision.reason.lower() or "rejected" in decision.reason.lower()
    assert decision.actions_enqueued == 0

    # 6. INVARIANT ASSERTION: ZERO durable partial side effects
    # Tasks table must have 0 tasks!
    tasks = await runtime.event_store.get_pending_tasks()
    assert len(tasks) == 0

    # Open loop MUST STILL BE ACTIVE! Not resolved!
    active_loops_after = await runtime.event_store.get_active_open_loops(scene_id)
    assert len(active_loops_after) == 1
    assert active_loops_after[0]["id"] == loop_id
    assert active_loops_after[0]["status"] == "active"

    # Memories table must have 0 memories!
    mems = await runtime.memory_store.query_memories([scene_id])
    assert len(mems) == 0

    # Scheduler heap must be completely empty!
    assert len(runtime.scheduler._heap) == 0

    # ActionQueue must have 0 items!
    assert runtime.action_queue._queue.qsize() == 0

    await runtime.stop()

@pytest.mark.asyncio
async def test_atomic_proposal_commit_all_or_nothing_on_database_error(tmp_path):
    """
    V2 Phase 1 Invariant Test:
    Proves that if an unexpected database I/O error occurs midway through durable mutations,
    the transaction is rolled back cleanly.
    """
    db_file = str(tmp_path / "atomic_db_error.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:db_err_test"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)

    loop_id = "loop_err_preserve"
    await runtime.event_store.save_open_loop({
        "id": loop_id,
        "scene_id": scene_id,
        "target_actor_id": "user:200",
        "intent": "waiting",
        "source_event_id": "ev_0",
        "status": "active",
        "created_at": time.time(),
        "expires_at": time.time() + 3600
    })

    # Hook database execution to fail specifically on open_loops update
    orig_execute = runtime.event_store._db.execute
    fail_on_open_loops = True

    async def hooked_execute(sql, *args, **kwargs):
        if fail_on_open_loops and "UPDATE open_loops" in sql:
            raise RuntimeError("Simulated database failure during open_loops update!")
        return await orig_execute(sql, *args, **kwargs)

    runtime.event_store._db.execute = hooked_execute

    outcome = EpisodeOutcome(
        disposition=FinalDisposition.ACTION,
        decision_reason="Attempting commit with simulated DB error",
        message_proposals=[MessageProposal(content="不会被发送的消息")],
        task_proposals=[TaskProposal(description="Task that must rollback", delay_seconds=600)],
        resolve_open_loop_ids=[loop_id]
    )

    mailbox = EpisodeMailbox("ep_db_err", scene_id, base_scene_version=actor.state.version)
    decision = await actor.submit_proposal(
        episode_id="ep_db_err",
        outcome=outcome,
        mailbox=mailbox,
        runtime_gate=runtime.runtime_gate
    )

    assert decision.disposition == FinalDisposition.SILENCE
    assert "rollback" in decision.reason.lower()

    # Restore DB execute
    fail_on_open_loops = False
    runtime.event_store._db.execute = orig_execute

    # INVARIANT: Task must not exist! Open loop must still be active!
    tasks = await runtime.event_store.get_pending_tasks()
    assert len(tasks) == 0

    loops = await runtime.event_store.get_active_open_loops(scene_id)
    assert len(loops) == 1
    assert loops[0]["id"] == loop_id
    assert loops[0]["status"] == "active"

    assert len(runtime.scheduler._heap) == 0
    assert runtime.action_queue._queue.qsize() == 0

    await runtime.stop()

@pytest.mark.asyncio
async def test_atomic_proposal_commit_success_and_external_distribution(tmp_path):
    """
    V2 Phase 1 Invariant Test:
    Proves that upon successful atomic transaction commit:
    - All durable mutations (tasks, open loop resolutions, memory slots) commit in SQLite.
    - External distribution occurs: Scheduler heap is populated, ActionQueue receives action with dependent loop.
    - CommittedProposal is populated and attached to GateDecision.
    """
    db_file = str(tmp_path / "atomic_success.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    sent_items: list[ActionItem] = []
    async def mock_send(item: ActionItem) -> bool:
        sent_items.append(item)
        return True

    runtime = AgentRuntime(config, send_adapter=mock_send)
    await runtime.start()

    scene_id = "group:atomic_success"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)

    # 1. Seed valid event
    ev = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:300",
        timestamp=time.time(),
        payload={"raw_text": "我最喜欢吃川味火锅"}
    )
    await runtime.event_store.commit_scene_event(ev, actor.state.model_dump())

    # 2. Seed active loop to be resolved
    old_loop_id = "loop_to_resolve"
    await runtime.event_store.save_open_loop({
        "id": old_loop_id,
        "scene_id": scene_id,
        "target_actor_id": "user:300",
        "intent": "ask_food",
        "source_event_id": ev.id,
        "status": "active",
        "created_at": time.time(),
        "expires_at": time.time() + 3600
    })

    # 3. Outcome with all 4 mutations
    outcome = EpisodeOutcome(
        disposition=FinalDisposition.ACTION,
        decision_reason="Valid atomic commit with task, memory, open loop resolution, and message",
        message_proposals=[
            MessageProposal(
                content="火锅确实香，今晚几点去？",
                expect_reply=True,
                reply_target="user:300",
                reply_intent="hotpot_time"
            )
        ],
        task_proposals=[
            TaskProposal(description="19:00 提醒准备出门", delay_seconds=3600)
        ],
        resolve_open_loop_ids=[old_loop_id],
        memory_proposals=[
            MemoryProposal(
                subject="user:300",
                kind="preference",
                key="food",
                value="sichuan_hotpot",
                evidence=[ev.id],
                human_readable_assertion="喜欢川味火锅"
            )
        ]
    )

    mailbox = EpisodeMailbox("ep_success", scene_id, base_scene_version=actor.state.version)
    decision = await actor.submit_proposal(
        episode_id="ep_success",
        outcome=outcome,
        mailbox=mailbox,
        runtime_gate=runtime.runtime_gate
    )

    # 4. Assert Decision
    assert decision.disposition == FinalDisposition.ACTION
    assert decision.actions_enqueued == 1
    assert decision.committed_proposal is not None
    assert len(decision.committed_proposal.committed_tasks) == 1
    assert len(decision.committed_proposal.resolved_loop_ids) == 1
    assert len(decision.committed_proposal.committed_memories) == 1

    # 5. Assert SQLite Durable State
    tasks = await runtime.event_store.get_pending_tasks()
    assert len(tasks) == 1
    assert tasks[0]["description"] == "19:00 提醒准备出门"

    # Old loop is resolved!
    active_loops = await runtime.event_store.get_active_open_loops(scene_id)
    assert len(active_loops) == 0  # old loop is resolved

    mems = await runtime.memory_store.query_memories([scene_id])
    assert len(mems) == 1
    assert mems[0].value == "sichuan_hotpot"
    assert mems[0].status == MemoryStatus.ACTIVE

    # 6. Assert External Side-Effect Distribution
    # Task registered in Scheduler heap
    assert len(runtime.scheduler._heap) == 1
    assert runtime.scheduler._heap[0].id == tasks[0]["id"]

    # Wait briefly for ActionQueue to dispatch through send_adapter
    await asyncio.sleep(0.1)
    assert len(sent_items) == 1
    assert sent_items[0].content == "火锅确实香，今晚几点去？"
    assert sent_items[0].associated_open_loop["intent"] == "hotpot_time"

    # And now the new OpenLoop from MESSAGE_SENT has been activated in SQLite!
    loops_after_send = await runtime.event_store.get_active_open_loops(scene_id)
    assert len(loops_after_send) == 1
    assert loops_after_send[0]["intent"] == "hotpot_time"

    await runtime.stop()
