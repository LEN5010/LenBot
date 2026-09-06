"""Reset cancels old work before deleting its evidence and resumes cleanly."""

import asyncio
import base64
import io
import time
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI
from PIL import Image

from len_bot.actions.models import DeliveryResult, DeliveryStatus
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.memory.models import MemoryProposal
from len_bot.cognition.models import EpisodeOutcome
from len_bot.cognition.jobs import JobProposal
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.scheduler.models import TaskItem
from runtime_support import configure_fixture_profile
from len_bot.testing.replay import drain
from len_bot.testing.turns import turn_result
from len_bot.tools.results import ToolResult
from len_bot.web.app import create_app


@pytest.mark.asyncio
async def test_authenticated_reset_cancels_old_cognition_and_resumes_without_history(tmp_path):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    delivered = asyncio.Event()
    sent = []
    prompts = []

    async def cognition(session, events):
        prompt = str([event.model_dump(mode="json") for event in events])
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
        return turn_result(reason="回应重置后的新消息", content="从新话题开始")

    async def send(action):
        sent.append(action.content)
        delivered.set()
        return DeliveryResult(status=DeliveryStatus.SENT, transport="test")

    runtime = AgentRuntime(
        RuntimeConfig(
            db_path=str(tmp_path / "reset.db"), bot_qq=42,
            onebot_access_token="", message_pacing=False,
            dashboard_default_admin_password="reset-test-password",
        ),
        send_adapter=send, mock_turn_handler=cognition,
    )
    await runtime.start()
    await configure_fixture_profile(runtime)
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
                                  payload={"raw_text": "旧的私聊内容：我喜欢喝茶"})
            await runtime.receive_event(private_event)
            private_actor = await runtime.scene_manager.get_or_create_actor(private)
            await private_actor._queue.join()
            result = await runtime.operator_outcome(private, EpisodeOutcome(decision_reason="测试种入有证据的旧认识",
                memory_proposals=[MemoryProposal(subject="user:8", kind="preference", statement="用户8说喜欢喝茶",
                    basis="reported", scope=private, evidence=[private_event.id])]))
            assert result.accepted
            task = TaskItem(id="old-task", scene_id=group, description="旧的提醒", due_at=time.time() + 3600)
            await runtime.event_store.save_task(task)
            runtime.scheduler.schedule_task(task)
            observation, event = await runtime.event_store.save_tool_observation(
                group, "web_search", {"query": "旧资料"}, ToolResult(content="旧查询结果"))
            await runtime.commit_tool_observation(event)
            picture = io.BytesIO()
            Image.new("RGB", (2, 2), "red").save(picture, format="PNG")
            old_image = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=group, actor_id="user:7",
                timestamp=time.time()-120, payload={"raw_text": "旧聊天图片", "segments": [{"type": "image", "data": {
                    "file": "base64://"+base64.b64encode(picture.getvalue()).decode()}}]})
            await runtime.commit_tool_observation(old_image)
            asset, _ = await runtime.media_service.get_bytes(old_image.metadata["media"][0]["asset_id"], group)
            assert Path(asset["path"]).is_file()
            sticker = io.BytesIO()
            Image.new("RGB", (2, 2), "blue").save(sticker, format="PNG")
            curated = await runtime.media_service.upload(sticker.getvalue(), "global-safe", "运营表情", ["开心"])

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
                assert runtime.scene_manager.get_session(scene) is None
                assert await runtime.memory_store.query_memories([scene]) == []
            assert await runtime.event_store.get_pending_tasks() == []
            assert await runtime.event_store.read_tool_observation(observation.result_id, [group]) is None
            assert await runtime.event_store.get_media(asset["id"], [group]) is None
            assert not Path(asset["path"]).exists()
            assert (await runtime.event_store.get_media(curated["id"], ["global-safe"]))["description"] == "运营表情"
            assert Path(curated["path"]).is_file()
            assert await runtime.event_store.event_exists(curated["source_event_id"], "global-safe")
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


@pytest.mark.asyncio
async def test_reset_waits_for_active_work_and_reflection_before_clearing_results(tmp_path):
    entered = {role: asyncio.Event() for role in ("work", "reflection")}
    cancelled = {role: asyncio.Event() for role in entered}
    requests = []

    async def quiet(session, events):
        return turn_result(reason="仅保存测试原话")

    async def model(request):
        import json
        payload = json.loads(request.content)
        names = {tool["function"]["name"] for tool in payload["tools"]}
        role = "reflection" if "finish_reflection" in names else "work"
        requests.append(role)
        entered[role].set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled[role].set()
            raise

    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "workers-reset.db"), bot_qq=42,
        reflection_quiet_window_seconds=3600), mock_turn_handler=quiet, clock=lambda: 1000)
    await runtime.start()
    await configure_fixture_profile(runtime)
    model_client = AsyncOpenAI(api_key="fixture", base_url="https://fixture.invalid/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(model)))
    runtime.provider_registry._clients["fixture"] = model_client
    scene = "group:126300994"
    try:
        source = Event(id="old-work-source", event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id=scene, actor_id="user:8", timestamp=1000, payload={"raw_text": "请核对旧资料", "at_bot": True})
        await runtime.receive_event(source)
        await drain(runtime)
        decision = await runtime.operator_outcome(scene, EpisodeOutcome(decision_reason="测试旧信息工作",
            job_proposals=[JobProposal(proposal_id="old-work", goal="核对旧资料", source_event_ids=[source.id])]))
        assert decision.accepted
        await runtime.scheduler.run_due(1000)
        await asyncio.wait_for(entered["work"].wait(), 3)
        runtime.mock_turn_handler = None
        reflection = runtime._spawn_background_task(runtime._quiet_window_reflect(scene))
        await asyncio.wait_for(entered["reflection"].wait(), 3)
        result = await asyncio.wait_for(runtime.reset_conversation_data("tester"), 5)
        assert result["success"]
        assert all(event.is_set() for event in cancelled.values())
        assert reflection.done()
        assert await runtime.event_store.list_jobs() == []
        assert await runtime.event_store.get_recent_events(scene) == []
        assert await runtime.event_store.list_tool_observations(scene) == []
        assert runtime.scene_manager.get_session(scene) is None
        assert sorted(requests) == ["reflection", "work"]
        assert runtime.provider_registry.snapshot()["routing"]["work"]["model"] == "fixture-model"
    finally:
        await runtime.stop()
        await model_client.close()
