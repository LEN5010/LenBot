import pytest
import asyncio
import time
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.events.models import Event, EventType
from len_bot.scheduler.models import TaskItem, TaskStatus
from len_bot.testing.replay import drain

@pytest.mark.asyncio
async def test_condition_bound_wake_match_exact_dict(tmp_path):
    """ADR-0029, §16: Structured wake_match fires only when event payload matches exact dict."""
    db_file = str(tmp_path / "wake_match.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:stream_watch"
    now = time.time()

    # Task 1: wait for room 111
    task1 = TaskItem(
        id="task_room_111",
        scene_id=scene_id,
        description="Notify when room 111 goes live",
        due_at=now + 3600.0,
        status=TaskStatus.PENDING,
        wake_event_type="LIVE_STARTED",
        wake_match={"room_id": 111}
    )
    # Task 2: wait for room 222
    task2 = TaskItem(
        id="task_room_222",
        scene_id=scene_id,
        description="Notify when room 222 goes live",
        due_at=now + 3600.0,
        status=TaskStatus.PENDING,
        wake_event_type="LIVE_STARTED",
        wake_match={"room_id": 222}
    )

    await runtime.event_store.save_task(task1)
    await runtime.event_store.save_task(task2)
    runtime.scheduler.schedule_task(task1)
    runtime.scheduler.schedule_task(task2)

    # Broadcast event for room 111 arrives
    live_event_111 = Event(
        id="ev_live_111",
        event_type=EventType.LIVE_STARTED,
        scene_id=scene_id,
        actor_id="plugin:sensor",
        timestamp=now + 5,
        payload={"room_id": 111, "title": "Room 111 Gaming"}
    )
    await runtime.receive_event(live_event_111)
    await drain(runtime)

    # Task 1 should have fired and been claimed
    tasks = await runtime.event_store.get_pending_tasks()
    pending_ids = [t["id"] for t in tasks]
    assert "task_room_111" not in pending_ids
    assert "task_room_222" in pending_ids

    # Check task 1 status in database
    cursor = await runtime.event_store._db.execute("SELECT status, trigger_event_id FROM tasks WHERE id = ?;", ("task_room_111",))
    row = await cursor.fetchone()
    assert row[0] in ("claimed", "processing")
    assert row[1] == "ev_live_111"

    await runtime.stop()


@pytest.mark.asyncio
async def test_durable_task_claim_prevents_duplicate_emit(tmp_path):
    """ADR-0029, §15: Durable claim prevents duplicate TASK_DUE emit under concurrent firing."""
    db_file = str(tmp_path / "task_claim.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    task = TaskItem(
        id="task_race",
        scene_id="group:race",
        description="Single execution promise",
        due_at=time.time() + 100.0,
        status=TaskStatus.PENDING
    )
    await runtime.event_store.save_task(task)

    # Two concurrent claim attempts
    claim1, claim2 = await asyncio.gather(
        runtime.scheduler._emit_task_due(task, time.time(), trigger_event_id="worker_1"),
        runtime.scheduler._emit_task_due(task, time.time(), trigger_event_id="worker_2"),
    )
    await drain(runtime)

    # Exactly one must succeed
    assert (claim1, claim2) in [(True, False), (False, True)]

    # Check trigger_event_id belongs to the winning worker
    cursor = await runtime.event_store._db.execute("SELECT status, trigger_event_id FROM tasks WHERE id = ?;", ("task_race",))
    row = await cursor.fetchone()
    assert row[0] == "processing"
    assert row[1] in ("worker_1", "worker_2")
    events = await runtime.event_store.get_recent_events(task.scene_id)
    assert sum(event.event_type == EventType.TASK_DUE for event in events) == 1

    await runtime.stop()




@pytest.mark.asyncio
async def test_sync_from_db_restores_shadow_origin_mode(tmp_path):
    """V4 invariant: shadow-origin tasks must survive restart recovery and never revert to live-send."""
    db_file = str(tmp_path / "shadow_recovery.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()
    try:
        now = time.time()
        await runtime.event_store.create_task({
            "id": "task_shadow_restart",
            "scene_id": "group:shadow",
            "description": "Shadow task created before restart",
            "due_at": now + 3600.0,
            "status": "pending",
            "source_event_id": "episode:shadow",
            "payload": {},
            "created_at": now,
            "origin_mode": "shadow",
        })

        # Simulate restart recovery / anti-drift re-sync
        await runtime.scheduler._sync_from_db()

        recovered = next(t for t in runtime.scheduler._heap if t.id == "task_shadow_restart")
        assert recovered.origin_mode == "shadow"
    finally:
        await runtime.stop()
