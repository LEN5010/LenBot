import json
from types import SimpleNamespace as NS

import pytest
import httpx

from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.cognition.providers import ProviderRegistry
from len_bot.cognition.social_core import SocialCognitionCore
from len_bot.testing.replay import ReplayLab, ReplayInjection, ReplayFailure
from len_bot.testing.social import social_result
from len_bot.tools.results import ToolResult
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.testing.replay import drain
from len_bot.web.app import create_app


def message(eid, text, timestamp=1000):
    return Event(id=eid, event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:replay", actor_id="user:1",
                 timestamp=timestamp, payload={"raw_text": text, "at_bot": True, "message_id": eid})


class Registry:
    def __init__(self):
        self.calls = []
    def resolve(self, tier):
        return NS(provider_id="fake", model="script", client=NS(chat=NS(completions=self)))
    def resolve_fallback(self):
        return None
    def has_live_provider(self):
        return True
    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            result = NS(content=None, tool_calls=[NS(id="query", function=NS(name="web_search", arguments='{"query":"public documentation"}'))])
        else:
            assert "不要命令行" in json.dumps(kwargs["messages"], ensure_ascii=False)
            result = NS(content=social_result(reason="按最新要求", content="我会按图形界面的方式说明").model_dump_json(), tool_calls=None)
        return NS(choices=[NS(message=result)], usage=None)


@pytest.mark.asyncio
async def test_input_arrives_during_tool_then_affects_final():
    registry = Registry()
    core = SocialCognitionCore(RuntimeConfig(bot_qq=999), registry)
    lab = ReplayLab(RuntimeConfig(bot_qq=999), core,
        tool_results={"web_search": str(ToolResult(content="There is a GUI setting", evidence_kind="external"))},
        injections=[ReplayInjection("before_tool", [message("late", "不要命令行", 1002)])],
        delivery_mode="simulated", strict=True)
    outputs = await lab.run([message("first", "查一下操作方式")])
    assert lab.last_run["completed"]
    assert outputs[-1]["would_send"] == ["我会按图形界面的方式说明"]
    sent = [event for event in lab.last_deliveries if event["event_type"] == "MESSAGE_SENT"]
    assert len(sent) == 1 and sent[0]["metadata"]["simulated"]
    assert sent[0]["payload"]["transport"] == "replay_simulated"
    assert lab.last_metrics["social"]["visible_messages"] == 0
    assert lab.last_metrics["social"]["simulated_messages"] == 1


@pytest.mark.asyncio
async def test_final_continuation_acknowledges_its_own_tool_receipt():
    class ContinuationRegistry(Registry):
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls)==2:
                response=NS(content=None,tool_calls=[NS(id="tool",function=NS(name="web_search",arguments='{"query":"public docs"}'))])
            else:
                result=social_result(reason="已有资料，询问需求",content="资料确认支持这个设置，你需要哪一项？",expect_reply=True,reply_target="user:1")
                response=NS(content=result.model_dump_json(),tool_calls=None)
            return NS(choices=[NS(message=response)],usage=None)
    registry=ContinuationRegistry();config=RuntimeConfig(bot_qq=999)
    lab=ReplayLab(config,SocialCognitionCore(config,registry),delivery_mode="simulated",strict=True,
        tool_results={"web_search":str(ToolResult(content="公开设置说明",evidence_kind="external"))},
        injections=[ReplayInjection("after_model",[message("late","先看文档再告诉我有哪些设置",1002)])])
    await lab.run([message("first","等我补充")])
    assert len(registry.calls)==3
    assert lab.last_run["completed"]
    assert len([e for e in lab.last_deliveries if e["event_type"]=="MESSAGE_SENT"])==1
    cognition=next(t for t in lab.last_traces if t["kind"]=="social_cognition")
    assert cognition["payload"]["cognition"]["acknowledged_tool_observations"]==1


@pytest.mark.asyncio
async def test_simulated_reply_is_next_turn_input_but_not_human_feedback():
    seen = []
    async def respond(messages):
        seen.append(json.dumps(messages, ensure_ascii=False))
        return social_result(reason="接话", content="记下这个说法")
    core = SocialCognitionCore(RuntimeConfig(bot_qq=999), ProviderRegistry(), mock_handler=respond)
    lab = ReplayLab(RuntimeConfig(bot_qq=999), core, delivery_mode="simulated", strict=True)
    await lab.run([message("one", "这样叫我"), message("two", "还记得吗", 1010)])
    assert "MESSAGE_SENT" in seen[-1] and "记下这个说法" in seen[-1]
    assert lab.last_run["human_response_to_candidate"] is None


@pytest.mark.asyncio
async def test_shadow_does_not_forge_sent_and_failed_live_run_is_reported():
    async def respond(messages):
        return social_result(reason="接话", content="候选")
    config = RuntimeConfig(bot_qq=999)
    lab = ReplayLab(config, SocialCognitionCore(config, ProviderRegistry(), mock_handler=respond), strict=True)
    await lab.run([message("one", "你好")])
    assert all(event["event_type"] != "MESSAGE_SENT" for event in lab.last_deliveries)
    broken = ReplayLab(config, SocialCognitionCore(config, ProviderRegistry()), strict=True)
    with pytest.raises(ReplayFailure):
        await broken.run([message("two", "你好")])
    assert broken.last_run["completed"] is False
    assert any(trace["kind"] == "social_cognition_error" for trace in broken.last_traces)


@pytest.mark.asyncio
async def test_panel_can_select_isolated_delivery_mode(tmp_path):
    async def respond(messages):
        return social_result(reason="panel fixture", content="面板回放候选")
    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "panel.db"), bot_qq=999), mock_social_handler=respond)
    await runtime.start()
    try:
        await runtime.set_shadow_mode(True)
        event = message("panel-input", "你好", runtime.clock())
        await runtime.receive_event(event)
        await drain(runtime)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(runtime)), base_url="http://test") as client:
            assert (await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})).status_code == 200
            response = await client.post("/api/replay", json={"scene_id": event.scene_id, "delivery_mode": "simulated", "tool_mode": "mock"})
            assert response.status_code == 200
            execution = response.json()["runs"][0]["execution"]
            assert execution["completed"] and execution["delivery_mode"] == "simulated"
            assert runtime.shadow_mode  # Isolated mode must not change the host runtime.
    finally:
        await runtime.stop()
