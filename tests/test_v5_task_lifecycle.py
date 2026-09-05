"""ADR-0040 task commits, delivery ambiguity and durable recovery."""
import asyncio
import pytest
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.cognition.models import TaskProposal, EpisodeOutcome, FinalDisposition, MessageProposal
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.events.models import Event, EventType
from len_bot.testing.social import social_result
from len_bot.testing.replay import drain

async def proposal(rt, tasks=(), messages=(), scene="group:1"):
    actor = await rt.scene_manager.get_or_create_actor(scene)
    box = EpisodeMailbox("test", scene, actor.state.version)
    return await actor.submit_proposal("test", EpisodeOutcome(
        disposition=FinalDisposition.ACTION if messages else FinalDisposition.SILENCE,
        decision_reason="test", task_proposals=list(tasks), message_proposals=list(messages)),
        box, rt.runtime_gate)

async def pending(rt):
    decision = await proposal(rt, [TaskProposal(description="叫醒A", due_at=rt.clock()+600, proposal_id="wake")])
    assert decision.accepted
    return decision.committed_proposal.committed_tasks[0]

@pytest.mark.asyncio
async def test_missing_task_reference_blocks_acknowledgement(tmp_path):
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path/"task.db")))
    await rt.start()
    try:
        decision = await proposal(rt, messages=[MessageProposal(content="好，到点叫你", task_ref="missing")])
        assert not decision.accepted
        assert rt.action_queue._queue.empty()
        assert await rt.event_store.get_pending_tasks() == []
    finally:
        await rt.stop()

@pytest.mark.asyncio
async def test_update_cancel_scoped_and_no_partial_commit(tmp_path):
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path/"task.db")))
    await rt.start()
    try:
        task = await pending(rt)
        wrong = await proposal(rt, [TaskProposal(operation="cancel", task_id=task.id)], scene="group:2")
        assert not wrong.accepted
        changed = rt.clock()+3600
        assert (await proposal(rt, [TaskProposal(operation="update", task_id=task.id, due_at=changed)])).accepted
        assert (await rt.event_store.get_pending_tasks())[0]["due_at"] == changed
        bad = await proposal(rt, [TaskProposal(operation="cancel", task_id=task.id),
                                 TaskProposal(operation="cancel", task_id="missing")])
        assert not bad.accepted
        assert (await rt.event_store.get_pending_tasks())[0]["status"] == "pending"
        assert (await proposal(rt, [TaskProposal(operation="cancel", task_id=task.id)])).accepted
        assert await rt.event_store.get_pending_tasks() == []
        assert not await rt.scheduler.trigger_task_now(task.id)
    finally:
        await rt.stop()

@pytest.mark.asyncio
@pytest.mark.parametrize("delivery,status", [("ok","completed"),("reject","failed"),("unknown","delivery_unknown"),("shadow","shadow_observed")])
async def test_fulfilment_waits_for_actual_delivery(tmp_path, delivery, status):
    entered, release = asyncio.Event(), asyncio.Event()
    async def send(action):
        entered.set()
        await release.wait()
        if delivery == "unknown":
            raise TimeoutError("connection lost after write")
        return delivery == "ok"
    async def core(messages):
        return social_result(reason="履约", content="该起床了", fulfils_task_id=task.id)
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path/"task.db")), send_adapter=send, mock_social_handler=core)
    await rt.start()
    await rt.scheduler.stop()
    try:
        task = await pending(rt)
        if delivery == "shadow":
            await rt.set_shadow_mode(True)
        await rt.scheduler.trigger_task_now(task.id)
        if delivery != "shadow":
            await entered.wait()
            assert (await rt.event_store.scene_tasks("group:1"))[0]["status"] == "awaiting_delivery"
        release.set()
        await drain(rt)
        row = (await rt.event_store.scene_tasks("group:1"))[0]
        assert row["status"] == status
        await rt.event_store.recover_task_execution()
        assert (await rt.event_store.scene_tasks("group:1"))[0]["status"] == status
        assert not await rt.scheduler.trigger_task_now(task.id)
    finally:
        release.set()
        await rt.stop()

@pytest.mark.asyncio
async def test_claim_and_due_event_survive_crash(tmp_path):
    config = RuntimeConfig(db_path=str(tmp_path/"restart.db"))
    rt = AgentRuntime(config, mock_social_handler=lambda messages: asyncio.sleep(0, result=social_result(reason="核对")))
    await rt.start()
    await rt.scheduler.stop()
    task = await pending(rt)
    due = Event(event_type=EventType.TASK_DUE, scene_id="group:1", actor_id="system:scheduler", payload={"task_id":task.id})
    assert await rt.event_store.claim_task_event(task.id, task.scene_id, due)
    assert not await rt.event_store.claim_task_event(task.id, task.scene_id, due)
    await rt.stop()
    restarted = AgentRuntime(config, mock_social_handler=lambda messages: asyncio.sleep(0, result=social_result(reason="核对")))
    await restarted.start()
    try:
        await drain(restarted)
        events = await restarted.event_store.get_recent_events("group:1")
        assert len([e for e in events if e.id == due.id]) == 1
        assert await restarted.event_store.pending_runtime_events() == []
        assert (await restarted.event_store.scene_tasks("group:1"))[0]["status"] == "processing"
    finally:
        await restarted.stop()

@pytest.mark.asyncio
async def test_incomplete_delivery_never_auto_resends_after_restart(tmp_path):
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path/"uncertain.db")))
    await rt.start()
    await rt.scheduler.stop()
    try:
        task = await pending(rt)
        due = Event(event_type=EventType.TASK_DUE, scene_id=task.scene_id, actor_id="system", payload={"task_id":task.id})
        await rt.event_store.claim_task_event(task.id, task.scene_id, due)
        await rt.event_store.commit_scene_event(due, {}, task_id_to_trigger=task.id)
        await rt.event_store.commit_proposal_transaction("test",task.scene_id,[],[],[],deliveries={task.id:"attempt-1"})
        await rt.event_store.recover_task_execution()
        assert (await rt.event_store.scene_tasks(task.scene_id))[0]["status"] == "delivery_unknown"
        assert await rt.event_store.get_pending_tasks() == []
        assert await rt.event_store.pending_runtime_events() == []
    finally:
        await rt.stop()
