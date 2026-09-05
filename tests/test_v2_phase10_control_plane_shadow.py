from len_bot.actions.models import DeliveryResult, DeliveryStatus
import pytest
import asyncio
import time
from httpx import AsyncClient, ASGITransport

from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.web.app import create_app
from len_bot.events.models import Event, EventType
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal
from len_bot.testing.social import social_result


async def _make_runtime_and_client(config):
    runtime = AgentRuntime(config)
    await runtime.start()
    app = create_app(runtime)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    login = await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})
    assert "session_token" in login.cookies
    headers = {}
    return runtime, client, headers


@pytest.mark.asyncio
async def test_trace_captures_full_causal_chain(tmp_path):
    """
    ADR-0022 (完成定义 Control Plane): from one message you can see
    Event → Attention → Cognition → Gate → durable effects → visible action.
    """
    config = RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "trace.db"))
    sent_actions = []

    async def mock_send(item):
        sent_actions.append(item)
        return DeliveryResult(status=DeliveryStatus.SENT, transport="test")

    async def mock_social_core(messages):
        stimulus_text = messages[-1]["content"].split("【CURRENT BURST】")[-1]
        if "帮我看看" in stimulus_text:
            return social_result(reason="User asked directly", content="看了一下，没问题")
        return social_result(reason="silence")

    runtime = AgentRuntime(config, send_adapter=mock_send, mock_social_handler=mock_social_core)
    await runtime.start()
    app = create_app(runtime)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})
        assert "session_token" in login.cookies
        headers = {}

        scene_id = "group:trace"
        t0 = time.time()
        await runtime.receive_event(Event(
            event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=scene_id,
            actor_id="user:A", timestamp=t0, payload={"raw_text": "@Bot 帮我看看这个", "at_bot": True}
        ))
        await asyncio.sleep(0.4)

        traces = (await client.get("/api/cockpit/traces", params={"scene_id": scene_id}, headers=headers)).json()
        kinds = {t["kind"] for t in traces}
        assert "social_cognition" in kinds
        social_rows = [t for t in traces if t["kind"] == "social_cognition"]
        assert social_rows
        payload = social_rows[0]["payload"]
        assert payload["result"]["decision"]["action"] == "speak"
        assert payload["gate"]["disposition"] == "ACTION"
        assert payload["actions_enqueued"] == 1
        assert len(sent_actions) == 1
        assert payload["cognition"]["mode"] == "mock"

    await runtime.stop()


@pytest.mark.asyncio
async def test_memory_chain_and_filtered_events_via_query_service(tmp_path):
    """ADR-0022: QueryService exposes superseded chains & filtered event queries."""
    # Mock cognition keeps any incidental wake fully offline
    async def mock_social_core(messages):
        return social_result(reason="offline")

    runtime = AgentRuntime(
        RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "chain.db")),
        mock_social_handler=mock_social_core
    )
    await runtime.start()
    app = create_app(runtime)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    login = await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})
    assert "session_token" in login.cookies
    headers = {}

    scene_id = "group:chain"
    from len_bot.memory.models import MemoryItem, MemoryKind, MemoryStatus
    old = MemoryItem(
        subject="user:A", kind=MemoryKind.PREFERENCE, key="food", value="微辣",
        scope=scene_id, evidence=["ev1"], human_readable_assertion="A 吃微辣",
        status=MemoryStatus.SUPERSEDED, superseded_by="mem_new",
        created_at=time.time(), last_confirmed_at=time.time() - 100
    )
    await runtime.memory_store.save_memory(old)
    await runtime.memory_store._db.execute(
        """INSERT INTO memories (id, subject, kind, key, value, temporal, certainty, scope,
                                 evidence, status, human_readable_assertion, created_at, last_confirmed_at)
           VALUES ('mem_new', 'user:A', 'preference', 'food', '不吃辣', 'recent', 'likely', ?,
                   '[]', 'active', 'A 现在完全不吃了', ?, ?);""",
        (scene_id, time.time(), time.time())
    )
    await runtime.memory_store._db.commit()

    chain = (await client.get("/api/cockpit/memories/mem_new/chain", headers=headers)).json()["chain"]
    # Chain is oldest → newest: root ancestor first, then the superseding belief
    assert [m["id"] for m in chain] == [old.id, "mem_new"]
    assert chain[0]["status"] == "superseded"
    assert chain[1]["status"] == "active"

    # Filtered events query
    t0 = time.time()
    ev = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=scene_id,
               actor_id="user:B", timestamp=t0, payload={"raw_text": "标记消息"})
    await runtime.receive_event(ev)
    await asyncio.sleep(0.2)
    filtered = (await client.get(
        "/api/cockpit/../overview/recent_events", headers=headers  # overview endpoint sanity
    ))
    by_scene = (await client.get(
        "/api/cockpit/traces", params={"scene_id": scene_id, "kind": "social_cognition"}, headers=headers
    )).json()
    assert isinstance(by_scene, list)

    # Event filters via query service path (overview recent_events supports filters)
    events = await runtime.query_service.query_events(scene_id=scene_id, actor_id="user:B")
    assert len(events) == 1 and events[0]["actor_id"] == "user:B"

    await client.aclose()
    await runtime.stop()


@pytest.mark.asyncio
async def test_replay_lab_dispositions_and_policy_compare(tmp_path):
    """
    Replay Lab sends each recorded social event through the same Social Core.
    """
    # Mock cognition keeps the ingestion path fully offline (no LLM, no network)
    async def mock_social_core(messages):
        return social_result(reason="offline replay", summary="understood replay event")

    runtime = AgentRuntime(
        RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "replay.db")),
        mock_social_handler=mock_social_core
    )
    await runtime.start()
    app = create_app(runtime)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    login = await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})
    assert "session_token" in login.cookies
    headers = {}
    try:
        scene_id = "group:replay"
        t0 = time.time()
        corpus = [
            ("user:A", "今天中午吃啥", False),
            ("user:B", "火锅吧", False),
            ("user:A", "@Bot 你呢", True),
            ("user:B", "今晚Major决赛看吗", False),
        ]
        for i, (actor, text, at_bot) in enumerate(corpus):
            await runtime.receive_event(Event(
                event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=scene_id,
                actor_id=actor, timestamp=t0 + i * 5,
                payload={"raw_text": text, "at_bot": at_bot}
            ))
        await asyncio.sleep(0.3)

        res = await client.post("/api/replay", headers=headers, json={
            "scene_id": scene_id,
            "since": t0 - 1,
            "until": t0 + 100,
        })
        assert res.status_code == 200
        data = res.json()
        assert data["event_count"] >= 4
        assert len(data["runs"]) == 1
        rows = data["runs"][0]["rows"]
        # Bursts, not individual messages, are the evaluation unit.
        sources = {source for row in rows for source in row["trace"]["burst"]["source_event_ids"]}
        assert len(sources) == 4
        assert all(row["decision"] == "silence" for row in rows)
        assert all(row["understanding"] == "understood replay event" for row in rows)
    finally:
        await client.aclose()
        await runtime.stop()


@pytest.mark.asyncio
async def test_shadow_mode_records_without_sending(tmp_path):
    """
    ADR-0023 Shadow Mode: enabled → zero adapter calls, zero MESSAGE_SENT events,
    no OpenLoop activation; would-send log records what WOULD have been sent.
    Disabled → normal sending resumes.
    """
    config = RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "shadow.db"))
    sent_actions = []

    async def mock_send(item):
        sent_actions.append(item)
        return DeliveryResult(status=DeliveryStatus.SENT, transport="test")

    async def mock_social_core(messages):
        stimulus_text = messages[-1]["content"].split("【CURRENT BURST】")[-1]
        if "你好" in stimulus_text:
            return social_result(
                reason="greeting", content="你好呀", expect_reply=True, reply_target="user:A"
            )
        return social_result(reason="silence")

    runtime = AgentRuntime(config, send_adapter=mock_send, mock_social_handler=mock_social_core)
    await runtime.start()
    await runtime.set_shadow_mode(True)

    scene_id = "group:shadow"
    await runtime.receive_event(Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=scene_id,
        actor_id="user:A", timestamp=time.time(),
        payload={"raw_text": "@Bot 你好", "at_bot": True}
    ))
    await asyncio.sleep(0.4)

    # NOTHING physically sent — cognition happened, visible speech did not
    assert len(sent_actions) == 0
    assert runtime.metrics.social["would_send"] == 1
    assert len(runtime.shadow_would_send_log) == 1
    assert runtime.shadow_would_send_log[0]["content"] == "你好呀"

    # No MESSAGE_SENT social fact → open loop never activated
    events = await runtime.event_store.get_recent_events(scene_id, limit=10)
    assert all(e.event_type != EventType.MESSAGE_SENT for e in events)
    loops = await runtime.event_store.get_active_open_loops(scene_id)
    assert loops == []

    # Toggle off via the authority method → sending resumes
    await runtime.set_shadow_mode(False)
    await runtime.receive_event(Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:shadow2",
        actor_id="user:A", timestamp=time.time(),
        payload={"raw_text": "@Bot 你好", "at_bot": True}
    ))
    await asyncio.sleep(0.4)
    assert len(sent_actions) == 1

    await runtime.stop()


@pytest.mark.asyncio
async def test_shadow_toggle_persists_and_api_reports(tmp_path):
    runtime, client, headers = await _make_runtime_and_client(
        RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "shadow_api.db"))
    )
    try:
        toggle = await client.post("/api/cockpit/shadow/toggle", headers=headers, json={"enabled": True})
        assert toggle.status_code == 200
        assert toggle.json()["shadow_mode"] is True

        status = (await client.get("/api/cockpit/shadow", headers=headers)).json()
        assert status["enabled"] is True
        assert status["would_send"] == []

        # Restart persistence
        runtime2 = AgentRuntime(RuntimeConfig(bot_qq=12345678, db_path=runtime.config.db_path))
        await runtime2.start()
        assert runtime2.shadow_mode is True
        await runtime2.stop()
    finally:
        await client.aclose()
        await runtime.stop()


@pytest.mark.asyncio
async def test_cors_no_wildcard_with_credentials(tmp_path):
    """§三十九: create_app must not combine allow_origins=['*'] with credentials."""
    runtime, client, headers = await _make_runtime_and_client(
        RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "cors.db"))
    )
    try:
        import inspect
        from len_bot.web.app import create_app as _ca
        src = inspect.getsource(_ca)
        assert 'allow_origins=["*"]' not in src
    finally:
        await client.aclose()
        await runtime.stop()
