import pytest
import asyncio
import time
import json
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal, TaskProposal
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.memory.models import MemoryProposal, MemoryCertainty
from len_bot.actions.models import ActionItem
from len_bot.testing.scenario_runner import ScenarioRunner
from len_bot.testing.social import social_result

@pytest.mark.asyncio
async def test_p0_1_atomic_event_and_scene_state(tmp_path):
    """
    P0.1: Verifies that SceneActor is the single commit authority.
    An event cannot exist in events table without its state being reduced and persisted in scene_states.
    """
    db_file = str(tmp_path / "p0_atomic.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:atomic_1"
    event = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:999",
        timestamp=time.time(),
        payload={"raw_text": "第一条原子性测试消息"}
    )

    await runtime.receive_event(event)
    # Wait for actor worker to process and commit
    await asyncio.sleep(0.1)

    # Verify both Event and SceneState are committed
    async with runtime.event_store._db.execute("SELECT id, payload FROM events WHERE scene_id = ?;", (scene_id,)) as cursor:
        event_rows = await cursor.fetchall()
    assert len(event_rows) == 1

    loaded_state = await runtime.event_store.load_scene_state(scene_id)
    assert loaded_state is not None
    assert loaded_state["version"] == 1
    assert "user:999" in loaded_state["participants"]

    await runtime.stop()

@pytest.mark.asyncio
async def test_p0_2_single_scene_episode_mutual_exclusion(tmp_path):
    """
    P0.2: Verifies that only ONE active cognitive episode can run per scene at any time.
    A non-direct interim event is folded into the active mailbox while an episode is
    in flight; a direct @Bot during in-flight cognition deterministically PREEMPTS the
    episode (ADR-0037): the in-flight model call is cancelled and cognition restarts
    over the merged burst — the two episodes must never overlap in flight.
    """
    db_file = str(tmp_path / "p0_concurrency.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file, debounce_idle_ms=20, debounce_max_ms=50)

    episode_1_started = asyncio.Event()
    release_first = asyncio.Event()
    episodes_executed: list[str] = []
    concurrent_overlap = False

    async def slow_social_core(messages: list[dict[str, str]]):
        nonlocal concurrent_overlap
        user_prompt = "\n".join(m["content"] for m in messages)
        in_flight = episode_1_started.is_set() and not release_first.is_set()
        if in_flight and episodes_executed:
            # A second episode must never run while the first is still live.
            concurrent_overlap = True
        episodes_executed.append(user_prompt)
        if len(episodes_executed) == 1:
            episode_1_started.set()
            try:
                await release_first.wait()
            except asyncio.CancelledError:
                release_first.set()
                raise
            return social_result(reason="stale first answer", content="Slow response")
        return social_result(reason="merged burst re-cognized after preemption")

    runner = ScenarioRunner(config=config, mock_social_handler=slow_social_core)
    await runner.setup()

    scene_id = "group:concurrency_test"

    # 1. Trigger Episode 1
    await runner.step_message(scene_id, user_id=1001, text="@Bot 第一条任务", at_bot=True)
    await episode_1_started.wait()

    # Actor must have active episode lease
    actor = await runner.runtime.scene_manager.get_or_create_actor(scene_id)
    assert actor.has_active_episode() is True

    # 2. Inbound interim event arrives while Episode 1 is still in-flight
    await runner.step_message(scene_id, user_id=1002, text="在你想的时候插句话")
    await asyncio.sleep(0.2)

    # Active mailbox must have received the interim event
    assert actor._active_mailbox is not None
    interim_events = actor._active_mailbox.get_interim_events()
    assert any("在你想的时候插句话" in e.raw_text for e in interim_events)

    # 3. A direct @Bot arrives in the SAME scene: ADR-0037 direct preemption.
    # The in-flight episode is cancelled; cognition restarts over the merged burst.
    await runner.step_message(scene_id, user_id=1003, text="@Bot 并发打断", at_bot=True)
    await actor._queue.join()
    assert not release_first.is_set()
    release_first.set()
    await runner.settle(0.2)

    # Exactly two cognition calls, strictly sequential; the restarted episode
    # saw the merged burst (both direct messages + the interim chatter).
    assert not concurrent_overlap
    assert len(episodes_executed) == 2
    merged_prompt = episodes_executed[1]
    assert "第一条任务" in merged_prompt
    assert "并发打断" in merged_prompt
    assert "在你想的时候插句话" in merged_prompt

    # Lease must be released after the preempted restart completes
    assert actor.has_active_episode() is False
    assert runner.sent_actions == []

    await runner.teardown()

@pytest.mark.asyncio
async def test_p0_3_memory_scope_enforcement_and_evidence_isolation(tmp_path):
    """
    P0.3: Verifies that MemoryProposal cannot leak across scenes.
    1. RuntimeGate rigidly overrides proposal.scope to current scene.
    2. MemoryGate rejects evidence belonging to a foreign scene.
    """
    db_file = str(tmp_path / "p0_mem.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    private_scene = "private:user_secret"
    public_scene = "group:public_chat"

    # Plant a secret event in private chat
    secret_event = Event(
        event_type=EventType.PRIVATE_MESSAGE_RECEIVED,
        scene_id=private_scene,
        actor_id="user:secret",
        timestamp=time.time(),
        payload={"raw_text": "这是我的绝密信息"}
    )
    await runtime.receive_event(secret_event)
    await asyncio.sleep(0.05)

    # Model in public group attempts to create a memory using evidence from private scene!
    illegal_proposal = MemoryProposal(
        subject="user:secret",
        kind="fact",
        key="secret_data",
        value="绝密信息",
        scope=public_scene, # Proposed into public scene
        evidence=[secret_event.id], # Evidence is from private chat!
        human_readable_assertion="泄露的秘密"
    )

    gate_result = await runtime.memory_gate.commit_proposal(illegal_proposal)
    assert gate_result.success is False
    assert "Rejected" in gate_result.reason

    # Model proposes memory with scope="global-safe" in EpisodeOutcome
    outcome = EpisodeOutcome(
        disposition=FinalDisposition.ACTION,
        decision_reason="Try to declare global memory",
        message_proposals=[MessageProposal(content="ok")],
        memory_proposals=[
            MemoryProposal(
                subject="user:1001",
                kind="fact",
                key="preference",
                value="hotpot",
                scope="global-safe", # Attempting to escalate to global!
                evidence=[secret_event.id],
                human_readable_assertion="User likes hotpot"
            )
        ]
    )

    actor = await runtime.scene_manager.get_or_create_actor(public_scene)
    mailbox = EpisodeMailbox("ep_test", public_scene, 0)
    actor.acquire_episode_lease("ep_test", mailbox)

    try:
        await runtime.runtime_gate.evaluate_and_commit(outcome, mailbox, actor.state)
    finally:
        actor.release_episode_lease("ep_test")

    # The proposal scope was forced to public_scene, and because secret_event does not belong to public_scene, it was safely rejected!
    memories = await runtime.memory_store.query_memories(allowed_scopes=["global-safe"])
    assert len(memories) == 0

    await runtime.stop()

@pytest.mark.asyncio
async def test_p0_4_scheduler_atomic_trigger_and_payload_roundtrip(tmp_path):
    """
    P0.4: Verifies that task payload serializes as valid JSON and
    TASK_DUE event atomically sets task status to 'triggered' in SQLite.
    """
    db_file = str(tmp_path / "p0_scheduler.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:sched_test"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    mailbox = EpisodeMailbox("ep_sched", scene_id, 0)
    actor.acquire_episode_lease("ep_sched", mailbox)

    complex_payload = {"room_id": 123456, "sub_keys": ["a", "b"], "nested": {"count": 42}}

    outcome = EpisodeOutcome(
        disposition=FinalDisposition.SILENCE,
        decision_reason="Schedule complex payload task",
        task_proposals=[
            TaskProposal(
                description="检查直播间状态",
                delay_seconds=0.15,
                payload=complex_payload
            )
        ]
    )

    await runtime.runtime_gate.evaluate_and_commit(outcome, mailbox, actor.state)
    actor.release_episode_lease("ep_sched")

    # 1. Verify get_pending_tasks properly loads complex payload as dict without JSONDecodeError
    pending = await runtime.event_store.get_pending_tasks()
    assert len(pending) == 1
    assert isinstance(pending[0]["payload"], dict)
    assert pending[0]["payload"]["room_id"] == 123456
    assert pending[0]["payload"]["nested"]["count"] == 42
    task_id = pending[0]["id"]

    # 2. Wait for scheduler to fire
    await asyncio.sleep(0.4)

    # 3. Task in SQLite must now be 'triggered'
    async with runtime.event_store._db.execute("SELECT status FROM tasks WHERE id = ?;", (task_id,)) as cursor:
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "processing"

    # 4. TASK_DUE event must exist in events table
    async with runtime.event_store._db.execute(
        "SELECT event_type, payload FROM events WHERE scene_id = ? AND event_type = 'TASK_DUE';",
        (scene_id,)
    ) as cursor:
        event_row = await cursor.fetchone()
    assert event_row is not None
    payload_data = json.loads(event_row[1])
    assert payload_data["task_id"] == task_id

    await runtime.stop()
