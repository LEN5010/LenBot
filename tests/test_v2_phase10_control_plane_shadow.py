"""Authenticated trace, knowledge history and Shadow controls on the VNext runtime."""

from httpx import ASGITransport, AsyncClient
import pytest

from delivery_support import allow_fake_delivery
from len_bot.actions.models import DeliveryResult, DeliveryStatus
from len_bot.cognition.models import EpisodeOutcome
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.memory.models import MemoryProposal
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.testing.replay import drain
from len_bot.testing.turns import turn_result
from len_bot.web.app import create_app


async def _make_runtime_and_client(config, *, mock_turn_handler=None, send_adapter=None):
    runtime = AgentRuntime(config, mock_turn_handler=mock_turn_handler, send_adapter=send_adapter)
    await runtime.start()
    client = AsyncClient(transport=ASGITransport(app=create_app(runtime)), base_url="http://test")
    login = await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})
    assert "session_token" in login.cookies
    return runtime, client


@pytest.mark.asyncio
async def test_trace_captures_full_causal_chain(tmp_path):
    sent_actions = []

    async def send(action):
        sent_actions.append(action)
        return DeliveryResult(status=DeliveryStatus.SENT, transport="isolated-test")

    async def turn(session, events):
        outcome=turn_result(reason="User asked directly", content="看了一下，没问题")
        outcome.message_proposals[0].reply_to="protocol-original"
        return outcome

    runtime, client = await _make_runtime_and_client(
        RuntimeConfig(db_path=str(tmp_path / "trace.db")), mock_turn_handler=turn, send_adapter=send,
    )
    try:
        scene_id = "group:trace"
        await allow_fake_delivery(runtime, scene_id)
        incoming = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=scene_id,
                         actor_id="user:A", payload={"raw_text": "@Bot 帮我看看这个", "at_bot": True,"message_id":"protocol-original"})
        await runtime.receive_event(incoming)
        await drain(runtime)
        response = await client.get("/api/cockpit/traces", params={"scene_id": scene_id, "kind": "conversation"})
        assert response.status_code == 200
        rows = response.json()["items"]
        assert response.json()["total"] == len(rows) == 1 and "payload" not in rows[0]
        detail=await client.get(f"/api/cockpit/traces/{rows[0]['id']}",params={"scene_id":scene_id})
        payload = detail.json()["payload"]
        assert payload["result"]["disposition"] == "ACTION"
        assert payload["gate"]["accepted"] and payload["gate"]["disposition"] == "ACTION"
        assert payload["actions_enqueued"] == len(sent_actions) == 1
        assert incoming.id in payload["conversation"]["source_event_ids"]
        assert payload["gate"]["action_ids"] == [sent_actions[0].id]
        events = await runtime.event_store.get_recent_events(scene_id)
        delivery = next(event for event in events if event.event_type == EventType.MESSAGE_SENT)
        assert delivery.payload["action_id"] == sent_actions[0].id
        await runtime.event_store.append_event(Event(id="unrelated-nearby",event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id=scene_id,actor_id="user:B",timestamp=incoming.timestamp,payload={"raw_text":"附近但无引用关系"}))
        await runtime.event_store.append_event(Event(id="foreign-batch",event_type=EventType.MESSAGE_SENT,
            scene_id="group:other",actor_id=runtime.bot_actor_id,payload={"batch_id":rows[0]["ref_id"],"delivery_status":"sent"}))
        linked=(await client.get('/api/cockpit/relations',params={"scene_id":scene_id,"event_id":incoming.id})).json()
        assert {incoming.id,delivery.id} <= {item["id"] for item in linked["events"]}
        assert not {"unrelated-nearby","foreign-batch"} & {item["id"] for item in linked["events"]}
        assert linked["actions"][0]["receipt_event_ids"] == [delivery.id]
        assert linked["actions"][0]["delivery_status"] == "sent"
        projected=next(item for item in linked["events"] if item["id"]==delivery.id)
        assert projected["quote"]["event_id"]==incoming.id and projected["quote"]["text"]==incoming.raw_text
        assert (await client.get('/api/cockpit/relations',params={"scene_id":"group:other","event_id":incoming.id})).status_code==404
        await runtime.event_store.save_trace(kind="conversation",scene_id=scene_id,ref_id="pending-episode",payload={
            "gate":{"accepted":True,"action_ids":["pending-only"]},"continuation":{"thought_signature":"private-continuation"}})
        pending=(await client.get('/api/cockpit/relations',params={"scene_id":scene_id,"action_id":"pending-only"})).json()
        assert pending["actions"][0]["id"]=="pending-only" and pending["actions"][0]["delivery_status"] is None
        assert pending["actions"][0]["receipt_event_ids"]==[]
        trace_detail=await client.get('/api/cockpit/traces/'+pending["traces"][0]["id"],params={"scene_id":scene_id})
        assert "private-continuation" not in trace_detail.text and "continuation" not in trace_detail.json()["payload"]


    finally:
        await client.aclose()
        await runtime.stop()


@pytest.mark.asyncio
async def test_memory_chain_and_filtered_events_via_query_service(tmp_path):
    runtime, client = await _make_runtime_and_client(RuntimeConfig(db_path=str(tmp_path / "chain.db")))
    try:
        scene_id = "group:chain"
        original = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=scene_id,
                         actor_id="user:A", payload={"raw_text": "我吃微辣"})
        await runtime.receive_event(original)
        await drain(runtime)
        first = await runtime.operator_outcome(scene_id, EpisodeOutcome(
            decision_reason="原话支持的认识",
            memory_proposals=[MemoryProposal(subject="user:A", kind="preference", statement="A说自己吃微辣",
                                             basis="reported", evidence=[original.id])],
        ))
        assert first.accepted
        old = first.committed_proposal.committed_memories[0]
        correction = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=scene_id,
                           actor_id="user:A", payload={"raw_text": "我现在完全不吃辣了"})
        await runtime.receive_event(correction)
        await drain(runtime)
        second = await runtime.operator_outcome(scene_id, EpisodeOutcome(
            decision_reason="后续原话更正",
            memory_proposals=[MemoryProposal(operation="supersede", target_memory_ids=[old.id],
                reason="A明确更正当前偏好", subject="user:A", kind="preference", statement="A说自己现在完全不吃辣",
                basis="reported", evidence=[correction.id])],
        ))
        assert second.accepted
        current = second.committed_proposal.committed_memories[0]
        response = await client.get(f"/api/cockpit/memories/{current.id}/chain")
        assert response.status_code == 200
        chain = response.json()["chain"]
        assert [item["id"] for item in chain] == [old.id, current.id]
        assert [item["status"] for item in chain] == ["superseded", "active"]
        assert chain[0]["statement"] == old.statement and chain[0]["evidence"] == [original.id]
        assert chain[1]["statement"] == current.statement and chain[1]["evidence"] == [correction.id]

        marker = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=scene_id,
                       actor_id="user:B", payload={"raw_text": "标记消息"})
        await runtime.receive_event(marker)
        await drain(runtime)
        events = (await runtime.query_service.query_events(scene_id=scene_id, actor_id="user:B"))["items"]
        assert len(events) == 1 and events[0]["id"] == marker.id
        assert events[0]["actor_id"] == "user:B"
    finally:
        await client.aclose()
        await runtime.stop()


@pytest.mark.asyncio
async def test_shadow_mode_records_without_sending(tmp_path):
    sent_actions = []

    async def send(action):
        sent_actions.append(action)
        return DeliveryResult(status=DeliveryStatus.SENT, transport="isolated-test")

    async def turn(session, events):
        return turn_result(reason="greeting", content="你好呀", expect_reply=True, reply_target="user:A")

    runtime, client = await _make_runtime_and_client(
        RuntimeConfig(db_path=str(tmp_path / "shadow.db")), mock_turn_handler=turn, send_adapter=send,
    )
    try:
        scene_id = "group:shadow"
        await allow_fake_delivery(runtime, scene_id)
        await runtime.set_shadow_mode(True)
        await runtime.receive_event(Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=scene_id,
            actor_id="user:A", payload={"raw_text": "@Bot 你好", "at_bot": True}))
        await drain(runtime)
        assert not sent_actions
        assert runtime.metrics.social["would_send"] == 1
        assert len(runtime.shadow_would_send_log) == 1
        assert runtime.shadow_would_send_log[0]["content"] == "你好呀"
        events = await runtime.event_store.get_recent_events(scene_id)
        assert all(event.event_type != EventType.MESSAGE_SENT for event in events)
        assert await runtime.event_store.get_active_open_loops(scene_id) == []

        await runtime.set_shadow_mode(False)
        await runtime.receive_event(Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=scene_id,
            actor_id="user:A", payload={"raw_text": "@Bot 你好", "at_bot": True}))
        await drain(runtime)
        assert len(sent_actions) == 1
        loops = await runtime.event_store.get_active_open_loops(scene_id)
        assert len(loops) == 1 and loops[0]["source_event_id"]
    finally:
        await client.aclose()
        await runtime.stop()


@pytest.mark.asyncio
async def test_shadow_toggle_persists_and_api_reports(tmp_path):
    runtime, client = await _make_runtime_and_client(RuntimeConfig(db_path=str(tmp_path / "shadow-api.db")))
    try:
        toggle = await client.post("/api/cockpit/shadow/toggle", json={"enabled": True})
        assert toggle.status_code == 200 and toggle.json()["shadow_mode"] is True
        response = await client.get("/api/cockpit/shadow")
        assert response.status_code == 200
        assert response.json()["enabled"] is True and response.json()["would_send"] == []
        runtime2 = AgentRuntime(RuntimeConfig(db_path=runtime.config.db_path))
        await runtime2.start()
        try:
            assert runtime2.shadow_mode is True
        finally:
            await runtime2.stop()
    finally:
        await client.aclose()
        await runtime.stop()


@pytest.mark.asyncio
async def test_cors_no_wildcard_with_credentials(tmp_path):
    runtime, client = await _make_runtime_and_client(RuntimeConfig(db_path=str(tmp_path / "cors.db")))
    try:
        response = await client.get("/api/overview/stats", headers={"Origin": "https://untrusted.example"})
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-credentials" not in response.headers
    finally:
        await client.aclose()
        await runtime.stop()
