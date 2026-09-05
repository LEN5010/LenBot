"""ADR-0042: real commit boundaries, scripted cognition, no network or QQ sends."""
import json
import asyncio
from contextlib import asynccontextmanager

import pytest

from len_bot.actions.queue import ActionQueue
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.session import (
    GroupAgentSession, GroupAgentSessionReducer, RelationshipUpdate, SelfSocialStateUpdate,
    SocialCognitionResult, SocialMemoryCandidate, SocialWorldPatch, WorkingPersonUpdate,
)
from len_bot.cognition.social_core import SocialCognitionCore
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.memory.models import MemoryProposal
from len_bot.memory.store import MemoryStore
from len_bot.runtime.gate import RuntimeGate
from len_bot.scenes.actor import SceneActor
from len_bot.testing.social import social_result


@asynccontextmanager
async def scene(tmp_path):
    store = EventStore(str(tmp_path / "revisions.db"))
    await store.initialize()
    memory = MemoryStore(store._db, store._write_lock)
    await memory.initialize()
    actor = SceneActor("group:1", "user:123", store)
    await actor.start()
    queue = ActionQueue(store)
    try:
        yield store, memory, actor, queue
    finally:
        await actor.stop()
        await store.close()


async def incoming(actor, text="我是雨月云，不是火草", user="user:A", **payload):
    event = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=actor.scene_id,
                  actor_id=user, payload={"raw_text": text, **payload})
    actor.post_event(event)
    await actor._queue.join()
    return event


async def commit(actor, store, queue, result, evidence):
    mailbox = EpisodeMailbox("revision", actor.scene_id, actor.state.version)
    assert actor.acquire_episode_lease(mailbox.episode_id, mailbox)
    try:
        return await actor.commit_cognitive_turn(
            SocialCognitionResult.model_validate(result.model_dump()),
            actor.group_session.last_observed_event_rowid, evidence, "live", mailbox.episode_id,
            mailbox, RuntimeGate(store, queue), social_revision=actor.group_session.social_revision)
    finally:
        actor.release_episode_lease(mailbox.episode_id)


def belief(event, key="nickname", value="雨月云", **kwargs):
    return SocialMemoryCandidate(subject="user:A", kind="fact", key=key,
                                 value=value, evidence=[event.id], **kwargs)


@pytest.mark.asyncio
async def test_preferred_address_survives_metadata_refresh_and_restart(tmp_path):
    async with scene(tmp_path) as (store, memory, actor, queue):
        first = await incoming(actor, sender={"nickname": "雨月云", "card": "火草"})
        result = social_result(reason="尊重明确称呼")
        result.perception.person_updates = [WorkingPersonUpdate(
            actor_id="user:A", preferred_name="雨月云", source_event_ids=[first.id])]
        assert (await commit(actor, store, queue, result, [first.id])).accepted
        later = await incoming(actor, "下一句", sender={"nickname": "账号新昵称", "card": "新群名片"})
        await incoming(actor, "同名但另一个人", user="user:B", sender={"nickname": "雨月云", "card": "火草"})
        await actor.stop()
        await actor.start()
        person = actor.group_session.working_persons["user:A"]
        assert (person.nickname, person.card, person.preferred_name) == ("账号新昵称", "新群名片", "雨月云")
        assert later.id in person.recent_event_ids
        assert actor.group_session.working_persons["user:B"].preferred_name is None


@pytest.mark.asyncio
async def test_memory_refute_receipts_and_conflict_prevent_false_confirmation(tmp_path):
    async with scene(tmp_path) as (store, memory, actor, queue):
        first = await incoming(actor)
        draft = social_result(reason="形成认识")
        draft.memory_candidates = [belief(first, value="猜测称呼")]
        assert (await commit(actor, store, queue, draft, [first.id])).accepted
        mid = actor.group_session.recent_memory_changes[0]["id"]
        assert mid in actor.group_session.working_persons["user:A"].memory_ids
        correction = social_result(reason="纠错", content="刚才认错了，已经改过来了")
        correction.memory_candidates = [SocialMemoryCandidate(
            operation="refute", target_memory_ids=[mid], reason="本人纠正了称呼", evidence=[first.id])]
        correction.perception.person_updates = [WorkingPersonUpdate(
            actor_id="user:A", preferred_name="雨月云", source_event_ids=[first.id])]
        assert (await commit(actor, store, queue, correction, [first.id])).accepted
        assert not await memory.query_memories(allowed_scopes=[actor.scene_id])
        history = await memory.query_memories(allowed_scopes=[actor.scene_id], include_superseded=True)
        assert history[0].status.value == "refuted"
        assert history[0].revision_evidence == [first.id]
        assert actor.group_session.working_persons["user:A"].memory_ids == []
        assert actor.group_session.recent_memory_changes[-1]["status"] == "refuted"
        count = queue._queue.qsize()
        revision = actor.group_session.social_revision
        assert not (await commit(actor, store, queue, correction, [first.id])).accepted
        assert queue._queue.qsize() == count
        assert actor.group_session.social_revision == revision


@pytest.mark.asyncio
async def test_supersede_merges_keys_and_transaction_rollback_keeps_old_beliefs(tmp_path):
    async with scene(tmp_path) as (store, memory, actor, queue):
        event = await incoming(actor)
        draft = social_result(reason="两个重复槽位")
        draft.memory_candidates = [belief(event, "a", "曾自称雨月云"), belief(event, "b", "群名片是火草")]
        assert (await commit(actor, store, queue, draft, [event.id])).accepted
        old = await memory.query_memories(allowed_scopes=[actor.scene_id])
        mids = [m.id for m in old]
        revision = social_result(reason="合并", content="以后叫你雨月云")
        revision.memory_candidates = [belief(event, "preferred_address", "希望称呼雨月云", operation="supersede",
                                              target_memory_ids=mids, reason="区分群名片和本人偏好")]
        await store._db.execute("CREATE TRIGGER reject_session BEFORE UPDATE ON group_agent_sessions BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        await store._db.commit()
        assert not (await commit(actor, store, queue, revision, [event.id])).accepted
        assert {m.id for m in await memory.query_memories(allowed_scopes=[actor.scene_id])} == set(mids)
        assert queue._queue.empty()
        await store._db.execute("DROP TRIGGER reject_session")
        await store._db.commit()
        assert (await commit(actor, store, queue, revision, [event.id])).accepted
        active = await memory.query_memories(allowed_scopes=[actor.scene_id])
        assert len(active) == 1 and active[0].key == "preferred_address"
        historical = await memory.query_memories(allowed_scopes=[actor.scene_id], include_superseded=True)
        assert all(m.superseded_by == active[0].id for m in historical if m.id in mids)
        assert actor.group_session.working_persons["user:A"].memory_ids == [active[0].id]
        from types import SimpleNamespace
        from len_bot.web.query_service import RuntimeQueryService
        service = RuntimeQueryService(SimpleNamespace(memory_store=memory))
        chain = await service.memory_chain(active[0].id)
        assert {m["id"] for m in chain} == {*mids, active[0].id}
        assert all(m["revision_reason"] for m in chain if m["id"] in mids)


@pytest.mark.asyncio
async def test_memory_revision_cannot_cross_scene_or_use_bot_guess_as_fact(tmp_path):
    async with scene(tmp_path) as (store, memory, actor, queue):
        original = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:other",
                         actor_id="user:A", payload={"raw_text": "私密事实"})
        await store.append_event(original)
        _, _, saved = await store.commit_proposal_transaction("seed", "group:other", [], [], [
            MemoryProposal(**belief(original).model_dump())])
        event = await incoming(actor)
        proposal = social_result(reason="错误跨群目标", content="改好了")
        proposal.memory_candidates = [SocialMemoryCandidate(operation="refute", target_memory_ids=[saved[0].id],
                                                           reason="不可跨群", evidence=[event.id])]
        assert not (await commit(actor, store, queue, proposal, [event.id])).accepted
        spoken = Event(event_type=EventType.MESSAGE_SENT, scene_id=actor.scene_id,
                       actor_id="user:123", payload={"content": "他叫咆哮帝"})
        actor.post_event(spoken)
        await actor._queue.join()
        proposal.memory_candidates = [belief(spoken, value="他叫咆哮帝")]
        assert not (await commit(actor, store, queue, proposal, [spoken.id])).accepted
        failed = spoken.model_copy(update={"id": "unsent_guess", "event_type": EventType.MESSAGE_SEND_FAILED})
        actor.post_event(failed)
        await actor._queue.join()
        proposal.memory_candidates = [belief(failed, value="他叫咆哮帝")]
        assert not (await commit(actor, store, queue, proposal, [failed.id])).accepted
        assert queue._queue.empty()


def test_sparse_feedback_relationships_and_world_can_be_retracted():
    state = GroupAgentSession(scene_id="group:1")
    result = social_result(reason="接受反馈")
    result.self_state = SelfSocialStateUpdate(recent_feedback_add=["A希望温柔一些"], source_event_ids=["e1"])
    result.perception.relationship_updates = [RelationshipUpdate(
        actor_id="user:A", patterns_add=["先认真听抱怨，不急着调侃"], source_event_ids=["e1"])]
    result.perception.world_patch = SocialWorldPatch(social_dynamics_add=["刚才互相调侃"],
        group_identity={"norms_add": ["总要互怼"]}, source_event_ids=["e1"])
    state = GroupAgentSessionReducer.apply_cognition(state, result, 1)
    state = GroupAgentSessionReducer.apply_cognition(state, social_result(reason="普通接话"), 2)
    assert state.self_social_state.recent_feedback == ["A希望温柔一些"]
    assert state.working_relationships["user:A"].patterns == ["先认真听抱怨，不急着调侃"]
    update = social_result(reason="纠正旧理解")
    update.perception.world_patch = SocialWorldPatch(social_dynamics_remove=["刚才互相调侃"],
        group_identity={"norms_remove": ["总要互怼"]}, source_event_ids=["e2"])
    update.self_state = SelfSocialStateUpdate(recent_feedback_remove=["A希望温柔一些"], source_event_ids=["e2"])
    state = GroupAgentSessionReducer.apply_cognition(state, update, 3)
    assert state.social_world.social_dynamics == [] and state.group_identity.norms == []
    assert state.self_social_state.recent_feedback == []


@pytest.mark.asyncio
async def test_final_followup_may_retrieve_then_freezes_observation_cutoff():
    from test_v4_stage4_agentic_memory import _burst, _Registry, _Completions, _final_response, _tool_response, _Toolkit
    completions = _Completions([_final_response("旧草稿"), _tool_response(), _final_response("有根据地接话")])
    toolkit = _Toolkit()
    observes = []
    async def observe():
        observes.append(len(observes))
        return None if len(observes) == 1 else "新请求：回忆一下我之前说过什么"
    result, trace = await SocialCognitionCore(RuntimeConfig(), _Registry(completions)).execute(
        GroupAgentSession(scene_id="group:memory"), _burst(), [], [], toolkit=toolkit, observe=observe)
    assert len(toolkit.calls) == 1 and len(observes) == 2
    assert completions.calls[1].get("tool_choice", "auto") == "auto"
    assert trace["final_followups"] == 1 and result.decision.reason == "有根据地接话"


@pytest.mark.asyncio
async def test_insufficient_budget_does_not_consume_late_request():
    from test_v4_stage4_agentic_memory import _burst, _Registry, _Completions, _final_response, _tool_response, _Toolkit
    completions = _Completions([_tool_response(), _final_response("已完成本轮回忆")])
    observed = []
    async def observe():
        observed.append(1)
        return None
    _, trace = await SocialCognitionCore(RuntimeConfig(), _Registry(completions)).execute(
        GroupAgentSession(scene_id="group:memory"), _burst(), [], [], toolkit=_Toolkit(), observe=observe, max_steps=3)
    assert len(observed) == 1  # Two remaining calls cannot adopt another request.
    assert trace.get("final_followups", 0) == 0


@pytest.mark.asyncio
async def test_preset_preview_preserves_manual_edits_and_rejects_stale_preview(tmp_path):
    from len_bot.cognition.diana import LEGACY_PERSONA, LEGACY_EXAMPLES, PERSONA
    async with scene(tmp_path) as (store, memory, actor, queue):
        await store.save_dynamic_config("persona_config", {**LEGACY_PERSONA, "identity_core": "我手写的性格"})
        for i, (context, content) in enumerate(LEGACY_EXAMPLES):
            await store._db.execute("INSERT INTO voice_exemplars(id,content,context,created_at) VALUES(?,?,?,?)",
                                    (f"diana-v1:{i}", content, context, 0))
        await store._db.commit()
        preview = await store.preview_diana_persona()
        await store.update_voice_example("diana-v1:0", scene_id="", content="人工改的样例", context="前文", tag="")
        with pytest.raises(ValueError, match="重新预览"):
            await store.apply_diana_persona(123, preview["preview_token"])
        preview = await store.preview_diana_persona()
        assert await store.apply_diana_persona(123, preview["preview_token"])
        saved = await store.get_dynamic_config("persona_config")
        assert saved["identity_core"] == "我手写的性格"
        assert saved["conversation_style"] == PERSONA["conversation_style"]
        examples = await store.list_voice_examples()
        assert len(examples) == 28
        assert sum(bool(e["enabled"]) for e in examples) == 17
        assert [e for e in examples if e["id"] == "diana-v1:0"][0]["content"] == "人工改的样例"


@pytest.mark.asyncio
async def test_nickname_migration_uses_raw_events_not_display_name_guess(tmp_path):
    async with scene(tmp_path) as (store, memory, actor, queue):
        await incoming(actor, sender={"nickname": "账号昵称", "card": "群名片"})
        await actor.stop()
        saved = await store.load_group_agent_session(actor.scene_id)
        person = saved["working_persons"]["user:A"]
        person.pop("nickname")
        person.pop("card")
        await store._db.execute("UPDATE group_agent_sessions SET state_json=?", (json.dumps(saved),))
        await store._db.execute("DELETE FROM runtime_dynamic_configs WHERE key='person_names_v2'")
        await store._db.commit()
        await store._migrate_person_names()
        await actor.start()
        person = actor.group_session.working_persons["user:A"]
        assert (person.nickname, person.card, person.preferred_name) == ("账号昵称", "群名片", None)


@pytest.mark.asyncio
async def test_production_inflight_followup_reuses_tool_and_keeps_later_message_pending(tmp_path):
    from test_v4_stage4_agentic_memory import _Registry, _final_response, _tool_response
    from len_bot.runtime.agent_runtime import AgentRuntime
    from len_bot.testing.replay import drain
    first_started, release_first = asyncio.Event(), asyncio.Event()
    final_started, release_final = asyncio.Event(), asyncio.Event()
    class Completions:
        calls = 0
        async def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                first_started.set()
                await release_first.wait()
                return _final_response("旧草稿")
            if self.calls == 2:
                return _tool_response()
            if self.calls == 3:
                final_started.set()
                await release_final.wait()
                return _final_response("吸收纠正并回忆后决定")
            return _final_response("下一轮理解后到消息")
    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "inflight.db"), bot_qq=123,
                                       reflection_quiet_window_seconds=999))
    await runtime.start()
    try:
        await runtime.set_shadow_mode(True)
        runtime.provider_registry.has_live_provider = lambda: True  # Scripted provider, no network.
        runtime.social_core = SocialCognitionCore(runtime.config, _Registry(Completions()))
        actor = await runtime.scene_manager.get_or_create_actor("group:memory")
        first = await incoming(actor, "你记得我吗")
        await runtime.burst_assembler.flush_scene(actor.scene_id)
        await asyncio.wait_for(first_started.wait(), 2)
        correction = await incoming(actor, "别猜，回忆一下之前的记录")
        await runtime.burst_assembler.flush_scene(actor.scene_id)
        release_first.set()
        await asyncio.wait_for(final_started.wait(), 2)
        later = await incoming(actor, "刚才那个不用了，换个话题")
        await runtime.burst_assembler.flush_scene(actor.scene_id)
        release_final.set()
        await asyncio.wait_for(drain(runtime), 5)
        traces = [t["payload"] for t in await runtime.event_store.query_traces() if t["kind"] == "social_cognition"]
        assert len(traces) == 2
        first_trace = next(t for t in traces if t["result"]["decision"]["reason"] == "吸收纠正并回忆后决定")
        assert first_trace["cognition"]["final_followups"] == 1
        assert first_trace["cognition"]["deferred_event_count"] >= 1
        assert sum(len(step["tool_calls"]) for t in traces for step in t["cognition"]["steps"]) == 1
        assert actor.group_session.last_cognized_event_rowid == actor.group_session.last_observed_event_rowid
        assert not [e for e in await runtime.event_store.get_recent_events(actor.scene_id)
                    if e.event_type == EventType.MESSAGE_SENT]
    finally:
        release_first.set()
        release_final.set()
        await runtime.stop()


@pytest.mark.asyncio
async def test_old_reflection_cannot_restore_removed_feedback_or_mood(tmp_path):
    async with scene(tmp_path) as (store, memory, actor, queue):
        event = await incoming(actor)
        old_revision = actor.group_session.social_revision
        result = social_result(reason="更新")
        result.perception.world_patch = SocialWorldPatch(mood="认真", source_event_ids=[event.id])
        result.self_state = SelfSocialStateUpdate(recent_feedback_add=["少开玩笑"], source_event_ids=[event.id])
        assert (await commit(actor, store, queue, result, [event.id])).accepted
        actor.post_event(Event(event_type=EventType.REFLECTION_RECORDED, scene_id=actor.scene_id,
            actor_id="system:reflection", payload={"social_revision": old_revision,
                "patch": {"mood": "互相调侃"}, "summary": "旧的理解", "review_items": []}))
        await actor._queue.join()
        assert actor.group_session.social_world.mood == "认真"
        assert actor.group_session.self_social_state.recent_feedback == ["少开玩笑"]
        assert actor.group_session.recent_episode_summary != "旧的理解"


@pytest.mark.asyncio
async def test_three_message_delivery_order_with_partial_unknown_does_not_retry(tmp_path):
    from len_bot.actions.models import DeliveryResult, DeliveryStatus
    async with scene(tmp_path) as (store, memory, actor, queue):
        event = await incoming(actor)
        sent = []
        async def adapter(action):
            sent.append(action.content)
            return DeliveryResult(status=DeliveryStatus.UNKNOWN if action.content == "第二条" else DeliveryStatus.SENT,
                                  transport="test", message_id=None if action.content == "第二条" else str(len(sent)))
        queue.send_adapter = adapter
        queue.on_action_event = lambda e: _deliver(actor, e)
        await queue.start()
        try:
            result = social_result(reason="三段独立含义", content="第一条")
            result.message_proposals.extend([type(result.message_proposals[0])(content=text) for text in ("第二条", "第三条")])
            assert (await commit(actor, store, queue, result, [event.id])).accepted
            await queue._queue.join()
            await actor._queue.join()
            assert sent == ["第一条", "第二条"]
            events = await store.get_recent_events(actor.scene_id)
            deliveries = [e for e in events if e.event_type in {EventType.MESSAGE_SENT, EventType.MESSAGE_SEND_FAILED}]
            assert [e.payload["delivery_status"] for e in deliveries] == ["sent", "unknown", "not_sent"]
        finally:
            await queue.stop()


async def _deliver(actor, event):
    actor.post_event(event)
    await actor._queue.join()


@pytest.mark.asyncio
async def test_reviewed_correction_script_is_idempotent_and_keeps_unread_cursor(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("corrections", Path(__file__).resolve().parents[1] / "scripts/apply_reviewed_corrections.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    async with scene(tmp_path) as (store, memory, actor, queue):
        event = await incoming(actor)
        seed = social_result(reason="旧认识")
        seed.memory_candidates = [belief(event, value="旧猜测")]
        await commit(actor, store, queue, seed, [event.id])
        mid = actor.group_session.recent_memory_changes[0]["id"]
        cutoff = actor.group_session.last_cognized_event_rowid
        await incoming(actor, "没处理完的新请求")
        correction = social_result(reason="运营核对")
        correction.memory_candidates = [SocialMemoryCandidate(operation="refute", target_memory_ids=[mid],
                                                               reason="原始证据不足", evidence=[event.id])]
        path = tmp_path / "corrections.json"
        path.write_text(json.dumps({"id": "operator_revision:test", "scene_id": actor.scene_id,
            "bot_actor_id": "user:123", "expected_memories": {mid: "旧猜测"}, "result": correction.model_dump()}))
    database = tmp_path / "revisions.db"
    assert not await script.run(database, path)
    assert await script.run(database, path, apply=True)
    assert not await script.run(database, path, apply=True)
    async with scene(tmp_path) as (store, memory, actor, queue):
        assert actor.group_session.last_cognized_event_rowid == cutoff
        assert actor.group_session.last_observed_event_rowid > cutoff
        assert not await memory.query_memories([actor.scene_id])
        assert not [e for e in await store.get_recent_events(actor.scene_id) if e.event_type == EventType.MESSAGE_SENT]


@pytest.mark.asyncio
async def test_quiet_reflection_memories_cursor_and_receipts_recover_atomically(tmp_path):
    from len_bot.runtime.agent_runtime import AgentRuntime
    from len_bot.memory.models import EpisodeRecord
    async def silent(_):
        return social_result(reason="旁观")
    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "quiet.db"), reflection_quiet_window_seconds=999),
                           mock_social_handler=silent)
    await runtime.start()
    try:
        actor = await runtime.scene_manager.get_or_create_actor("group:quiet")
        event = await incoming(actor, "我通常晚上画画")
        await runtime.burst_assembler.flush_scene(actor.scene_id)
        from len_bot.testing.replay import drain
        await drain(runtime)
        async def reflector(events, context):
            return (EpisodeRecord(scene_id=actor.scene_id, title="作息", summary="A说明自己常在晚上画画",
                                  source_event_ids=[event.id], participants=["user:A"], tags=[]),
                    [MemoryProposal(subject="user:A", kind="habit", key="drawing_time", value="通常晚上画画",
                                    evidence=[event.id], certainty="explicit")], None, [])
        runtime.reflection_engine.llm_reflector = reflector
        await runtime.event_store._db.execute("CREATE TRIGGER fail_cursor BEFORE INSERT ON reflection_cursors BEGIN SELECT RAISE(ABORT,'cursor failure'); END")
        await runtime.event_store._db.commit()
        await runtime._quiet_window_reflect(actor.scene_id)
        assert not await runtime.memory_store.query_memories([actor.scene_id])
        assert await runtime.memory_store.get_reflection_cursor(actor.scene_id) == 0
        await runtime.event_store._db.execute("DROP TRIGGER fail_cursor")
        await runtime.event_store._db.commit()
        await runtime._quiet_window_reflect(actor.scene_id)
        await drain(runtime)
        assert await runtime.memory_store.get_reflection_cursor(actor.scene_id) > 0
        assert actor.group_session.recent_memory_changes[0]["key"] == "drawing_time"
        assert actor.group_session.working_persons["user:A"].memory_ids
        await actor.stop()
        await actor.start()
        assert actor.group_session.recent_memory_changes[0]["key"] == "drawing_time"
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_replay_history_restores_memory_references_without_claiming_old_sends():
    from len_bot.testing.replay import ReplayLab
    from len_bot.cognition.providers import ProviderRegistry
    from len_bot.memory.models import MemoryItem
    history = [Event(id="past", scene_id="group:eval", actor_id="user:A", timestamp=1,
                     event_type=EventType.GROUP_MESSAGE_RECEIVED, payload={"raw_text": "我是A"}),
               Event(id="old_send", scene_id="group:eval", actor_id="user:123", timestamp=2,
                     event_type=EventType.MESSAGE_SENT, payload={"content": "历史发言"})]
    incoming_event = Event(id="current", scene_id="group:eval", actor_id="user:A", timestamp=10,
                           event_type=EventType.GROUP_MESSAGE_RECEIVED, payload={"raw_text": "你记得我吗"})
    async def scripted(messages):
        assert "recent_memory_changes" in messages[1]["content"]
        assert "identity" in messages[1]["content"]
        return social_result(reason="回放当前输入", content="记得")
    config = RuntimeConfig(bot_qq=123)
    lab = ReplayLab(config, SocialCognitionCore(config, ProviderRegistry(), mock_handler=scripted))
    memory = MemoryItem(subject="user:A", kind="fact", key="identity", value="自称A", scope="group:eval",
                        evidence=["past"], human_readable_assertion="自称A", created_at=3, last_confirmed_at=3)
    result = await lab.run([incoming_event], history=history, memories=[memory.model_dump(mode="json")])
    assert len(result) == 1 and result[0]["would_send"] == ["记得"]
    assert lab.last_sessions["group:eval"]["self_social_state"]["consecutive_bot_messages"] == 0
    memory.created_at = 11
    with pytest.raises(ValueError, match="pre-cutoff"):
        await lab.run([incoming_event], history=history, memories=[memory.model_dump(mode="json")])


@pytest.mark.asyncio
async def test_delayed_reflection_receipt_does_not_resurrect_superseded_working_memory(tmp_path):
    from len_bot.memory.models import EpisodeRecord
    from len_bot.events.store import ReflectionConflictError
    async with scene(tmp_path) as (store, memory, actor, queue):
        event = await incoming(actor)
        a = social_result(reason="A")
        a.memory_candidates = [belief(event, value="A")]
        await commit(actor, store, queue, a, [event.id])
        old_revision = actor.group_session.social_revision
        old_id = actor.group_session.recent_memory_changes[0]["id"]
        reflected = Event(event_type=EventType.REFLECTION_RECORDED, scene_id=actor.scene_id,
                          actor_id="system:reflection", payload={"social_revision": old_revision,
                              "episode_summary": "旧反思", "review_items": []})
        episode = EpisodeRecord(scene_id=actor.scene_id, title="理解B", summary="B", source_event_ids=[event.id],
                                participants=[], tags=[])
        cutoff = actor.group_session.last_observed_event_rowid
        await store.commit_reflection_batch(actor.scene_id, episode,
            [MemoryProposal(**belief(event, value="B").model_dump())], cutoff, reflected, expected_cursor_rowid=0)
        b_id = reflected.payload["memory_receipts"][0]["id"]
        # Reflection's durable envelope is delayed while a newer cognition commits C.
        c = social_result(reason="C")
        c.memory_candidates = [belief(event, value="C")]
        await commit(actor, store, queue, c, [event.id])
        c_id = actor.group_session.recent_memory_changes[0]["id"]
        assert {old_id, b_id}.issubset(actor.group_session.recent_memory_changes[0]["target_memory_ids"])
        actor.post_event(reflected)
        await actor._queue.join()
        assert actor.group_session.working_persons["user:A"].memory_ids == [c_id]
        assert actor.group_session.recent_episode_summary != "旧反思"
        assert reflected.metadata["reflection_stale"] and reflected.metadata["needs_review"]
        # A second outdated reflection must roll back before modifying memory or cursor.
        with pytest.raises(ReflectionConflictError):
            await store.commit_reflection_batch(actor.scene_id, episode.model_copy(update={"id": "another"}),
                [MemoryProposal(**belief(event, value="D").model_dump())], cutoff,
                reflected.model_copy(update={"id": "old_envelope"}), expected_cursor_rowid=cutoff)
        assert [m.value for m in await memory.query_memories([actor.scene_id])] == ["C"]


@pytest.mark.asyncio
async def test_persona_preview_apply_and_example_edit_api(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from len_bot.web.auth import get_current_user
    from len_bot.web.query_service import RuntimeQueryService
    from len_bot.web.routes.settings import router as settings_router
    from len_bot.web.routes.voice import router as voice_router
    async with scene(tmp_path) as (store, memory, actor, queue):
        runtime = SimpleNamespace(event_store=store, config=RuntimeConfig())
        runtime.query_service = RuntimeQueryService(runtime)
        app = FastAPI()
        app.state.runtime = runtime
        app.dependency_overrides[get_current_user] = lambda: "admin"
        app.include_router(settings_router)
        app.include_router(voice_router)
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
            assert (await client.post("/api/settings/persona/diana", json={})).status_code == 422
            preview = (await client.get("/api/settings/persona/diana")).json()
            applied = await client.post("/api/settings/persona/diana", json={"preview_token": preview["preview_token"]})
            assert applied.status_code == 200 and applied.json()["applied"]
            assert runtime.config.identity_name == "嘉然"
            example = (await client.get("/api/voice/exemplars")).json()["exemplars"][0]
            assert (await client.put(f"/api/voice/exemplars/{example['id']}", json={
                "context": "同学刚刚叫我", "content": "在呢", "tag": "运营编辑", "scene_id": "group:1"})).status_code == 200
            latest = (await client.get("/api/voice/exemplars", params={"scene_id": "group:1"})).json()["exemplars"]
            assert next(e for e in latest if e["id"] == example["id"])["content"] == "在呢"
            preview = (await client.get("/api/settings/persona/diana")).json()
            assert not (await client.post("/api/settings/persona/diana", json={"preview_token": preview["preview_token"]})).json()["applied"]
            old_persona = runtime.config.identity_persona
            async def broken_save(*args):
                raise RuntimeError("simulated database failure")
            monkeypatch.setattr(store, "save_dynamic_config", broken_save)
            response = await client.post("/api/settings/persona", json={"identity_name": "changed", "identity_persona": "changed"})
            assert response.status_code == 500
            assert runtime.config.identity_name == "嘉然" and runtime.config.identity_persona == old_persona


@pytest.mark.asyncio
async def test_retrieval_shows_both_names_and_same_scene_quote_or_missing(tmp_path):
    from len_bot.tools.retrieval import RetrievalToolkit
    async with scene(tmp_path) as (store, memory, actor, queue):
        original = await incoming(actor, "这是本群原话", sender={"nickname": "账号名", "card": "名片"}, message_id=11)
        await store.append_event(Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:other",
            actor_id="user:B", payload={"raw_text": "不该泄露的原话", "message_id": 22}))
        quote = await incoming(actor, "查引用", sender={"nickname": "另一个账号", "card": "嘉然"},
                               user="user:C", message_id=12, reply_to_message_id="11")
        missing = await incoming(actor, "查缺失引用", message_id=13, reply_to_message_id="22")
        toolkit = RetrievalToolkit(store, [actor.scene_id], actor.scene_id, memory, bot_qq=123)
        result = await toolkit.execute("query_person_history", {"actor_id": "user:C"})
        assert "账号昵称=另一个账号 群名片=嘉然" in result and "user:C" in result
        assert f"user:A EventID={original.id}: 这是本群原话" in result and "你(Bot)" not in result
        result = await toolkit.execute("read_context", {"event_id": missing.id, "before": 0, "after": 0})
        assert "本群历史中未找到" in result and "不该泄露的原话" not in result
        # Initial and historical projection enrich copies; immutable raw events are unchanged.
        assert "quote_context" not in quote.metadata
