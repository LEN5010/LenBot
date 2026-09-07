import base64
import io
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from PIL import Image

from len_bot.actions.models import ActionItem, ActionType
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.media.models import MessageSegment, segment_text
from len_bot.media.service import MediaService
from len_bot.memory.store import MemoryStore
from len_bot.tools.results import ToolResult
from len_bot.web.auth import get_current_user
from len_bot.web.routes.media import router


def picture(color="red", size=(40, 30), *, animated=False):
    data = io.BytesIO()
    first = Image.new("RGB", size, color)
    if animated:
        first.save(data, format="GIF", save_all=True, append_images=[Image.new("RGB", size, "blue")], duration=100)
    else:
        first.save(data, format="PNG")
    return data.getvalue()


def decoded(block):
    return Image.open(io.BytesIO(base64.b64decode(block["image_url"]["url"].split(",", 1)[1])))


@pytest_asyncio.fixture
async def media(tmp_path):
    store = EventStore(str(tmp_path / "media.db"))
    await store.initialize()
    await MemoryStore(store._db, store._write_lock).initialize()
    runtime = SimpleNamespace(config=SimpleNamespace(db_path=store.db_path, media_enabled=True),
        event_store=store, commit_tool_observation=store.append_event)
    service = MediaService(runtime)
    runtime.media_service = service
    try:
        yield service, store
    finally:
        await service.close()
        await store.close()


@pytest.mark.asyncio
async def test_native_images_are_scoped_deduplicated_and_report_coverage(media):
    service, _ = media
    large = await service.upload(picture(size=(3000, 1000)), "group:a", "原图", [])
    animation = await service.upload(picture(animated=True), "global-safe", "动图", [])
    foreign = await service.upload(picture("green"), "group:b", "其他群", [])
    disabled = await service.upload(picture("yellow"), "group:a", "停用", [])
    await service.edit(disabled["id"], "group:a", "停用", [], False)
    prepared = await service.prepare_context_images("group:a", [large["id"], large["id"], foreign["id"], animation["id"], disabled["id"]])
    assert len(prepared["blocks"]) == 2
    assert [item["status"] for item in prepared["manifest"]] == ["included", "error", "included", "error"]
    assert prepared["manifest"][0]["source_event_id"] == large["source_event_id"]
    assert prepared["manifest"][0]["sha256"] == large["sha256"]
    assert prepared["manifest"][2]["coverage"] == "first_frame"
    with decoded(prepared["blocks"][0]) as image:
        assert max(image.size) == 2048 and image.getpixel((0, 0)) == (255, 0, 0)
    with decoded(prepared["blocks"][1]) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)
    bounded = await service.prepare_context_images("group:a", [large["id"], animation["id"]], limit=1)
    assert bounded["manifest"][1] == {"asset_id": animation["id"], "status": "omitted", "reason": "image_limit"}
    service.runtime.config.media_enabled = False
    assert (await service.prepare_context_images("group:a", [large["id"]]))["blocks"] == []
    assert (await service.read_media(large["id"], "group:a")).status == "unsupported"


@pytest.mark.asyncio
async def test_media_observation_keeps_attachment_references_without_a_model(media):
    service, store = media
    asset = await service.upload(picture(), "group:a", "原始照片", [])
    result = await service.read_media(asset["id"], "group:a")
    assert result.attachments == [asset["id"]] and result.evidence_kind == "retrieval"
    assert result.sources[0].event_id == asset["source_event_id"]
    assert result.coverage == "image" and "data:image" not in str(result)
    persisted, _ = await store.save_tool_observation("group:a", "read_media", {"asset_id": asset["id"]}, result)
    restored = await store.read_tool_observation(persisted.result_id, ["group:a"])
    assert restored.attachments == [asset["id"]]
    assert restored.page(limit=1).attachments == [asset["id"]]
    assert await store.read_tool_observation(persisted.result_id, ["group:b"]) is None
    assert (await service.read_media(asset["id"], "group:b")).attachments == []
    assert ToolResult.model_validate({"status": "ok", "content": "历史资料"}).attachments == []


@pytest.mark.asyncio
async def test_operator_palette_has_fixed_order_limit_and_invalidates_cache(media, monkeypatch):
    service, store = media
    assets = []
    for index in range(22):
        asset = await service.upload(picture(), "global-safe", f"表情{index}", ["开心"])
        await service.edit(asset["id"], asset["scope"], asset["description"], asset["tags"], True, palette_order=index)
        assets.append(asset)
    foreign = await service.upload(picture(), "group:b", "另一个群的目录项", [])
    await service.edit(foreign["id"], "group:b", foreign["description"], [], True, palette_order=0)
    await service.upload(picture(), "group:a", "没选入目录", [])
    palette = await store.list_palette("group:a")
    assert [asset["id"] for asset in palette] == [asset["id"] for asset in assets[:20]]
    first = await service.prepare_palette("group:a", include_pixels=True)
    assert [item["ref"] for item in first["manifest"]] == [f"P{i:02d}" for i in range(1, 21)]
    assert all(item["status"] == "included" for item in first["manifest"])
    with decoded(first["blocks"][0]) as image:
        assert image.size == (1600, 1400)
    async def no_io(*args):
        raise AssertionError("Unchanged palette must reuse its derived sheet")
    with monkeypatch.context() as patch:
        patch.setattr(service, "_prepare_asset", no_io)
        assert await service.prepare_palette("group:a", include_pixels=True) == first
    await service.edit(assets[0]["id"], "global-safe", "已停用", [], False)
    updated = await service.prepare_palette("group:a", include_pixels=True)
    assert assets[0]["id"] not in [item["asset_id"] for item in updated["manifest"]]
    assert updated["manifest"][0]["asset_id"] == assets[1]["id"]
    await service.edit(assets[1]["id"], "global-safe", "重命名", [], True, palette_order=30)
    renamed = await service.prepare_palette("group:a", include_pixels=True)
    assert renamed["manifest"][0]["asset_id"] == assets[2]["id"]
    await service.edit(assets[2]["id"], "global-safe", "移出", [], True, palette_order=None)
    assert assets[2]["id"] not in [asset["id"] for asset in await store.list_palette("group:a")]


@pytest.mark.asyncio
async def test_reset_keeps_curated_originals_order_and_source_but_clears_derived_files(media):
    service, store = media
    curated = await service.upload(picture(), "global-safe", "运营素材", [])
    await service.edit(curated["id"], "global-safe", "运营素材", [], True, palette_order=2)
    incoming = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:a", actor_id="user:1",
        payload={"segments": [{"type": "image", "data": {"file": "base64://"+base64.b64encode(picture("blue")).decode()}}]})
    await store.register_event_media_in_transaction(incoming)
    await store.append_event(incoming)
    incoming_asset, _ = await service.get_bytes(incoming.metadata["media"][0]["asset_id"], "group:a")
    derived = service.root / "unused-derived.png"
    derived.write_bytes(picture("yellow"))
    await service.prepare_palette("group:a", include_pixels=True)
    reset = Event(event_type=EventType.OPERATOR_ACTION, scene_id="system", actor_id="operator:test", payload={"command": "reset"})
    await store.reset_conversation_data(reset)
    await service.reset_cache()
    assert service._palette_cache == {}
    assert Path(curated["path"]).is_file()
    assert not Path(incoming_asset["path"]).exists() and not derived.exists()
    retained = await store.get_media(curated["id"], ["global-safe"])
    assert retained["palette_order"] == 2 and retained["source_event_id"] == curated["source_event_id"]


@pytest.mark.asyncio
async def test_palette_edit_api_and_send_preparation_recheck_scope_and_enabled(media):
    service, _ = media
    asset = await service.upload(picture(), "global-safe", "笑", [])
    app = FastAPI()
    app.state.runtime = service.runtime
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = {"scope": "global-safe", "description": "笑", "tags": [], "enabled": True, "palette_order": 3}
        assert (await client.post(f"/api/media/{asset['id']}", json=payload)).status_code == 401
        app.dependency_overrides[get_current_user] = lambda: "operator:test"
        selected = await client.post(f"/api/media/{asset['id']}", json=payload)
        assert selected.status_code == 200 and selected.json()["palette_order"] == 3
        assert "path" not in selected.json() and "locator" not in selected.json()
        payload.pop("palette_order")
        assert (await client.post(f"/api/media/{asset['id']}", json=payload)).json()["palette_order"] == 3
        payload["palette_order"] = None
        assert (await client.post(f"/api/media/{asset['id']}", json=payload)).json()["palette_order"] is None
    segments = [MessageSegment(type="text", text="好耶"), MessageSegment(type="image", asset_id=asset["id"])]
    action = ActionItem(action_type=ActionType.SEND_GROUP_MESSAGE, scene_id="group:a", content=segment_text(segments), segments=segments)
    prepared = await service.prepare_action(action)
    assert prepared.resolved_images[asset["id"]].startswith("base64://")
    assert not prepared.resolved_sticker_ids
    assert "resolved_images" not in prepared.model_dump()
    await service.edit(asset["id"], "global-safe", "笑", [], False)
    with pytest.raises(ValueError):
        await service.prepare_action(action)
    foreign = await service.upload(picture(), "group:b", "私有", [])
    action.segments = [MessageSegment(type="image", asset_id=foreign["id"])]
    action.content = "[图片]"
    with pytest.raises(ValueError):
        await service.prepare_action(action)


@pytest.mark.asyncio
async def test_existing_media_table_adds_nullable_order_without_replacing_assets(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE media_assets (id TEXT PRIMARY KEY, scope TEXT NOT NULL,
            source_event_id TEXT NOT NULL, locator TEXT NOT NULL DEFAULT '', sha256 TEXT,
            mime_type TEXT, path TEXT, description TEXT NOT NULL DEFAULT '', tags_json TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1, curated INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL)""")
        db.execute("""INSERT INTO media_assets(id,scope,source_event_id,description,curated,created_at)
            VALUES('existing','global-safe','original_source','原有素材',1,123)""")
    store = EventStore(str(path))
    await store.initialize()
    try:
        asset = await store.get_media("existing", ["global-safe"])
        assert asset["description"] == "原有素材" and asset["source_event_id"] == "original_source"
        assert asset["palette_order"] is None
        assert await store.list_palette("group:a") == []
        await store.initialize_media()
        assert (await store.get_media("existing", ["global-safe"]))["id"] == "existing"
    finally:
        await store.close()
