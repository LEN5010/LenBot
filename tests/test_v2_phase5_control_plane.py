import pytest
import asyncio
import time
from httpx import AsyncClient, ASGITransport
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.cognition.router import CognitiveTier
from len_bot.web.app import create_app
from len_bot.events.models import Event, EventType
from len_bot.scheduler.models import TaskItem, TaskStatus
from len_bot.memory.models import MemoryItem, MemoryCertainty, MemoryStatus

@pytest.mark.asyncio
async def test_cockpit_scene_inspection(tmp_path):
    """
    Goal 8: Inspect the actual scene state.
    """
    db_file = str(tmp_path / "cockpit_scene.db")
    config = RuntimeConfig(
        db_path=db_file,
        dashboard_enabled=False,
        dashboard_default_admin_user="admin",
        dashboard_default_admin_password="lenbot123"
    )
    runtime = AgentRuntime(config)
    await runtime.start()

    # Pre-seed a scene with message
    scene_id = "group:cockpit_1"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    ev = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:member1",
        timestamp=time.time(),
        payload={"raw_text": "群友们好！"}
    )
    await runtime.receive_event(ev)
    await asyncio.sleep(0.1)

    app = create_app(runtime)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Auth login
        login_res = await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})
        assert "session_token" in login_res.cookies
        headers = {}

        # 1. List Scenes
        scenes_res = await client.get("/api/cockpit/scenes", headers=headers)
        assert scenes_res.status_code == 200
        scenes_data = scenes_res.json()["scenes"]
        assert any(s["scene_id"] == scene_id for s in scenes_data)

        # 2. Get Scene Detail
        detail_res = await client.get(f"/api/cockpit/scenes/{scene_id}", headers=headers)
        assert detail_res.status_code == 200
        detail = detail_res.json()
        assert detail["scene_id"] == scene_id
        assert "user:member1" in detail["participants"]

    await runtime.stop()

@pytest.mark.asyncio
async def test_cockpit_task_loop_memory_interventions(tmp_path):
    """
    Goal 8: Task cancellation, trigger-now, loop resolution, and memory refute/supersede.
    """
    db_file = str(tmp_path / "cockpit_interventions.db")
    config = RuntimeConfig(
        db_path=db_file,
        dashboard_enabled=False,
        dashboard_default_admin_user="admin",
        dashboard_default_admin_password="lenbot123"
    )
    runtime = AgentRuntime(config)
    await runtime.start()

    app = create_app(runtime)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_res = await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})
        assert "session_token" in login_res.cookies
        headers = {}

        # --- PART 1: Tasks ---
        # Seed Task 1 (to cancel) and Task 2 (to trigger now)
        t1 = TaskItem(
            id="task_cancel_1",
            scene_id="group:tasks",
            description="待取消的延迟任务",
            due_at=time.time() + 3600,
            status=TaskStatus.PENDING
        )
        t2 = TaskItem(
            id="task_trigger_2",
            scene_id="group:tasks",
            description="待立即触发的任务",
            due_at=time.time() + 3600,
            status=TaskStatus.PENDING
        )
        await runtime.event_store.save_task(t1)
        runtime.scheduler.schedule_task(t1)
        await runtime.event_store.save_task(t2)
        runtime.scheduler.schedule_task(t2)

        # List tasks
        tasks_list = await client.get("/api/cockpit/tasks?status=pending", headers=headers)
        assert tasks_list.status_code == 200
        t_ids = [t["id"] for t in tasks_list.json()]
        assert "task_cancel_1" in t_ids
        assert "task_trigger_2" in t_ids

        # Cancel Task 1
        cancel_res = await client.post("/api/cockpit/tasks/task_cancel_1/cancel", headers=headers)
        assert cancel_res.status_code == 200
        assert cancel_res.json()["success"] is True

        # Trigger Task 2 now
        trigger_res = await client.post("/api/cockpit/tasks/task_trigger_2/trigger_now", headers=headers)
        assert trigger_res.status_code == 200
        assert trigger_res.json()["success"] is True
        await asyncio.sleep(0.1)

        # Verify TASK_DUE was emitted into EventStore
        cursor = await runtime.event_store._db.execute("SELECT id, event_type FROM events WHERE event_type = 'TASK_DUE';")
        due_events = await cursor.fetchall()
        assert len(due_events) >= 1

        # --- PART 2: Open Loops ---
        # Seed an open loop
        loop_id = "loop_test_99"
        await runtime.event_store.save_open_loop({
            "id": loop_id,
            "scene_id": "group:tasks",
            "target_actor_id": "user:target",
            "intent": "询问是否参加周六聚餐",
            "source_event_id": "ev_test_loop",
            "status": "active",
            "created_at": time.time(),
            "expires_at": time.time() + 1800
        })

        loops_list = await client.get("/api/cockpit/loops?status=active", headers=headers)
        assert loops_list.status_code == 200
        assert any(l["id"] == loop_id for l in loops_list.json())

        # Resolve Open Loop manually
        resolve_res = await client.post(f"/api/cockpit/loops/{loop_id}/resolve", headers=headers)
        assert resolve_res.status_code == 200
        assert resolve_res.json()["success"] is True

        # Verify loop is resolved
        loops_after = await client.get("/api/cockpit/loops?status=active", headers=headers)
        assert not any(l["id"] == loop_id for l in loops_after.json())

        # --- PART 3: Memories ---
        mem = MemoryItem(
            id="mem_admin_test",
            subject="user:3003",
            kind="fact",
            key="major",
            value="computer_science",
            certainty=MemoryCertainty.STRONG,
            scope="group:tasks",
            status=MemoryStatus.ACTIVE,
            human_readable_assertion="User 3003 的专业是计算机科学"
        )
        await runtime.memory_store.save_memory(mem)

        mems_list = await client.get("/api/cockpit/memories?status=active", headers=headers)
        assert mems_list.status_code == 200
        assert any(m["id"] == "mem_admin_test" for m in mems_list.json())

        # Refute memory manually
        refute_res = await client.post(f"/api/cockpit/memories/{mem.id}/refute", headers=headers, json={"reason": "用户已退学"})
        assert refute_res.status_code == 200
        assert refute_res.json()["status"] == "refuted"

        # Verify it is no longer returned in active query
        active_mems = await runtime.memory_store.query_memories(["group:tasks"], subject="user:3003")
        assert len(active_mems) == 0

    await runtime.stop()

@pytest.mark.asyncio
async def test_goal9_dynamic_config_hot_reload_and_persistence(tmp_path):
    """
    Goal 9: Dynamic configuration hot reload and persistence across process restarts.
    """
    db_file = str(tmp_path / "goal9_configs.db")
    config = RuntimeConfig(
        db_path=db_file,
        dashboard_enabled=False,
        dashboard_default_admin_user="admin",
        dashboard_default_admin_password="lenbot123"
    )
    runtime1 = AgentRuntime(config)
    await runtime1.start()

    app1 = create_app(runtime1)
    transport1 = ASGITransport(app=app1)
    async with AsyncClient(transport=transport1, base_url="http://test") as client:
        login_res = await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})
        assert "session_token" in login_res.cookies
        headers = {}

        # 1. Update Provider & Routing (ADR-0020 replaces legacy /api/models/config)
        provider_post = await client.post("/api/models/providers", headers=headers, json={
            "id": "hot-provider",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-hot-update-key",
            "enabled": True
        })
        assert provider_post.status_code == 200

        routing_post = await client.post("/api/models/routing", headers=headers, json={
            "normal_provider_id": "hot-provider",
            "normal_model": "gpt-4o-latest",
            "deliberate_provider_id": "hot-provider",
            "deliberate_model": "o1-preview"
        })
        assert routing_post.status_code == 200

        # Verify hot reload on runtime1 without restart
        normal_res = runtime1.provider_registry.resolve(CognitiveTier.NORMAL)
        deliberate_res = runtime1.provider_registry.resolve(CognitiveTier.DELIBERATE)
        assert normal_res.model == "gpt-4o-latest"
        assert deliberate_res.model == "o1-preview"

        # 2. Update Persona Config
        persona_post = await client.post("/api/settings/persona", headers=headers, json={
            "identity_name": "LenHotPersona",
            "identity_persona": "热更新后的人设",
            "bot_qq": 999888777
        })
        assert persona_post.status_code == 200
        assert runtime1.config.identity_name == "LenHotPersona"
        assert runtime1.config.bot_qq == 999888777
        assert runtime1.bot_actor_id == "user:999888777"
        assert runtime1.action_queue.bot_actor_id == "user:999888777"

    # Stop runtime1 (simulating process shutdown)
    await runtime1.stop()

    # 4. Start fresh runtime2 from the exact same SQLite database
    runtime2 = AgentRuntime(RuntimeConfig(db_path=db_file))
    await runtime2.start()

    # Verify all configs were restored from persistent storage!
    normal2 = runtime2.provider_registry.resolve(CognitiveTier.NORMAL)
    deliberate2 = runtime2.provider_registry.resolve(CognitiveTier.DELIBERATE)
    assert normal2.model == "gpt-4o-latest" and normal2.provider_id == "hot-provider"
    assert deliberate2.model == "o1-preview"
    assert runtime2.config.identity_name == "LenHotPersona"
    assert runtime2.config.bot_qq == 999888777
    assert runtime2.bot_actor_id == "user:999888777"
    await runtime2.stop()
