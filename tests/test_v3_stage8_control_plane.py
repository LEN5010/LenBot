import pytest
import asyncio
import json
import time
from httpx import AsyncClient, ASGITransport
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.web.app import create_app
from len_bot.adapters.onebot import OneBotAdapter
from len_bot.actions.models import ActionItem, ActionType

@pytest.mark.asyncio
async def test_shadow_annotations_crud_and_metrics(tmp_path):
    """ADR-0031, §23.3: Shadow annotations are persisted in SQLite and queried with precision/accuracy."""
    db_file = str(tmp_path / "shadow_ann.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    app = create_app(runtime)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_res = await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})
        assert login_res.status_code == 200

        # 1. Post TP annotation
        r1 = await client.post("/api/cockpit/shadow-annotations", json={
            "scene_id": "group:test_ann",
            "stimulus_id": "stim_1",
            "label": "TP",
            "comment": "Bot rightly wanted to speak"
        })
        assert r1.status_code == 200

        # 2. Post FP annotation
        r2 = await client.post("/api/cockpit/shadow-annotations", json={
            "scene_id": "group:test_ann",
            "stimulus_id": "stim_2",
            "label": "FP",
            "comment": "Bot should have stayed silent"
        })
        assert r2.status_code == 200

        # 3. Query annotations
        query_res = await client.get("/api/cockpit/shadow-annotations")
        assert query_res.status_code == 200
        data = query_res.json()
        assert len(data["annotations"]) == 2
        assert data["stats"]["TP"] == 1
        assert data["stats"]["FP"] == 1
        assert data["total"] == 2
        assert data["precision"] == 0.5

    await runtime.stop()


@pytest.mark.asyncio
async def test_onebot_adapter_drops_self_echo_and_extracts_reply():
    """ADR-0031, §17: OneBotAdapter drops self-sent echo and extracts reply_to_message_id."""
    config = RuntimeConfig(bot_qq=12345678)
    adapter = OneBotAdapter(config, on_event=lambda e: None)

    # 1. Self-sent message must be dropped
    self_event = adapter._normalize_event({
        "post_type": "message",
        "message_type": "group",
        "group_id": 999,
        "user_id": 12345678,  # Same as bot_qq!
        "raw_message": "I am the bot echoing",
        "message_id": 1001
    })
    assert self_event is None

    # 2. User message with reply CQ code must extract reply_to_message_id
    user_event = adapter._normalize_event({
        "post_type": "message",
        "message_type": "group",
        "group_id": 999,
        "user_id": 87654321,
        "raw_message": "[CQ:reply,id=54321] Hello bot!",
        "message_id": 1002
    })
    assert user_event is not None
    assert user_event.payload["message_id"] == 1002
    assert user_event.payload["reply_to_message_id"] == "54321"

    # Ring buffer recorded the reply link
    assert len(adapter._reply_cache) == 1
    assert adapter._reply_cache[-1] == ("1002", "54321")

    adapter._own_message_ids.append("54321")
    reply_event = adapter._normalize_event({
        "post_type": "message",
        "message_type": "group",
        "group_id": 999,
        "user_id": 87654321,
        "raw_message": "[CQ:reply,id=54321] 接着说",
        "message_id": 1003,
    })
    assert reply_event.payload["reply_bot"] is True


@pytest.mark.asyncio
async def test_onebot_http_action_uses_bearer_token_and_records_sent_message(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "ok", "retcode": 0, "data": {"message_id": 7788}}

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append({"headers": kwargs["headers"]})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            calls[-1].update({"url": url, "json": json})
            return FakeResponse()

    monkeypatch.setattr("len_bot.adapters.onebot.httpx.AsyncClient", FakeClient)
    adapter = OneBotAdapter(
        RuntimeConfig(
            bot_qq=123,
            onebot_action_transport="http",
            onebot_http_url="http://127.0.0.1:13000/",
            onebot_access_token="test-token",
        ),
        on_event=lambda event: None,
    )
    sent = await adapter.send_action(ActionItem(
        action_type=ActionType.SEND_GROUP_MESSAGE,
        scene_id="group:456",
        content="测试消息",
        reply_to="99",
    ))

    assert sent is True
    assert calls == [{
        "headers": {"Authorization": "Bearer test-token"},
        "url": "http://127.0.0.1:13000/send_group_msg",
        "json": {"message": "[CQ:reply,id=99]测试消息", "group_id": 456},
    }]
    assert list(adapter._own_message_ids) == ["7788"]


@pytest.mark.asyncio
async def test_onebot_forward_websocket_connects_with_token_and_delivers_events(monkeypatch):
    received = asyncio.get_running_loop().create_future()
    connect_args = {}

    async def on_event(event):
        if not received.done():
            received.set_result(event)

    class FakeSocket:
        remote_address = ("127.0.0.1", 13001)

        def __init__(self):
            self.messages = [json.dumps({
                "post_type": "message",
                "message_type": "group",
                "group_id": 456,
                "user_id": 789,
                "raw_message": "连接成功后的第一条消息",
                "message_id": 100,
                "time": time.time(),
            })]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.messages:
                return self.messages.pop(0)
            await asyncio.Event().wait()

        async def close(self, **_kwargs):
            return None

    class FakeConnection:
        async def __aenter__(self):
            return FakeSocket()

        async def __aexit__(self, *_args):
            return None

    def fake_connect(uri, **kwargs):
        connect_args.update({"uri": uri, **kwargs})
        return FakeConnection()

    monkeypatch.setattr("len_bot.adapters.onebot.websockets.connect", fake_connect)
    adapter = OneBotAdapter(
        RuntimeConfig(
            bot_qq=123,
            onebot_connection_mode="forward_ws",
            onebot_ws_url="ws://127.0.0.1:13001/",
            onebot_access_token="test-token",
        ),
        on_event=on_event,
    )

    await adapter.start()
    event = await asyncio.wait_for(received, 1.0)
    await adapter.stop()

    assert connect_args["uri"] == "ws://127.0.0.1:13001/"
    assert connect_args["additional_headers"] == {"Authorization": "Bearer test-token"}
    assert connect_args["proxy"] is None
    assert event.scene_id == "group:456"
    assert event.actor_id == "user:789"


@pytest.mark.asyncio
async def test_onebot_websocket_action_correlates_echo_and_records_message_id():
    adapter = OneBotAdapter(RuntimeConfig(bot_qq=123), on_event=lambda event: None)
    payloads = []

    class FakeSocket:
        async def send(self, raw):
            payload = json.loads(raw)
            payloads.append(payload)
            adapter._pending_requests[payload["echo"]].set_result({
                "status": "ok",
                "retcode": 0,
                "data": {"message_id": 8899},
                "echo": payload["echo"],
            })

    adapter._active_ws = FakeSocket()
    sent = await adapter.send_action(ActionItem(
        action_type=ActionType.SEND_PRIVATE_MESSAGE,
        scene_id="private:456",
        content="你好",
    ))

    assert sent is True
    assert payloads == [{
        "action": "send_private_msg",
        "params": {"message": "你好", "user_id": 456},
        "echo": "echo_1",
    }]
    assert list(adapter._own_message_ids) == ["8899"]
