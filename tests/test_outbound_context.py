"""Approved output is visible without inventing delivery or consuming input."""

import asyncio
import json

import httpx
import pytest
import pytest_asyncio
from openai import AsyncOpenAI

from len_bot.actions.models import DeliveryResult, DeliveryStatus
from len_bot.cognition.context import ConversationContext
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.scenes.models import SceneSession
from runtime_support import configure_fixture_profile


SCENE, OTHER_SCENE, BOT = "group:4242", "group:8181", "user:99"


def terminal(text=None):
    messages = [{"segments": [{"text": text}]}] if text else []
    return {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [
        {"id": "finish", "type": "function", "function": {"name": "finish_turn", "arguments": json.dumps({"messages": messages})}},
    ]}}]}


def facts_from_messages(messages):
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and content.startswith("运行事实"):
            return json.loads(content.rsplit("\n", 1)[-1])
    return {}


@pytest.mark.asyncio
async def test_blocked_send_is_visible_to_next_turn_and_late_receipt_stays_out_of_input(tmp_path):
    send_started, release_send, second_model = asyncio.Event(), asyncio.Event(), asyncio.Event()
    sent, model_views = [], []

    async def send(action):
        sent.append(action)
        send_started.set()
        await release_send.wait()
        return DeliveryResult(status=DeliveryStatus.SENT, transport="isolated", message_id="delivered")

    async def respond(request):
        payload = json.loads(request.content)
        model_views.append(facts_from_messages(payload["messages"]))
        if len(model_views) == 1:
            return httpx.Response(200, json=terminal("啊啊啊禁止复读！"))
        second_model.set()
        return httpx.Response(200, json=terminal())

    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "pending.db"), bot_qq=99,
        debounce_idle_ms=1, debounce_max_ms=2, message_pacing=False, history_quiet_window_seconds=3600),
        send_adapter=send, clock=lambda: 1000)
    await runtime.start()
    await configure_fixture_profile(runtime)
    await runtime.set_delivery_scenes([SCENE])
    await runtime.set_shadow_mode(False)
    client = AsyncOpenAI(api_key="fixture", base_url="https://fixture.invalid/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    runtime.provider_registry._clients["fixture"] = client
    try:
        await runtime.receive_event(Event(id="repeat", event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=SCENE,
            actor_id="user:1", timestamp=1000, payload={"raw_text": "在丧尸末日也要精致", "at_bot": True}))
        await asyncio.wait_for(send_started.wait(), 3)
        await runtime.receive_event(Event(id="new-topic", event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=SCENE,
            actor_id="user:2", timestamp=1000, payload={"raw_text": "睡醒了", "at_bot": True}))
        await asyncio.wait_for(second_model.wait(), 3)
        await asyncio.gather(*list(runtime._conversation_tasks.values()))
        pending = model_views[1]["outbound"]
        assert len(pending) == 1 and pending[0]["status"] == "pending"
        assert pending[0]["segments"][0]["text"] == "啊啊啊禁止复读！"
        assert pending[0]["receipt_event_id"] is None
        assert not [event for event in await runtime.event_store.get_recent_events(SCENE) if event.event_type == EventType.MESSAGE_SENT]
        assert len(sent) == 1

        # Take the turn snapshot before delivery, then build its facts after
        # delivery: this reproduces the load/build race without moving cutoff.
        session = runtime.scene_manager.get_session(SCENE).model_copy(deep=True)
        cutoff = session.last_observed_event_rowid
        context = ConversationContext(runtime, session, cutoff)
        release_send.set()
        await asyncio.wait_for(runtime.action_queue._queue.join(), 3)
        await runtime.scene_manager._actors[SCENE]._queue.join()
        delivered = next(event for event in await runtime.event_store.get_recent_events(SCENE) if event.event_type == EventType.MESSAGE_SENT)
        message = await context.facts_message()
        refreshed = facts_from_messages([message])["outbound"]
        assert message["role"] == "user"
        assert refreshed[0]["status"] == "sent" and refreshed[0]["receipt_after_cutoff"]
        assert refreshed[0]["receipt_event_id"] == delivered.id
        assert refreshed[0]["body_source"] == "receipt"
        assert context.refs.cutoff == cutoff and delivered.metadata["_rowid"] > cutoff
        assert delivered.id not in context.refs.read_events and delivered.id not in context.refs.events.values()
        assert not runtime.scene_manager.get_session(SCENE).pending_wakes
        assert await runtime.event_store.outbound_message_facts(SCENE, delivered.metadata["_rowid"], bot_actor_id=BOT) == []
        context.refs.cutoff=delivered.metadata['_rowid']
        cleared=await context.facts_message()
        assert facts_from_messages([cleared])=={'outbound':[]}
        assert await context.facts_message() is None
    finally:
        release_send.set()
        await runtime.stop()
        await client.close()


@pytest_asyncio.fixture
async def store(tmp_path):
    value = EventStore(str(tmp_path / "facts.db"), clock=lambda: 1000)
    await value.initialize()
    yield value
    await value.close()


async def approval(store, batch="batch", *, scene=SCENE, count=1):
    event = Event(id="turn:"+batch, event_type=EventType.CONVERSATION_COMMITTED, scene_id=scene,
        actor_id="system:conversation", timestamp=900, metadata={"mode": "live"},
        payload={"outcome": {"message_proposals": [{"segments": [{"type": "text", "text": f"获准表达{index}"}]} for index in range(count)]}})
    await store.append_event(event)
    return event


async def receipt(store, *, scene=SCENE, actor=BOT, kind=EventType.MESSAGE_SEND_FAILED,
                  status="unknown", batch="batch", index=0, simulated=False):
    event = Event(event_type=kind, scene_id=scene, actor_id=actor, timestamp=1000, metadata={"simulated": simulated},
        payload={"batch_id": batch, "batch_index": index, "delivery_status": status,
                 "segments": [{"type": "text", "text": "实际传输内容"}]})
    await store.append_event(event)
    return event


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "status", "simulated", "expected"), [
    (EventType.MESSAGE_SEND_FAILED, "unknown", False, "unknown"),
    (EventType.MESSAGE_SEND_FAILED, "not_sent", False, "not_sent"),
    (EventType.MESSAGE_SEND_FAILED, "rejected", False, "rejected"),
    (EventType.ACTION_SHADOWED, None, False, "shadow"),
    (EventType.MESSAGE_SENT, "sent", True, "simulated_sent"),
])
async def test_status_facts_preserve_unknown_failed_shadow_and_simulated_receipts(store, kind, status, simulated, expected):
    await approval(store)
    event = await receipt(store, kind=kind, status=status, simulated=simulated)
    facts = await store.outbound_message_facts(SCENE, 1, bot_actor_id=BOT)
    assert len(facts) == 1 and facts[0]["status"] == expected
    assert facts[0]["receipt_event_id"] == event.id and facts[0]["receipt_after_cutoff"]
    if kind != EventType.MESSAGE_SENT:
        assert (await store.outbound_message_facts(SCENE, 100, bot_actor_id=BOT))[0]["status"] == expected


@pytest.mark.asyncio
async def test_receipts_and_approvals_cannot_cross_scene_or_bot_identity(store):
    await approval(store)
    await receipt(store, scene=OTHER_SCENE, kind=EventType.MESSAGE_SENT, status="sent")
    await receipt(store, actor="user:other-bot", kind=EventType.MESSAGE_SENT, status="sent")
    facts = await store.outbound_message_facts(SCENE, 100, bot_actor_id=BOT)
    assert len(facts) == 1 and facts[0]["status"] == "pending"
    assert facts[0]["receipt_event_id"] is None
    assert await store.outbound_message_facts(OTHER_SCENE, 100, bot_actor_id=BOT) == []


@pytest.mark.asyncio
async def test_one_delivered_part_does_not_hide_other_approved_parts(store):
    await approval(store, count=2)
    await receipt(store, kind=EventType.MESSAGE_SENT, status="sent", index=0)
    facts = await store.outbound_message_facts(SCENE, 100, bot_actor_id=BOT)
    assert len(facts) == 1 and facts[0]["batch_index"] == 1 and facts[0]["status"] == "pending"


def test_mentions_use_the_same_actor_handles_in_raw_and_quoted_messages():
    from types import SimpleNamespace
    context = ConversationContext(SimpleNamespace(bot_actor_id=BOT, config=RuntimeConfig()), SceneSession(scene_id=SCENE), 10)
    event = Event(id="message", event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=SCENE, actor_id="user:1",
        payload={"raw_text": "[CQ:at,qq=99] 看看 [CQ:at,qq=22] [CQ:at,qq=all]"},
        metadata={"_rowid": 2, "quote_context": {"event_id": "quoted", "rowid": 1, "actor_id": "user:22",
                                                  "text": "[CQ:at,qq=99] [CQ:at,qq=1]"}})
    text = context.event_message(event)["content"]
    assert "[提及 BOT]" in text and "[提及 U2]" in text and "[提及 U1]" in text
    assert "[提及全体成员]" in text
    assert "提及 QQ" not in text
    assert context.refs.actor_id("U2") == "user:22"


def test_call_signals_keep_private_and_read_reply_context_without_matching_quoted_names():
    from types import SimpleNamespace
    runtime=SimpleNamespace(bot_actor_id=BOT,config=RuntimeConfig(identity_name='嘉然'))
    context=ConversationContext(runtime,SceneSession(scene_id=SCENE),10)
    reply=Event(id='reply',event_type=EventType.GROUP_MESSAGE_RECEIVED,scene_id=SCENE,actor_id='user:1',
        payload={'raw_text':'你接着说'},metadata={'_rowid':3,'quote_context':{
            'event_id':'bot-message','rowid':2,'actor_id':BOT,'text':'小然、然比都是角色称呼'}})
    context.event_message(reply)
    context.input_message([reply])
    assert context.call_signals=={'reply':{'reply_to_bot':True}}
    assert context.refs.cutoff==10

    private=ConversationContext(runtime,SceneSession(scene_id='private:1'),1)
    message=Event(id='direct',event_type=EventType.PRIVATE_MESSAGE_RECEIVED,scene_id='private:1',actor_id='user:1',
        payload={'raw_text':'你好'},metadata={'_rowid':1})
    private.event_message(message)
    private.input_message([message])
    assert private.call_signals=={'direct':{'direct_message':True}}
