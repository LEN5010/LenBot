import asyncio
import pytest

from len_bot.actions.models import ActionItem, ActionType, DeliveryStatus
from len_bot.actions.queue import ActionQueue
from len_bot.adapters.onebot import OneBotAdapter
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.scenes.actor import SceneActor
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.runtime.gate import RuntimeGate


def action():
    return ActionItem(action_type=ActionType.SEND_GROUP_MESSAGE, scene_id="group:1", content="来了")


@pytest.mark.asyncio
async def test_delivery_no_connection_and_rejection_redaction():
    adapter = OneBotAdapter(RuntimeConfig(onebot_access_token="secret-token"), on_event=None)
    assert (await adapter.send_action(action())).status == DeliveryStatus.NOT_SENT
    result = adapter._delivery_response({"status": "failed", "retcode": 1200,
        "wording": "bad secret-token https://host/path?key=abc Bearer other-key"}, "http")
    assert result.status == DeliveryStatus.REJECTED
    assert result.error_code == "1200"
    assert not any(x in result.error for x in ("secret-token", "key=abc", "other-key"))
    assert adapter._delivery_response({"status": "async"}, "websocket").status == DeliveryStatus.UNKNOWN


@pytest.mark.asyncio
async def test_delivery_disconnect_after_write_is_unknown():
    class Socket:
        async def send(self, payload):
            raise ConnectionError("secret URL must not be exposed")
    adapter = OneBotAdapter(RuntimeConfig(), on_event=None)
    adapter._active_ws = Socket()
    result = await adapter.send_action(action())
    assert result.status == DeliveryStatus.UNKNOWN
    assert "secret" not in result.error
    assert not adapter._pending_requests


@pytest.mark.asyncio
async def test_missing_adapter_never_confirms_delivery(tmp_path):
    store = EventStore(str(tmp_path / "delivery.db"))
    await store.initialize()
    try:
        await ActionQueue(store)._process(action())
        events = await store.get_recent_events("group:1")
        assert events[-1].event_type == EventType.MESSAGE_SEND_FAILED
        assert events[-1].payload["delivery_status"] == "not_sent"
    finally:
        await store.close()


