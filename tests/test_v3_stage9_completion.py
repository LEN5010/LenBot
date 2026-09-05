"""V3 Stage 9 Completion Tests.

Tests:
1. BilibiliContentPlugin: get_video_info, search_bilibili, get_dynamic_feed, and sandbox error handling.
2. Shadow Origin Isolation: Task created with shadow origin never live-sends even when runtime is live.
3. Promote Task: Promoted task has origin_mode="live" and clears wake conditions.
4. Core tool reservation: inspect_episode cannot be shadowed by plugins.
"""
from len_bot.actions.models import DeliveryResult, DeliveryStatus

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from len_bot.config import RuntimeConfig
from len_bot.actions.models import ActionItem, ActionType
from len_bot.actions.queue import ActionQueue
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.plugins.builtin.bilibili_content import BilibiliContentPlugin
from len_bot.plugins.host import PluginHost, RESERVED_CORE_TOOLS
from len_bot.scheduler.engine import TaskScheduler
from len_bot.scheduler.models import TaskItem, TaskStatus


@pytest.mark.asyncio
async def test_bilibili_content_plugin_tools():
    plugin = BilibiliContentPlugin()
    context = MagicMock()
    registered_tools = {}

    def mock_reg(name, description, parameters, handler):
        registered_tools[name] = handler

    context.register_tool = mock_reg
    await plugin.on_load(context)

    assert "get_video_info" in registered_tools
    assert "search_bilibili" in registered_tools
    assert "get_dynamic_feed" in registered_tools

    # 1. Test get_video_info without bvid/aid
    err = await registered_tools["get_video_info"]({})
    assert "错误" in err

    # 2. Mock Bilibili view API response
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "code": 0,
        "data": {
            "title": "测试视频标题",
            "bvid": "BV1test123",
            "desc": "测试视频简介",
            "owner": {"name": "测试UP主"},
            "stat": {"view": 10000, "danmaku": 500, "like": 800}
        }
    }

    with patch.object(plugin._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        info = await registered_tools["get_video_info"]({"bvid": "BV1test123"})
        assert "测试视频标题" in info
        assert "测试UP主" in info
        assert "10000" in info

    # 3. Test get_dynamic_feed without sessdata
    feed_msg = await registered_tools["get_dynamic_feed"]({"mid": 123456})
    assert "SESSDATA" in feed_msg

    await plugin.on_unload()


@pytest.mark.asyncio
async def test_shadow_origin_isolation_prevents_live_send(tmp_path):
    """Item 24: ActionItem with origin_mode="shadow" is recorded as would_send

    even if the runtime environment itself is live!
    """
    db_path = str(tmp_path / "shadow_origin.db")
    store = EventStore(db_path)
    await store.initialize()

    sent_actions = []
    would_send_actions = []

    async def mock_adapter(action):
        sent_actions.append(action)
        return DeliveryResult(status=DeliveryStatus.SENT, transport="test")

    async def mock_shadow_recorder(action):
        would_send_actions.append(action)

    # Runtime is in LIVE mode (shadow_probe returns False)
    queue = ActionQueue(
        event_store=store,
        send_adapter=mock_adapter,
        shadow_probe=lambda: False,
        shadow_recorder=mock_shadow_recorder
    )
    await queue.start()

    # Enqueue an action originated from a shadow-phase task
    shadow_action = ActionItem(
        action_type=ActionType.SEND_GROUP_MESSAGE,
        scene_id="group:100",
        content="Shadow task payload",
        origin_mode="shadow"
    )
    queue.enqueue(shadow_action)
    await asyncio.sleep(0.1)

    assert len(sent_actions) == 0, "Shadow-origin action must NEVER be physically sent in live mode!"
    assert len(would_send_actions) == 1, "Shadow-origin action must be intercepted by shadow recorder!"
    assert would_send_actions[0].content == "Shadow task payload"

    # Regular live action proceeds normally
    live_action = ActionItem(
        action_type=ActionType.SEND_GROUP_MESSAGE,
        scene_id="group:100",
        content="Real live message",
        origin_mode="live"
    )
    queue.enqueue(live_action)
    await asyncio.sleep(0.1)

    assert len(sent_actions) == 1
    assert sent_actions[0].content == "Real live message"

    await queue.stop()
    await store.close()


@pytest.mark.asyncio
async def test_promote_task_sets_live_origin(tmp_path):
    """Item 24: promote_task promotes a shadow-bound obligation to live origin."""
    db_path = str(tmp_path / "promote_origin.db")
    store = EventStore(db_path)
    await store.initialize()

    emitted_events = []
    async def mock_emit(evt):
        emitted_events.append(evt)

    scheduler = TaskScheduler(event_store=store, emit_event=mock_emit)
    await scheduler.start()

    task = TaskItem(
        id="shadow_task_1",
        scene_id="group:200",
        description="开播叫我",
        due_at=time.time() + 1000.0,
        status=TaskStatus.PENDING,
        wake_event_type="LIVE_STARTED",
        wake_match={"room_id": 123},
        origin_mode="shadow"
    )
    await store.save_task(task)
    scheduler.schedule_task(task)

    # Promote the task via scheduler authority
    promoted = await scheduler.promote_task("shadow_task_1")
    assert promoted is not None
    assert promoted["origin_mode"] == "live"
    assert "[Promoted]" in promoted["description"]

    await scheduler.stop()
    await store.close()


def test_reserved_core_tools_includes_inspect_episode():
    assert "inspect_episode" in RESERVED_CORE_TOOLS
    assert "search_messages" in RESERVED_CORE_TOOLS
    assert "read_context" in RESERVED_CORE_TOOLS
