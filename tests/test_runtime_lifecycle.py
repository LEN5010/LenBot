"""Runtime startup, shutdown and explicit Reset use only isolated state and mocks."""

import asyncio
import io
from pathlib import Path

from PIL import Image
import pytest

from len_bot.actions.models import ActionItem, ActionType
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.context import ConversationContext
from len_bot.cognition.jobs import JobProposal
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal, TaskProposal
from len_bot.cognition.providers import ModelProfile, ProviderConfig, RoutingConfig
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.memory.models import MemoryProposal
from len_bot.tools.results import ToolResult


SCENE = "group:126300994"


def human(text="请接话", event_id=None):
    args = {"id": event_id} if event_id else {}
    return Event(**args, event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=SCENE, actor_id="user:A",
                 timestamp=100.0, payload={"raw_text": text, "at_bot": True, "sender": {"nickname": "A"}})


def reply(text="知道了"):
    return EpisodeOutcome(disposition=FinalDisposition.ACTION, decision_reason="回应当前原话",
                          message_proposals=[MessageProposal(content=text)])


async def settle(runtime):
    for _ in range(20):
        for actor in list(runtime.scene_manager._actors.values()):
            await asyncio.wait_for(actor._queue.join(), 2)
        tasks = list(runtime._conversation_tasks.values())
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), 2)
        await asyncio.wait_for(runtime.action_queue._queue.join(), 2)
        for actor in list(runtime.scene_manager._actors.values()):
            await asyncio.wait_for(actor._queue.join(), 2)
        if not runtime._conversation_tasks and not runtime._pending_bursts:
            return
        await asyncio.sleep(0)
    raise AssertionError("Isolated runtime did not settle")


@pytest.mark.asyncio
async def test_unconfigured_runtime_records_inputs_without_creating_default_models(tmp_path):
    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "unconfigured.db")), clock=lambda: 100.0)
    await runtime.start()
    try:
        event = human(event_id="unconfigured:input")
        await runtime.receive_event(event)
        await settle(runtime)
        assert runtime.provider_registry.snapshot() == {"providers": [], "routing": None}
        assert await runtime.event_store.get_dynamic_config("provider_config") is None
        actor = runtime.scene_manager._actors[SCENE]
        assert actor.session.last_cognized_event_rowid == 0
        assert await runtime.event_store.event_exists(event.id, SCENE)
        assert not runtime._conversation_tasks
        assert await runtime.event_store.query_traces(scene_id=SCENE) == []
    finally:
        await runtime.stop()
    assert runtime.event_store._db is None and not runtime.scene_manager._actors


@pytest.mark.asyncio
async def test_mock_turn_uses_actor_gate_and_shadow_never_becomes_delivery(tmp_path):
    calls = []

    async def turn(session, events):
        calls.append((session, events))
        return reply()

    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "mock.db")), mock_turn_handler=turn, clock=lambda: 100.0)
    await runtime.start()
    try:
        await runtime.receive_event(human(event_id="mock:input"))
        await settle(runtime)
        events = await runtime.event_store.get_recent_events(SCENE)
        assert len(calls) == 1
        assert any(event.event_type == EventType.CONVERSATION_COMMITTED for event in events)
        assert any(event.event_type == EventType.ACTION_SHADOWED for event in events)
        assert all(event.event_type != EventType.MESSAGE_SENT for event in events)
        assert runtime._last_gate_decision.accepted
        assert runtime.scene_manager._actors[SCENE].session.last_bot_message_event_id is None
        trace = (await runtime.event_store.query_traces(scene_id=SCENE, kind="conversation"))[0]
        assert "conversation" in trace["payload"] and "cognition" not in trace["payload"]
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_input_arriving_during_a_turn_remains_for_the_next_turn(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def turn(session, events):
        seen.append([event.id for event in events if event.event_type == EventType.GROUP_MESSAGE_RECEIVED])
        if len(seen) == 1:
            entered.set()
            await release.wait()
        return reply()

    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "pending.db")), mock_turn_handler=turn, clock=lambda: 100.0)
    await runtime.start()
    try:
        await runtime.receive_event(human(event_id="input:old"))
        await asyncio.wait_for(entered.wait(), 2)
        await runtime.receive_event(human(event_id="input:new"))
        actor = runtime.scene_manager._actors[SCENE]
        await asyncio.wait_for(actor._queue.join(), 2)
        release.set()
        await settle(runtime)
        assert seen[0] == ["input:old"] and seen[1] == ["input:old", "input:new"]
        traces = await runtime.event_store.query_traces(scene_id=SCENE, kind="conversation")
        assert len(traces) == 2
        first = next(trace for trace in traces if trace["payload"]["conversation"]["source_event_ids"] == ["input:old"])
        newer = next(event for event in await runtime.event_store.get_recent_events(SCENE) if event.id == "input:new")
        assert first["payload"]["conversation"]["through_event_rowid"] < newer.metadata["_rowid"]
        assert actor.session.last_cognized_event_rowid >= newer.metadata["_rowid"]
    finally:
        release.set()
        await runtime.stop()


@pytest.mark.asyncio
async def test_rejected_work_intent_is_visible_once_without_creating_a_job(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    contexts = []
    calls = 0

    async def turn(session, events):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
            return EpisodeOutcome(disposition=FinalDisposition.ACTION, decision_reason='拟查询',
                job_proposals=[JobProposal(proposal_id='query', goal='核对公开模型的正式评测',
                                           source_event_ids=['question'])],
                message_proposals=[MessageProposal(content='我去查询', task_ref='query')])
        context = ConversationContext(runtime, session, session.last_observed_event_rowid)
        contexts.extend(await context.build(events, {'question', 'new-input'}))
        return EpisodeOutcome(decision_reason='根据新输入重新决定，暂不建立工作')

    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path/'rejected-work.db')),
                           mock_turn_handler=turn, clock=lambda:100.0)
    await runtime.start()
    try:
        await runtime.receive_event(human('这个模型的评测怎么样', 'question'))
        await asyncio.wait_for(entered.wait(), 2)
        await runtime.receive_event(human('先等等', 'new-input'))
        actor = runtime.scene_manager._actors[SCENE]
        await actor._queue.join()
        release.set()
        await settle(runtime)
        assert calls == 2 and not await runtime.event_store.list_jobs(SCENE)
        failed = [message for message in contexts if isinstance(message.get('content'),str)
                  and message['content'].startswith('前一轮的工作意向')]
        assert len(failed) == 1 and failed[0]['role'] == 'user'
        assert 'not_committed' in failed[0]['content'] and '核对公开模型的正式评测' in failed[0]['content']
        assert not [event for event in await runtime.event_store.get_recent_events(SCENE)
                    if event.event_type in {EventType.MESSAGE_SENT,EventType.ACTION_SHADOWED}]
        assert await runtime.event_store.uncommitted_job_attempts('group:other',0,1000) == []
        consumed=actor.session.last_cognized_event_rowid
        assert await runtime.event_store.uncommitted_job_attempts(SCENE,consumed,consumed) == []
        assert await runtime.event_store.uncommitted_job_attempts(SCENE,0,0) == []
    finally:
        release.set()
        await runtime.stop()


@pytest.mark.asyncio
async def test_reset_cancels_old_cognition_and_preserves_operator_assets_and_config(tmp_path):
    entered, cancelled = asyncio.Event(), asyncio.Event()
    prompts = []

    async def turn(session, events):
        prompts.append([event.raw_text for event in events])
        if len(prompts) == 1:
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        assert "旧对话" not in prompts[-1]
        return reply("重新开始")

    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "reset.db")), mock_turn_handler=turn, clock=lambda: 100.0)
    await runtime.start()
    try:
        await runtime.event_store.save_dynamic_config("persona_config", {"identity_name": "保留昵称", "identity_persona": "人工人格"})
        await runtime.event_store.add_voice_example("", content="人工样例", context="问候")
        picture = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(picture, format="PNG")
        sticker = await runtime.media_service.upload(picture.getvalue(), "global-safe", "人工表情", ["开心"])
        observation, event = await runtime.event_store.save_tool_observation(SCENE, "read_history", {}, ToolResult(content="旧资料"))
        await runtime.commit_tool_observation(event)
        config_before = {key: await runtime.event_store.get_dynamic_config(key) for key in ("persona_config", "provider_config", "delivery_scenes", "shadow_config")}
        voice_before = await runtime.event_store.list_voice_examples()
        await runtime.receive_event(human("旧对话", "before:reset"))
        await asyncio.wait_for(entered.wait(), 2)
        response = await asyncio.wait_for(runtime.reset_conversation_data("tester"), 5)
        assert response["success"] and cancelled.is_set() and len(prompts) == 1
        assert await runtime.event_store.get_recent_events(SCENE) == []
        assert await runtime.event_store.read_tool_observation(observation.result_id, [SCENE]) is None
        assert runtime.scene_manager.get_session(SCENE) is None
        assert {key: await runtime.event_store.get_dynamic_config(key) for key in config_before} == config_before
        assert await runtime.event_store.list_voice_examples() == voice_before
        assert Path(sticker["path"]).is_file()
        assert await runtime.event_store.event_exists(sticker["source_event_id"], "global-safe")
        assert (await runtime.event_store.get_media(sticker["id"], ["global-safe"]))["description"] == "人工表情"
        await runtime.receive_event(human("新对话", "after:reset"))
        await settle(runtime)
        assert len(prompts) == 2
        assert runtime._last_gate_decision.accepted
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_reset_waits_for_old_delivery_and_job_cancellation_before_clearing(tmp_path):
    delivery_started, delivery_cancelled, work_started, work_cancelled = [asyncio.Event() for _ in range(4)]

    async def send(_action):
        delivery_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            delivery_cancelled.set()
            raise

    async def work():
        work_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            work_cancelled.set()
            raise

    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "cancel.db")), send_adapter=send, clock=lambda: 100.0)
    await runtime.start()
    try:
        await runtime.set_shadow_mode(False)
        runtime.action_queue.enqueue(ActionItem(action_type=ActionType.SEND_GROUP_MESSAGE, scene_id=SCENE, content="旧发送", batch_id="old"))
        runtime.job_runner._tasks[SCENE] = asyncio.create_task(work())
        await asyncio.wait_for(asyncio.gather(delivery_started.wait(), work_started.wait()), 2)
        await asyncio.wait_for(runtime.reset_conversation_data("tester"), 5)
        assert delivery_cancelled.is_set() and work_cancelled.is_set()
        assert await runtime.event_store.get_recent_events(SCENE) == []
        assert not runtime.job_runner._tasks and runtime.action_queue._queue.empty()
        assert runtime.shadow_mode is False and runtime.allowed_scenes == {SCENE}
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_operator_effects_keep_unread_conversation_and_link_the_operator_event(tmp_path):
    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "operator.db")), clock=lambda: 100.0)
    await runtime.start()
    try:
        incoming = human(event_id="still:unread")
        await runtime.receive_event(incoming)
        actor = await runtime.scene_manager.get_or_create_actor(SCENE)
        await actor._queue.join()
        event = await runtime.record_operator_event(SCENE, "inspect", "tester", {"operation": "cannot-override", "operator": "cannot-override"})
        result = await runtime.operator_outcome(SCENE, EpisodeOutcome(decision_reason="运营操作"), source_event_ids=[event.id])
        assert result.accepted and actor.session.last_cognized_event_rowid == 0
        assert event.actor_id == "operator:tester" and event.payload["operation"] == "inspect"
        committed = [item for item in await runtime.event_store.get_recent_events(SCENE) if item.event_type == EventType.CONVERSATION_COMMITTED]
        assert committed[0].payload["source_event_ids"] == [event.id]
        assert await runtime.event_store.event_exists(incoming.id, SCENE)
    finally:
        await runtime.stop()


async def enable_fake_profiles(runtime):
    profile = ModelProfile(provider_id="isolated", model="never-called")
    await runtime.provider_registry.apply_update(
        [ProviderConfig(id="isolated", base_url="http://127.0.0.1:9/v1", api_key="unused")],
        RoutingConfig(conversation=profile, work=profile),
    )


@pytest.mark.asyncio
async def test_already_read_tool_receipt_does_not_make_a_control_proposal_stale(tmp_path):
    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "observation.db")), clock=lambda: 100.0)

    class IsolatedCore:
        async def run(self, session, events, through_rowid, episode_id, source_event_ids, *, observe, commit, trace):
            _result, event = await runtime.event_store.save_tool_observation(
                SCENE, "read_history", {}, ToolResult(content="已读取的原话", evidence_kind="retrieval"),
            )
            await runtime.commit_tool_observation(event)
            assert event.metadata["_rowid"] > through_rowid
            assert runtime.scene_manager._actors[SCENE].session.last_observed_event_rowid == through_rowid
            assert await observe() is None
            outcome = EpisodeOutcome(decision_reason="按当前已读请求提醒", task_proposals=[
                TaskProposal(proposal_id="reminder", description="明确请求的提醒", due_at=200,
                             source_event_ids=["control:input"]),
            ])
            assert (await commit(outcome)).accepted
            return outcome

    runtime.social_core = IsolatedCore()
    await runtime.start()
    await enable_fake_profiles(runtime)
    try:
        await runtime.receive_event(human(event_id="control:input"))
        await settle(runtime)
        assert len(await runtime.event_store.scene_tasks(SCENE)) == 1
        assert await runtime.event_store.pending_runtime_events() == []
        assert runtime._last_gate_decision.accepted
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_changed_knowledge_aborts_observe_without_consuming_input_or_repeating_the_model(tmp_path):
    entered, resume = asyncio.Event(), asyncio.Event()
    calls = []
    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "knowledge-change.db")), clock=lambda: 100.0)

    class IsolatedCore:
        async def run(self, session, events, through_rowid, episode_id, source_event_ids, *, observe, commit, trace):
            calls.append(session.knowledge_revision)
            if len(calls) == 1:
                entered.set()
                await resume.wait()
                await observe()
                raise AssertionError("Changed knowledge must abort this run")
            outcome = EpisodeOutcome(decision_reason="使用新的认识继续")
            assert (await commit(outcome)).accepted
            return outcome

    runtime.social_core = IsolatedCore()
    await runtime.start()
    await enable_fake_profiles(runtime)
    try:
        await runtime.receive_event(human(event_id="knowledge:input"))
        await asyncio.wait_for(entered.wait(), 2)
        operation = await runtime.record_operator_event(SCENE, "review", "tester")
        outcome = EpisodeOutcome(decision_reason="提交已有原话支持的认识", memory_proposals=[MemoryProposal(
            subject="user:A", kind="preference", statement="A明确希望认真回答", basis="reported", evidence=["knowledge:input"],
        )])
        assert (await runtime.operator_outcome(SCENE, outcome, source_event_ids=[operation.id])).accepted
        resume.set()
        await settle(runtime)
        session = runtime.scene_manager._actors[SCENE].session
        assert calls == [0] and session.last_cognized_event_rowid == 0 and session.knowledge_revision == 1
        traces = await runtime.event_store.query_traces(scene_id=SCENE, kind="conversation_error")
        assert len(traces) == 1 and traces[0]["payload"]["error_type"] == "SceneCommitConflict"
        await runtime.receive_event(human(event_id="knowledge:new-input"))
        await settle(runtime)
        assert calls == [0, 1]
        assert runtime.scene_manager._actors[SCENE].session.last_cognized_event_rowid > 0
    finally:
        resume.set()
        await runtime.stop()


def test_mailbox_acknowledges_only_the_observed_snapshot():
    mailbox = EpisodeMailbox("cutoff", SCENE, 0)
    first, later = human(event_id="first"), human(event_id="later")
    first.metadata["_rowid"], later.metadata["_rowid"] = 1, 2
    mailbox.post(first)
    mailbox.post(later)
    mailbox.acknowledge_through(1)
    assert mailbox.has_unseen_interim()
    assert [event.id for event in mailbox.fetch_unseen_interim_events()] == ["later"]
