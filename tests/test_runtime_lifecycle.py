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
        for scene in list(runtime.burst_assembler._buffers):
            await runtime.burst_assembler.flush_scene(scene)
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
        assert [wake.event_id for wake in actor.session.pending_wakes] == [event.id]
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
        assert not actor.session.pending_wakes
    finally:
        release.set()
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure_kind',['conflict','protocol'])
async def test_rejected_work_intent_is_visible_once_without_creating_a_job(tmp_path,failure_kind):
    entered, release = asyncio.Event(), asyncio.Event()
    contexts = []
    calls = 0

    async def turn(session, events):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
            if failure_kind=='protocol':
                from len_bot.cognition.agent_loop import AgentProtocolError
                raise AgentProtocolError('ack_ref没有对应本轮提案，先调用start_work')
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
                  and message['content'].startswith('前一轮处理这些输入时失败')]
        assert len(failed) == 1 and failed[0]['role'] == 'user'
        assert 'not_committed' in failed[0]['content']
        assert ('核对公开模型的正式评测' if failure_kind=='conflict' else 'ack_ref没有对应本轮提案') in failed[0]['content']
        assert not [event for event in await runtime.event_store.get_recent_events(SCENE)
                    if event.event_type in {EventType.MESSAGE_SENT,EventType.ACTION_SHADOWED}]
        assert await runtime.event_store.uncommitted_job_attempts('group:other',0,1000) == []
        consumed=actor.session.last_observed_event_rowid
        assert not actor.session.pending_wakes
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
        assert result.accepted and [wake.event_id for wake in actor.session.pending_wakes] == [incoming.id]
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
        assert calls == [0] and [wake.event_id for wake in session.pending_wakes] == ["knowledge:input"] and session.knowledge_revision == 1
        traces = await runtime.event_store.query_traces(scene_id=SCENE, kind="conversation_error")
        assert len(traces) == 1 and traces[0]["payload"]["error_type"] == "SceneCommitConflict"
        await runtime.receive_event(human(event_id="knowledge:new-input"))
        await settle(runtime)
        assert calls == [0, 1]
        assert not runtime.scene_manager._actors[SCENE].session.pending_wakes
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


@pytest.mark.asyncio
async def test_attention_storage_sampling_inflight_and_delivery_survive_restart(tmp_path):
    """One event chain protects the scheduling/delivery boundary, with real model plumbing."""
    import httpx
    import json
    import re
    from openai import AsyncOpenAI
    from len_bot.actions.models import DeliveryResult, DeliveryStatus
    from test_conversation_agent import response, call, text_reply
    from runtime_support import configure_fixture_profile

    now = [100.0]
    draws, requests = [], []
    send_started, release_send = asyncio.Event(), asyncio.Event()

    def random_source():
        draws.append(now[0])
        return 1.0

    async def model(request):
        requests.append(request)
        args = text_reply('嗯，你说') if len(requests) == 2 else {'messages':[]}
        if len(requests) == 5:
            messages = json.loads(request.content)['messages']
            original = next(message['content'] for message in messages
                            if isinstance(message.get('content'), str) and 'B先前的问题' in message['content'])
            ref = re.search(r'\[(M\d+) ', original).group(1)
            args = {'messages':[{'segments':[{'text':'接着回答B'}], 'reply_to':ref}]}
        return httpx.Response(200, json=response(call('finish_turn', args)))

    async def send(_action):
        send_started.set()
        await release_send.wait()
        return DeliveryResult(status=DeliveryStatus.SENT, transport='isolated-receipt-fixture', message_id='receipt:1')

    config = RuntimeConfig(db_path=str(tmp_path/'attention.db'), message_pacing=False,
                           attention_keywords=['计算'], attention_sample_probability=.2)
    runtime = AgentRuntime(config, clock=lambda:now[0], attention_random=random_source, send_adapter=send)
    await runtime.start()
    await configure_fixture_profile(runtime)
    client = AsyncOpenAI(api_key='fixture', base_url='https://fixture.invalid/v1', max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(model)))
    runtime.provider_registry._clients['fixture'] = client
    await runtime.set_shadow_mode(False)

    async def incoming(ident, text, actor='user:A'):
        event = Event(id=ident, event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=SCENE,
            actor_id=actor, timestamp=now[0], payload={'raw_text':text,'message_id':ident})
        await runtime.receive_event(event)
        await runtime.scene_manager._actors[SCENE]._queue.join()
        await runtime.burst_assembler.flush_scene(SCENE)
        if runtime._conversation_tasks:
            await asyncio.gather(*list(runtime._conversation_tasks.values()))
        return event

    try:
        for index in range(4):
            await incoming('ambient:'+str(index), '今天一起吃饭')
        assert len(draws) == 1 and not requests
        assert not await runtime.event_store.query_traces(scene_id=SCENE, kind='conversation')
        await incoming('keyword:1', '他们在讨论计算方法')
        assert len(requests) == 1
        await incoming('keyword:2', '继续讨论计算方法')
        assert len(requests) == 1
        await incoming('named', '小然在吗')
        await asyncio.wait_for(send_started.wait(), 2)
        session = runtime.scene_manager.get_session(SCENE)
        assert session.focused_participants == {}
        # The conversation has committed but transport is still waiting.
        follow = await incoming('in-flight', '刚才的问题再补一句')
        assert 'in_flight_follow_up' in follow.metadata['attention_reasons']
        assert len(requests) == 3 and session.focused_participants == {}
        release_send.set()
        await runtime.action_queue._queue.join()
        await runtime.scene_manager._actors[SCENE]._queue.join()
        assert runtime.scene_manager.get_session(SCENE).focused_participants == {'user:A':220.0}
        follow = await incoming('delivered-follow-up', '还有一个细节')
        assert 'continuing_interaction' in follow.metadata['attention_reasons']
        await runtime.receive_event(Event(event_type=EventType.MESSAGE_SENT, scene_id=SCENE,
            actor_id=runtime.bot_actor_id, timestamp=now[0], metadata={'simulated':True},
            payload={'response_actor_ids':['user:B'],'delivery_status':'sent'}))
        await runtime.scene_manager._actors[SCENE]._queue.join()
        assert 'user:B' not in runtime.scene_manager.get_session(SCENE).focused_participants
        await incoming('old-b', 'B先前的问题', actor='user:B')
        await incoming('quote-b', '小然，接一下刚才那句')
        await settle(runtime)
        assert runtime.scene_manager.get_session(SCENE).focused_participants['user:B'] == 220.0
        follow = await incoming('b-follow', '还有呢', actor='user:B')
        assert follow.metadata['attention_reasons'] == ['continuing_interaction']
        assert len(requests) == 6
        # Shut the model profile off to leave an actual unprocessed wake.
        await runtime.provider_registry.apply_update(list(runtime.provider_registry._providers.values()), None)
        await runtime.event_store.save_dynamic_config('provider_config', runtime.provider_registry.export())
        await incoming('durable-wake', '小然，稍后处理这个问题')
        saved = await runtime.event_store.load_scene_session(SCENE)
        assert [item['event_id'] for item in saved['pending_wakes']] == ['durable-wake']
        assert saved['attention_sample_window'] == 0
    finally:
        release_send.set()
        await runtime.stop()
    restored = AgentRuntime(config, clock=lambda:now[0], attention_random=random_source)
    await restored.start()
    try:
        session = restored.scene_manager.get_session(SCENE)
        assert session.model_dump() == saved
        event = Event(id='restart-ambient', event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=SCENE,
                      actor_id='user:C', timestamp=now[0], payload={'raw_text':'路过'})
        await restored.receive_event(event)
        await settle(restored)
        assert len(draws) == 1
        assert await restored.event_store.event_exists('ambient:0', SCENE)
        assert [wake.event_id for wake in session.pending_wakes] == ['durable-wake']
    finally:
        await restored.stop()


@pytest.mark.asyncio
async def test_late_control_candidate_continues_in_one_budget_without_side_effects(tmp_path):
    import json
    import httpx
    from openai import AsyncOpenAI
    from runtime_support import configure_fixture_profile
    from test_conversation_agent import response, call

    requests = []
    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path/'fresh-candidate.db'),
        conversation_max_steps=4, attention_sample_probability=0), clock=lambda:100.0)
    await runtime.start()
    await configure_fixture_profile(runtime)

    async def model(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            result = response(call('start_work', {'goal':'核对资料A','evidence':['M1']}, 'stage'))
        elif len(requests) == 2:
            await runtime.receive_event(Event(id='cancel-before-commit', event_type=EventType.GROUP_MESSAGE_RECEIVED,
                scene_id=SCENE, actor_id='user:A', timestamp=100,
                payload={'raw_text':'不用查了，取消','message_id':'cancel-before-commit'}))
            await runtime.scene_manager._actors[SCENE]._queue.join()
            result = response(call('finish_turn', {'messages':[{'segments':[{'text':'我去查'}],'ack_ref':'S1'}]}, 'stale'))
        else:
            assert len(requests) == 3
            assert '不用查了' in str(requests[-1]['messages'])
            receipt = next(message for message in requests[-1]['messages'] if message.get('tool_call_id') == 'stale')
            assert json.loads(receipt['content'])['committed'] is False
            result = response(call('discard_proposal', {'proposal_ref':'S1'}, 'discard'),
                              call('finish_turn', {'messages':[]}, 'final'))
        return httpx.Response(200, json=result)

    client = AsyncOpenAI(api_key='fixture', base_url='https://fixture.invalid/v1', max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(model)))
    runtime.provider_registry._clients['fixture'] = client
    try:
        await runtime.receive_event(human('帮我核对资料A', 'work-original'))
        await settle(runtime)
        assert len(requests) == 3
        assert not await runtime.event_store.list_jobs(SCENE)
        assert not runtime.scene_manager.get_session(SCENE).pending_wakes
        events = await runtime.event_store.get_recent_events(SCENE)
        commits = [event for event in events if event.event_type == EventType.CONVERSATION_COMMITTED]
        assert len(commits) == 1 and {'work-original','cancel-before-commit'} <= set(commits[0].payload['source_event_ids'])
        assert not any(event.event_type in {EventType.MESSAGE_SENT,EventType.ACTION_SHADOWED} for event in events)
        calls = await runtime.event_store.list_model_calls(SCENE)
        assert len(calls) == 3 and len({item['episode_id'] for item in calls}) == 1
        trace = (await runtime.event_store.query_traces(scene_id=SCENE, kind='conversation'))[0]['payload']['conversation']
        assert trace['model_calls_used'] == 3 and trace['tool_calls_used'] == 2
    finally:
        await runtime.stop()
