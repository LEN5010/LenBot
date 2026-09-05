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
from len_bot.cognition.session import WorkingPersonUpdate
from len_bot.runtime.gate import RuntimeGate
from len_bot.testing.social import social_result


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


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
async def test_actor_cutoff_keeps_new_facts_but_tasks_require_freshness(tmp_path, strict):
    store = EventStore(str(tmp_path / "cutoff.db"))
    await store.initialize()
    actor = SceneActor("group:1", "user:123", store)
    await actor.start()
    try:
        first = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:1", actor_id="user:A",
                      payload={"raw_text": "你好", "sender": {"nickname": "旧昵称"}})
        actor.post_event(first)
        await actor._queue.join()
        cutoff, revision = actor.group_session.last_observed_event_rowid, actor.group_session.social_revision
        mailbox = EpisodeMailbox("test", "group:1", actor.state.version)
        assert actor.acquire_episode_lease("test", mailbox)
        later = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:1", actor_id="user:A",
                      payload={"raw_text": "又一条", "sender": {"nickname": "新昵称"}})
        actor.post_event(later)
        await actor._queue.join()
        result = social_result(reason="接话", content="来了", expect_reply=strict, reply_target="user:A")
        result.perception.person_updates = [WorkingPersonUpdate(actor_id="user:A", recent_context_add=["刚问好"], source_event_ids=[first.id])]
        # Validate fixture exactly as model output is validated.
        result = type(result).model_validate(result.model_dump())
        gate = RuntimeGate(store, ActionQueue(store))
        decision = await actor.commit_cognitive_turn(result, cutoff, [first.id], "live", "test", mailbox, gate,
                                                   social_revision=revision)
        if strict:
            assert decision is False
            assert actor.group_session.last_cognized_event_rowid == 0
        else:
            assert decision.accepted
            assert actor.group_session.last_cognized_event_rowid == cutoff
            person = actor.group_session.working_persons["user:A"]
            assert person.display_name == "新昵称" and later.id in person.recent_event_ids
            assert mailbox.has_unseen_interim()
        assert actor.group_session.last_observed_event_rowid > cutoff
    finally:
        await actor.stop()
        await store.close()


@pytest.mark.asyncio
async def test_diana_application_is_atomic_idempotent_and_preserves_edits(tmp_path):
    store = EventStore(str(tmp_path / "persona.db"))
    await store.initialize()
    try:
        assert await store.apply_diana_persona(3684366985)
        config = await store.get_dynamic_config("persona_config")
        assert config["identity_name"] == "嘉然" and config["bot_qq"] == 3684366985
        assert len(await store.list_voice_examples()) == 16
        config["identity_core"] = "人工改过"
        await store.save_dynamic_config("persona_config", config)
        assert not await store.apply_diana_persona(3684366985)
        assert (await store.get_dynamic_config("persona_config"))["identity_core"] == "人工改过"
        assert len(await store.list_voice_examples()) == 16
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_core_final_followup_is_bounded_under_continuous_input():
    from len_bot.cognition.social_core import SocialCognitionCore
    from len_bot.cognition.session import GroupAgentSession
    from test_v4_stage4_agentic_memory import _burst, _Registry, _Completions, _final_response
    completions = _Completions([_final_response("原决定"), _final_response("最后一次续接")])
    core = SocialCognitionCore(RuntimeConfig(), _Registry(completions))
    observed = 0
    async def observe():
        nonlocal observed
        observed += 1
        return f"新增消息 {observed}"
    result, trace = await core.execute(GroupAgentSession(scene_id="group:memory"), _burst(), [], [], observe=observe)
    assert len(completions.calls) == 2
    assert trace["interim_batches"] == 2
    assert observed == 2  # Events arriving during the last call are for the next turn.
    assert result.decision.reason == "最后一次续接"


@pytest.mark.asyncio
async def test_persona_failure_rolls_back_config_and_examples(tmp_path):
    store = EventStore(str(tmp_path / "rollback.db"))
    await store.initialize()
    try:
        await store.save_dynamic_config("persona_config", {"identity_name": "原名字"})
        await store._db.execute("CREATE TRIGGER reject_voice BEFORE INSERT ON voice_exemplars BEGIN SELECT RAISE(ABORT, 'test'); END")
        await store._db.commit()
        with pytest.raises(Exception, match="test"):
            await store.apply_diana_persona(123)
        assert (await store.get_dynamic_config("persona_config"))["identity_name"] == "原名字"
        assert not await store.list_voice_examples()
        assert await store.get_dynamic_config("diana-v1") is None
    finally:
        await store.close()
