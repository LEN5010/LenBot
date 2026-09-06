import asyncio
import base64
import io
import json

import httpx
import pytest
from PIL import Image

from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.events.models import Event, EventType
from len_bot.actions.models import ActionItem, ActionType, DeliveryResult, DeliveryStatus
from len_bot.adapters.onebot import OneBotAdapter
from len_bot.media.models import MessageSegment
from len_bot.cognition.models import MessageProposal
from len_bot.cognition.projection import project_event
from len_bot.testing.turns import turn_result
from len_bot.tools.retrieval import RetrievalToolkit
from len_bot.web.app import create_app


def picture():
    stream = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(stream, format="PNG")
    return stream.getvalue()


async def silent(session, events):
    return turn_result(reason="媒体边界测试")


@pytest.mark.asyncio
async def test_image_source_and_quote_references_are_scope_bound(tmp_path):
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "images.db"), bot_qq=999), mock_turn_handler=silent)
    await rt.start()
    adapter = OneBotAdapter(rt.config, rt.receive_event)
    try:
        wire = {"post_type": "message", "message_type": "group", "group_id": 123, "user_id": 1,
                "message_id": 44, "message": [{"type": "image", "data": {"file": "base64://" + base64.b64encode(picture()).decode()}}]}
        event = adapter._normalize_event(wire)
        assert event.raw_text == "[图片]" and event.payload["segments"] == wire["message"]
        await rt.commit_tool_observation(event)
        asset_id = event.metadata["media"][0]["asset_id"]
        assert asset_id in project_event(event, 999)
        asset, data = await rt.media_service.get_bytes(asset_id, "group:123")
        assert data == picture() and not asset["curated"]
        assert await rt.event_store.get_media(asset_id, ["group:other", "global-safe"]) is None
        with pytest.raises(ValueError):
            await rt.media_service.get_bytes(asset_id, "group:other")
        quote = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:123", actor_id="user:1",
                      payload={"raw_text": "这张图", "reply_to_message_id": "44"})
        projected = await rt.event_store.project_reply_context("group:123", [quote])
        assert asset_id in project_event(projected[0], 999)
        other = await rt.event_store.project_reply_context("group:other", [quote])
        assert other[0].metadata["quote_context"]["missing"]
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_curated_upload_toggle_preview_and_typed_protocol(tmp_path):
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "curated.db")), mock_turn_handler=silent)
    await rt.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(rt)), base_url="http://test") as client:
            assert (await client.get("/api/media")).status_code == 401
            await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})
            bad = await client.post("/api/media", files={"file": ("bad.png", b"not an image", "image/png")})
            assert bad.status_code == 400
            response = await client.post("/api/media", data={"scope": "global-safe", "description": "高兴", "tags": "开心 问候"}, files={"file": ("test.png", picture(), "image/png")})
            assert response.status_code == 200
            asset = response.json()
            assert "path" not in asset and "locator" not in asset
            preview = await client.get(f"/api/media/{asset['id']}/file", params={"scene_id": "group:a"})
            assert preview.content == picture()
            message = MessageProposal(segments=[MessageSegment(type="text", text="你好[CQ:at,qq=all]"), MessageSegment(type="image", asset_id=asset["id"])])
            action = ActionItem(action_type=ActionType.SEND_GROUP_MESSAGE, scene_id="group:123", content=message.content, segments=message.segments)
            prepared = await rt.media_service.prepare_action(action)
            endpoint, payload = OneBotAdapter(rt.config, rt.receive_event)._action_payload(prepared)
            assert payload["message"][0]["type"] == "text" and "CQ:at" in payload["message"][0]["data"]["text"]
            assert payload["message"][1]["data"]["file"].startswith("base64://")
            assert "resolved_images" not in prepared.model_dump()
            partial = await rt.media_service.upload(picture(), "global-safe", "笑脸", ["开心"])
            await rt.media_service.upload(picture(), "group:other", "本群素材", ["开心", "问候", "不存在"])
            kit = RetrievalToolkit(rt.event_store, ["group:a", "global-safe"], "group:a",
                                   media_service=rt.media_service, on_observation=rt.commit_tool_observation)
            result = await kit.execute_result("search_media", {"query": "开心 问候 不存在"})
            assert [item["asset_id"] for item in json.loads(result.content)] == [asset["id"], partial["id"]]
            updated = await client.post(f"/api/media/{asset['id']}", json={"scope": "global-safe", "description": "暂停", "tags": ["开心", "问候"], "enabled": False})
            assert updated.status_code == 200
            result = await kit.execute_result("search_media", {"query": "开心 问候 不存在"})
            assert [item["asset_id"] for item in json.loads(result.content)] == [partial["id"]]
            assert (await kit.execute_result("search_media", {"query": "完全没有这个标签"})).status == "no_results"
            with pytest.raises(ValueError):
                await rt.validate_outbound_action(prepared)
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_pacing_one_scene_does_not_block_another_and_rechecks_policy(tmp_path):
    from len_bot.actions.queue import ActionQueue
    from len_bot.events.store import EventStore
    store = EventStore(str(tmp_path / "queue.db"))
    await store.initialize()
    sent, sleeping, release = [], asyncio.Event(), asyncio.Event()
    async def send(action):
        sent.append(action.content)
        return DeliveryResult(status=DeliveryStatus.SENT, transport="test")
    async def sleep(delay):
        assert 0.6 <= delay <= 2
        sleeping.set()
        await release.wait()
    queue = ActionQueue(store, send_adapter=send)
    queue.pacing, queue.sleep = True, sleep
    await queue.start()
    try:
        for index, text in enumerate(["A1", "A2"]):
            queue.enqueue(ActionItem(action_type=ActionType.SEND_GROUP_MESSAGE, scene_id="group:a", content=text,
                                     batch_id="batch-a", batch_index=index, batch_size=2))
        await asyncio.wait_for(sleeping.wait(), 2)
        queue.enqueue(ActionItem(action_type=ActionType.SEND_GROUP_MESSAGE, scene_id="group:b", content="B1"))
        for _ in range(30):
            await asyncio.sleep(0)
            if "B1" in sent: break
        assert "B1" in sent and "A2" not in sent
        queue.scene_shadow_probe = lambda scene: scene == "group:a"
        release.set()
        await queue._queue.join()
        assert sent == ["A1", "B1"]
        assert any(e.event_type == EventType.ACTION_SHADOWED for e in await store.get_recent_events("group:a"))
    finally:
        release.set()
        await queue.stop()
        await store.close()
