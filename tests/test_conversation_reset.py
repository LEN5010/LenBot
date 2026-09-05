"""Reset cancels old work before deleting its evidence and resumes cleanly."""

import asyncio
import io
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image

from len_bot.actions.models import DeliveryResult, DeliveryStatus
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.memory.models import MemoryItem
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.scheduler.models import TaskItem
from len_bot.testing.social import social_result
from len_bot.tools.results import ToolResult
from len_bot.web.app import create_app


@pytest.mark.asyncio
async def test_authenticated_reset_cancels_old_cognition_and_resumes_without_history(tmp_path):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    delivered = asyncio.Event()
    sent = []
    prompts = []

    async def cognition(messages):
        prompt = str(messages)
        prompts.append(prompt)
        if len(prompts) == 1:
            assert "旧的群聊消息_待取消" in prompt
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        assert "旧的群聊消息_待取消" not in prompt
        assert "旧的私聊内容" not in prompt
        return social_result(reason="回应重置后的新消息", content="从新话题开始")

    async def send(action):
        sent.append(action.content)
        delivered.set()
        return DeliveryResult(status=DeliveryStatus.SENT, transport="test")

    runtime = AgentRuntime(
        RuntimeConfig(
            db_path=str(tmp_path / "reset.db"), bot_qq=42,
            openai_api_key="", onebot_access_token="", message_pacing=False,
            dashboard_default_admin_password="reset-test-password",
        ),
        send_adapter=send, mock_social_handler=cognition,
    )
    await runtime.start()
    group, private = "group:126300994", "private:8"
    try:
        await runtime.set_delivery_scenes([group])
        await runtime.set_shadow_mode(False)
        await runtime.event_store.save_dynamic_config("onebot_config", {"onebot_ws_url": "ws://localhost:12345"})
        app = create_app(runtime)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/api/auth/login", json={"username": "admin", "password": "reset-test-password"})
            assert login.status_code == 200
            persona = await client.post("/api/settings/persona", json={"identity_name": "重置测试", "identity_persona": "保留的人格"})
            assert persona.status_code == 200
            example = await client.post("/api/voice/exemplars", json={"content": "保留的表达样例", "context": "问候"})
            assert example.status_code == 200
            exemplars_before = (await client.get("/api/voice/exemplars")).json()
            config_before = {key: await runtime.event_store.get_dynamic_config(key) for key in
                             ("provider_config", "persona_config", "onebot_config", "shadow_config", "delivery_scenes")}
            user_before = await runtime.event_store.get_dashboard_user("admin")

            # Old private input stays durable without starting a second episode.
            private_event = Event(event_type=EventType.PRIVATE_MESSAGE_RECEIVED, scene_id=private,
                                  actor_id="user:8", timestamp=time.time() - 120,
                                  payload={"raw_text": "旧的私聊内容"})
            await runtime.receive_event(private_event)
            private_actor = await runtime.scene_manager.get_or_create_actor(private)
            await private_actor._queue.join()
            await runtime.memory_store.save_memory(MemoryItem(
                id="old-memory", subject="user:8", kind="preference", key="drink", value="茶",
                scope=private, evidence_event_ids=[private_event.id], human_readable_assertion="喜欢喝茶",
            ))
            task = TaskItem(id="old-task", scene_id=group, description="旧的提醒", due_at=time.time() + 3600)
            await runtime.event_store.save_task(task)
            runtime.scheduler.schedule_task(task)
            observation, event = await runtime.event_store.save_tool_observation(
                group, "web_search", {"query": "旧资料"}, ToolResult(content="旧查询结果"))
            await runtime.commit_tool_observation(event)
            picture = io.BytesIO()
            Image.new("RGB", (2, 2), "red").save(picture, format="PNG")
            asset = await runtime.media_service.upload(picture.getvalue(), group, "旧图片", [])
            assert Path(asset["path"]).is_file()

            await runtime.receive_event(Event(event_type=EventType.GROUP_MESSAGE_RECEIVED,
                scene_id=group, actor_id="user:7", payload={"raw_text": "旧的群聊消息_待取消", "at_bot": True}))
            await asyncio.wait_for(entered.wait(), timeout=3)
            response = await asyncio.wait_for(client.post("/api/settings/reset"), timeout=5)
            assert response.status_code == 200, response.text
            assert response.json()["success"] is True
            assert cancelled.is_set() and sent == []
            assert len(prompts) == 1
            for scene in (group, private):
                assert await runtime.event_store.get_recent_events(scene) == []
                assert runtime.scene_manager.get_group_session(scene) is None
                assert await runtime.memory_store.query_memories([scene]) == []
            assert await runtime.event_store.get_pending_tasks() == []
            assert await runtime.event_store.read_tool_observation(observation.result_id, [group]) is None
            assert await runtime.event_store.get_media(asset["id"], [group]) is None
            assert not runtime.media_service.root.exists()
            assert await runtime.event_store.get_dashboard_user("admin") == user_before
            assert (await client.get("/api/auth/me")).status_code == 200
            assert (await client.get("/api/voice/exemplars")).json() == exemplars_before
            assert {key: await runtime.event_store.get_dynamic_config(key) for key in config_before} == config_before
            assert runtime.config.identity_name == "重置测试"
            assert runtime.provider_registry.export() == config_before["provider_config"]
            assert runtime.shadow_mode is False and runtime.allowed_scenes == {group}

            await runtime.receive_event(Event(event_type=EventType.GROUP_MESSAGE_RECEIVED,
                scene_id=group, actor_id="user:7", payload={"raw_text": "重置后的新消息", "at_bot": True}))
            await asyncio.wait_for(delivered.wait(), timeout=3)
            await asyncio.wait_for(runtime.action_queue._queue.join(), timeout=3)
            group_actor = await runtime.scene_manager.get_or_create_actor(group)
            await asyncio.wait_for(group_actor._queue.join(), timeout=3)
            assert sent == ["从新话题开始"]
            events = await runtime.event_store.get_recent_events(group)
            assert any(event.event_type == EventType.MESSAGE_SENT for event in events)
    finally:
        await runtime.stop()
