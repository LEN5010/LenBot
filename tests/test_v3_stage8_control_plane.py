import pytest
import asyncio
import time
from httpx import AsyncClient, ASGITransport
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.web.app import create_app
from len_bot.adapters.onebot import OneBotAdapter

@pytest.mark.asyncio
async def test_monitored_keywords_hot_reload_on_social_update(tmp_path):
    """ADR-0031, §23.1: POST /api/cockpit/social hot-reloads AttentionEngine keywords live."""
    db_file = str(tmp_path / "hot_reload.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file, monitored_keywords=["cold_key"])
    runtime = AgentRuntime(config)
    await runtime.start()

    app = create_app(runtime)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Login
        login_res = await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})
        assert login_res.status_code == 200

        # Initial check
        assert runtime.attention_engine.monitored_keywords == ["cold_key"]

        # Hot reload via POST /api/cockpit/social
        res = await client.post("/api/cockpit/social", json={
            "monitored_keywords": ["keyword_alpha", "keyword_beta"],
            "speaking_budget_base_threshold": 0.55,
            "interest_topics": {"tech": 0.8}
        })
        assert res.status_code == 200

        # Runtime & AttentionEngine must both be updated!
        assert runtime.config.monitored_keywords == ["keyword_alpha", "keyword_beta"]
        assert runtime.attention_engine.monitored_keywords == ["keyword_alpha", "keyword_beta"]
        assert runtime.attention_engine.interest_model.topic_keywords["monitored"] == ["keyword_alpha", "keyword_beta"]

    await runtime.stop()


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
