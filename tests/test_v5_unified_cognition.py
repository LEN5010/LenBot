"""ADR-0040: one social processor, continuous inputs, deterministic authority."""
from delivery_support import allow_fake_delivery
from len_bot.actions.models import DeliveryResult, DeliveryStatus
import asyncio
import pytest
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.cognition.session import SocialMessageProposal, SocialTaskProposal
from len_bot.testing.social import social_result
from len_bot.testing.replay import drain, ReplayLab

def event(text="在吗", scene="group:1", **payload):
    return Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=scene,
                 actor_id="user:A", payload={"raw_text": text, "at_bot": True, **payload})

@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, "？"])
async def test_one_core_commits_speech_or_intentional_silence(tmp_path, content):
    calls, sent = [], []
    async def core(messages):
        calls.append(messages)
        return social_result(reason="看懂后自主决定", content=content)
    async def send(action):
        sent.append(action)
        return DeliveryResult(status=DeliveryStatus.SENT, transport="test")
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path/"one.db")), send_adapter=send, mock_social_handler=core)
    await rt.start()
    await allow_fake_delivery(rt, 'group:1')
    try:
        await rt.receive_event(event())
        await drain(rt)
        assert len(calls) == 1
        assert len(sent) == (0 if content is None else 1)
        traces = await rt.event_store.query_traces(kind="social_cognition")
        assert traces[0]["payload"]["path"] == "social"
        assert traces[0]["payload"]["gate"]["accepted"]
        assert rt.metrics.social["cognition_committed"] == 1
    finally:
        await rt.stop()

@pytest.mark.asyncio
async def test_multi_message_and_expected_reply_still_use_delivery_confirmation(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    async def core(messages):
        result = social_result(reason="接住追问", content="来了", expect_reply=True, reply_target="user:A")
        result.message_proposals.append(SocialMessageProposal(content="怎么了"))
        return result
    async def send(action):
        entered.set()
        await release.wait()
        return DeliveryResult(status=DeliveryStatus.SENT, transport="test")
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path/"reply.db")), send_adapter=send, mock_social_handler=core)
    await rt.start()
    await allow_fake_delivery(rt, 'group:1')
    try:
        await rt.receive_event(event())
        await entered.wait()
        assert await rt.event_store.get_active_open_loops("group:1") == []
        release.set()
        await drain(rt)
        loops = await rt.event_store.get_active_open_loops("group:1")
        assert len(loops) == 1 and loops[0]["target_actor_id"] == "user:A"
        assert len([e for e in await rt.event_store.get_recent_events("group:1") if e.event_type == EventType.MESSAGE_SENT]) == 2
    finally:
        release.set()
        await rt.stop()

@pytest.mark.asyncio
async def test_new_mention_continues_tool_context_without_repeating_retrieval():
    from test_v4_stage4_agentic_memory import _Registry, _Completions, _tool_response, _final_response, _Toolkit, _burst
    from len_bot.cognition.session import GroupAgentSession
    from len_bot.cognition.social_core import SocialCognitionCore
    toolkit = _Toolkit()
    completions = _Completions([_tool_response(), _final_response("别人已经答了，不重复")])
    core = SocialCognitionCore(RuntimeConfig(), _Registry(completions))
    seen = False
    async def observe():
        nonlocal seen
        if toolkit.calls and not seen:
            seen = True
            return "新增消息：@Bot 已经有人回答了，别重复"
    result, trace = await core.execute(GroupAgentSession(scene_id="group:memory"), _burst(), [], [],
                                       toolkit=toolkit, observe=observe)
    assert len(toolkit.calls) == 1
    assert trace["interim_batches"] == 1
    assert any(m["role"] == "tool" for m in completions.calls[-1]["messages"])
    assert "已经有人回答" in completions.calls[-1]["messages"][-1]["content"]
    assert result.decision.action == "silence"

@pytest.mark.asyncio
async def test_only_model_requests_deliberate():
    from test_v4_stage4_agentic_memory import _Registry, _Completions, _tool_response, _final_response, _Toolkit, _burst
    from len_bot.cognition.session import GroupAgentSession
    from len_bot.cognition.social_core import SocialCognitionCore
    response = _tool_response("{}")
    response.choices[0].message.tool_calls[0].function.name = "request_deliberate"
    completions = _Completions([response, _final_response("深度处理完毕")])
    registry = _Registry(completions)
    toolkit = _Toolkit()
    core = SocialCognitionCore(RuntimeConfig(), registry)
    _, trace = await core.execute(GroupAgentSession(scene_id="group:memory"), _burst(), [], [], toolkit=toolkit)
    assert [t.value for t in registry.tiers] == ["normal", "normal", "deliberate", "normal"]
    assert trace["escalations"][0]["reason"] == "model_requested"
    assert trace["escalations"][0]["actual_model_changed"] is False
    assert toolkit.calls == []

@pytest.mark.asyncio
async def test_replay_runs_runtime_tasks_and_shadow_queue(tmp_path):
    from len_bot.cognition.social_core import SocialCognitionCore
    from len_bot.cognition.providers import ProviderRegistry
    first = event("一分钟后叫我")
    first.timestamp = 1800000000.0
    calls = []
    async def core(messages):
        calls.append(messages)
        if len(calls) == 1:
            result = social_result(reason="安排提醒", content="行", task_ref="wake")
            result.task_proposals = [SocialTaskProposal(proposal_id="wake", description="叫醒A",
                due_at=first.timestamp+60, source_event_ids=[first.id])]
            return result
        import json
        prompt = messages[1]["content"].split("【CURRENT SOCIAL STATE】\n")[1].split("【GROUP REGISTER】")[0]
        tasks = json.loads(prompt)["tasks"]
        result = social_result(reason="提醒到期", content="到点了", fulfils_task_id=tasks[0]["id"])
        return result
    config = RuntimeConfig(db_path=str(tmp_path/"untouched.db"))
    lab = ReplayLab(config, SocialCognitionCore(config, ProviderRegistry(), mock_handler=core))
    rows = await lab.run([first], until=first.timestamp+61)
    assert len(calls) == 2
    assert [row["would_send"] for row in rows] == [["行"], ["到点了"]]
    assert lab.last_tasks[0]["status"] == "shadow_observed"
    assert lab.last_metrics["social"]["visible_messages"] == 0
    assert not (tmp_path/"untouched.db").exists()
