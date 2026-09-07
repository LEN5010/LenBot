"""ADR-0040 task commits, delivery ambiguity and durable recovery."""
from delivery_support import allow_fake_delivery
import asyncio
import pytest
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.cognition.models import TaskProposal, EpisodeOutcome, FinalDisposition, MessageProposal
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.events.models import Event, EventType
from len_bot.testing.turns import turn_result
from len_bot.testing.replay import drain

async def proposal(rt, tasks=(), messages=(), scene="group:1"):
    return await rt.operator_outcome(scene,EpisodeOutcome(
        disposition=FinalDisposition.ACTION if messages else FinalDisposition.SILENCE,
        decision_reason="test",task_proposals=list(tasks),message_proposals=list(messages)))

async def pending(rt):
    decision = await proposal(rt, [TaskProposal(description="叫醒A", due_at=rt.clock()+600, proposal_id="wake")])
    assert decision.accepted
    return decision.committed_proposal.committed_tasks[0]

@pytest.mark.asyncio
async def test_missing_task_reference_blocks_acknowledgement(tmp_path):
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path/"task.db")))
    await rt.start()
    await allow_fake_delivery(rt, 'group:1')
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
    await allow_fake_delivery(rt, 'group:1', 'group:2')
    try:
        task = await pending(rt)
        wrong = await proposal(rt, [TaskProposal(operation="cancel", task_id=task.id)], scene="group:2")
        assert not wrong.accepted
        changed = rt.clock()+3600
        await rt.set_shadow_mode(True)
        assert (await proposal(rt, [TaskProposal(operation="update", task_id=task.id, due_at=changed)])).accepted
        assert (await rt.event_store.get_pending_tasks())[0]["due_at"] == changed
        await rt.set_shadow_mode(False)
        assert (await rt.event_store.scene_tasks("group:1"))[0]["origin_mode"] == "shadow"
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
        from len_bot.actions.models import DeliveryResult, DeliveryStatus
        return DeliveryResult(status=DeliveryStatus.SENT if delivery == "ok" else DeliveryStatus.REJECTED,
                              transport="test")
    async def core(session, events):
        return turn_result(reason="履约", content="该起床了", fulfils_task_id=task.id)
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path/"task.db")), send_adapter=send, mock_turn_handler=core)
    await rt.start()
    await allow_fake_delivery(rt, 'group:1')
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
@pytest.mark.parametrize("trigger_event_id", ["", "condition:source"])
async def test_claim_and_due_event_survive_crash(tmp_path, trigger_event_id):
    config = RuntimeConfig(db_path=str(tmp_path/"restart.db"))
    rt = AgentRuntime(config, mock_turn_handler=lambda session, events: asyncio.sleep(0, result=turn_result(reason="核对")))
    await rt.start()
    await allow_fake_delivery(rt, 'group:1')
    await rt.scheduler.stop()
    task = await pending(rt)
    obsolete = Event(event_type=EventType.TASK_DUE, scene_id=task.scene_id, actor_id="system:scheduler",
                     payload={"task_id":task.id, "trigger_event_id":"old:claim"})
    assert await rt.event_store.claim_task_event(task.id, task.scene_id, obsolete)
    assert (await proposal(rt, [TaskProposal(operation="update", task_id=task.id, due_at=rt.clock()+600)])).accepted
    due = Event(event_type=EventType.TASK_DUE, scene_id="group:1", actor_id="system:scheduler",
                payload={"task_id":task.id, "trigger_event_id":trigger_event_id})
    assert await rt.event_store.claim_task_event(task.id, task.scene_id, due)
    assert not await rt.event_store.claim_task_event(task.id, task.scene_id, due)
    await rt.event_store.recover_task_execution()
    await rt.event_store.recover_task_execution()
    assert (await rt.event_store.scene_tasks(task.scene_id))[0]["status"] == "claimed"
    assert await rt.event_store.task_due_is_current(due)
    assert not await rt.event_store.task_due_is_current(obsolete)
    assert [event.id for event in await rt.event_store.pending_runtime_events()] == [obsolete.id, due.id]
    await rt.stop()
    turns = []
    async def core(session, events):
        turns.append([event.id for event in events])
        return turn_result(reason="核对")
    restarted = AgentRuntime(config, mock_turn_handler=core)
    await restarted.start()
    try:
        await drain(restarted)
        await restarted.receive_event(due)
        await drain(restarted)
        events = await restarted.event_store.get_recent_events("group:1")
        assert len([e for e in events if e.id == due.id]) == 1
        assert next(e for e in events if e.id == obsolete.id).metadata["obsolete_task_wake"] is True
        assert next(e for e in events if e.id == due.id).metadata["obsolete_task_wake"] is False
        assert not any(e.event_type == EventType.TASK_REVIEW for e in events)
        assert len(turns) == 1 and due.id in turns[0]
        assert await restarted.event_store.pending_runtime_events() == []
        assert (await restarted.event_store.scene_tasks("group:1"))[0]["status"] == "processing"
    finally:
        await restarted.stop()

@pytest.mark.asyncio
async def test_recovery_ignores_unrelated_pending_events(tmp_path):
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path/"unmatched.db")))
    await rt.start()
    await rt.scheduler.stop()
    try:
        task = await pending(rt)
        trigger = "current:claim"
        assert await rt.event_store.claim_task(task.id, trigger)
        unrelated = [
            Event(event_type=event_type, scene_id=scene_id, actor_id="system:scheduler",
                  payload={"task_id":task_id, "trigger_event_id":event_trigger})
            for event_type, scene_id, task_id, event_trigger in [
                (EventType.TASK_DUE, task.scene_id, "other:task", trigger),
                (EventType.TASK_DUE, "group:other", task.id, trigger),
                (EventType.TASK_DUE, task.scene_id, task.id, "old:claim"),
                (EventType.TOOL_COMPLETED, task.scene_id, task.id, trigger),
            ]
        ]
        await rt.event_store._db.executemany(
            "INSERT INTO pending_runtime_events VALUES (?,?,?)",
            [(event.id, event.scene_id, event.model_dump_json()) for event in unrelated],
        )
        await rt.event_store._db.commit()
        await rt.event_store.recover_task_execution()
        await rt.event_store.recover_task_execution()
        assert (await rt.event_store.scene_tasks(task.scene_id))[0]["status"] == "review_required"
        pending_events = await rt.event_store.pending_runtime_events()
        reviews = [event for event in pending_events if event.event_type == EventType.TASK_REVIEW]
        assert len(reviews) == 1
        assert reviews[0].scene_id == task.scene_id
        assert reviews[0].payload["task_id"] == task.id
        assert reviews[0].payload["trigger_event_id"] == trigger
        assert {event.id for event in pending_events} == {event.id for event in [*unrelated, reviews[0]]}
        assert not await rt.event_store.task_due_is_current(unrelated[2])
    finally:
        await rt.stop()

@pytest.mark.asyncio
async def test_incomplete_delivery_never_auto_resends_after_restart(tmp_path):
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path/"uncertain.db")))
    await rt.start()
    await allow_fake_delivery(rt, 'group:1')
    await rt.scheduler.stop()
    try:
        task = await pending(rt)
        due = Event(event_type=EventType.TASK_DUE, scene_id=task.scene_id, actor_id="system", payload={"task_id":task.id})
        await rt.event_store.claim_task_event(task.id, task.scene_id, due)
        await rt.commit_tool_observation(due)
        await rt.event_store.commit_proposal_transaction("test",task.scene_id,[],[],[],deliveries={task.id:"attempt-1"})
        await rt.event_store.recover_task_execution()
        assert (await rt.event_store.scene_tasks(task.scene_id))[0]["status"] == "delivery_unknown"
        assert await rt.event_store.get_pending_tasks() == []
        assert await rt.event_store.pending_runtime_events() == []
    finally:
        await rt.stop()
