import json
from types import SimpleNamespace

import pytest

from len_bot.cognition.router import CognitiveTier
from len_bot.cognition.session import (
    GroupAgentSession,
    SelfSocialStateUpdate,
    SocialCognitionResult,
    SocialDecision,
    SocialDecisionAction,
    SocialPerception,
    SocialWorldState,
)
from len_bot.cognition.social_core import SocialCognitionCore
from len_bot.config import RuntimeConfig
from len_bot.events.models import Stimulus, StimulusType
from len_bot.runtime.metrics import RuntimeMetrics


def _silent_result(reason: str) -> SocialCognitionResult:
    return SocialCognitionResult(
        perception=SocialPerception(
            summary="结合长期记忆理解了当前话题",
            world_state=SocialWorldState(),
        ),
        self_state=SelfSocialStateUpdate(
            engagement="observing",
            social_position="observer",
            current_interest="medium",
            inclination_to_speak="low",
        ),
        decision=SocialDecision(
            action=SocialDecisionAction.SILENCE,
            reason=reason,
        ),
    )


class _Completions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _tool_response(arguments='{"subject": "user:A"}'):
    tool_call = SimpleNamespace(
        id="memory_call",
        function=SimpleNamespace(
            name="query_memory",
            arguments=arguments,
        ),
    )
    message = SimpleNamespace(content=None, tool_calls=[tool_call])
    message.model_dump = lambda: {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "memory_call",
            "type": "function",
            "function": {
                "name": "query_memory",
                "arguments": arguments,
            },
        }],
    }
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def _final_response(reason: str):
    message = SimpleNamespace(
        content=_silent_result(reason).model_dump_json(),
        tool_calls=None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=80, completion_tokens=30),
    )


class _Toolkit:
    def __init__(self, result_text="A 最近一直在修同一个项目，经常用‘又炸了’描述构建失败。", error=None):
        self.calls = []
        self.result_text = result_text
        self.error = error

    def get_tool_definitions(self):
        return [{
            "type": "function",
            "function": {
                "name": "query_memory",
                "description": "查询长期记忆",
                "parameters": {"type": "object", "properties": {}},
            },
        }]

    async def execute(self, name, arguments):
        self.calls.append((name, arguments))
        if self.error is not None:
            raise self.error
        return self.result_text


class _Registry:
    def __init__(self, primary, fallback=None):
        self.primary = primary
        self.fallback = fallback
        self.tiers = []

    def resolve(self, tier):
        self.tiers.append(tier)
        return SimpleNamespace(
            provider_id="primary",
            model="chat-model",
            client=SimpleNamespace(chat=SimpleNamespace(completions=self.primary)),
        )

    def resolve_fallback(self):
        if self.fallback is None:
            return None
        return SimpleNamespace(
            provider_id="backup",
            model="backup-model",
            client=SimpleNamespace(chat=SimpleNamespace(completions=self.fallback)),
        )


def _burst():
    return Stimulus(
        scene_id="group:memory",
        stimulus_type=StimulusType.SINGLE_MESSAGE,
        source_event_ids=["event-1"],
        actor_id="user:A",
        combined_text="我草又炸了",
    )


@pytest.mark.asyncio
async def test_social_core_retrieves_long_term_memory_then_decides_with_same_context():
    completions = _Completions([_tool_response(), _final_response("这是之前项目故障的延续")])
    toolkit = _Toolkit()
    core = SocialCognitionCore(
        RuntimeConfig(bot_qq=42),
        registry=_Registry(completions),
        metrics=RuntimeMetrics(),
    )

    result, trace = await core.execute(
        session=GroupAgentSession(scene_id="group:memory"),
        burst=_burst(),
        raw_events=[],
        active_open_loops=[],
        toolkit=toolkit,
    )

    assert result.decision.reason == "这是之前项目故障的延续"
    assert toolkit.calls == [("query_memory", {"subject": "user:A"})]
    assert completions.calls[1]["messages"][-1]["role"] == "tool"
    assert "最近一直在修同一个项目" in completions.calls[1]["messages"][-1]["content"]
    assert trace["steps"][0]["tool_calls"][0]["name"] == "query_memory"


@pytest.mark.asyncio
async def test_social_core_uses_configured_fallback_after_primary_provider_failure():
    primary = _Completions([RuntimeError("主供应商不可用")])
    fallback = _Completions([_final_response("由回退模型完成")])
    metrics = RuntimeMetrics()
    core = SocialCognitionCore(
        RuntimeConfig(bot_qq=42),
        registry=_Registry(primary, fallback),
        metrics=metrics,
    )

    result, trace = await core.execute(
        session=GroupAgentSession(scene_id="group:memory"),
        burst=_burst(),
        raw_events=[],
        active_open_loops=[],
    )

    assert result.decision.reason == "由回退模型完成"
    assert trace["steps"][0]["fallback"] is True
    assert metrics.social["model_fallbacks"] == 1
    assert metrics.snapshot()["provider_errors"]["primary"] == 1


@pytest.mark.asyncio
async def test_tool_payload_escalates_to_deliberate_on_same_message_flow():
    completions = _Completions([_tool_response(), _final_response("结合长记忆判断这是老问题")])
    registry = _Registry(completions)
    toolkit = _Toolkit(result_text="旧项目反复构建失败的记录。" * 120)
    core = SocialCognitionCore(
        RuntimeConfig(bot_qq=42),
        registry=registry,
        metrics=RuntimeMetrics(),
    )

    result, trace = await core.execute(
        session=GroupAgentSession(scene_id="group:memory"),
        burst=_burst(),
        raw_events=[],
        active_open_loops=[],
        toolkit=toolkit,
    )

    assert result.decision.reason == "结合长记忆判断这是老问题"
    assert registry.tiers == [CognitiveTier.NORMAL, CognitiveTier.DELIBERATE]
    assert trace["escalations"][0]["reason"].startswith("tool_payload_length")
    assert any(m["role"] == "tool" for m in completions.calls[1]["messages"])


@pytest.mark.asyncio
async def test_last_step_forces_final_decision_with_tool_choice_none():
    completions = _Completions([_tool_response(), _final_response("最后一步必须收敛")])
    metrics = RuntimeMetrics()
    core = SocialCognitionCore(
        RuntimeConfig(bot_qq=42),
        registry=_Registry(completions),
        metrics=metrics,
    )

    result, trace = await core.execute(
        session=GroupAgentSession(scene_id="group:memory"),
        burst=_burst(),
        raw_events=[],
        active_open_loops=[],
        toolkit=_Toolkit(),
        max_steps=2,
    )

    assert result.decision.reason == "最后一步必须收敛"
    assert completions.calls[1]["tool_choice"] == "none"
    assert trace["steps"][1]["forced_final"] is True
    assert metrics.social["retrieval_forced_finals"] == 1


@pytest.mark.asyncio
async def test_tool_call_budget_forces_final_decision():
    completions = _Completions([_tool_response(), _final_response("预算耗尽，直接决定")])
    metrics = RuntimeMetrics()
    core = SocialCognitionCore(
        RuntimeConfig(bot_qq=42),
        registry=_Registry(completions),
        metrics=metrics,
    )

    result, trace = await core.execute(
        session=GroupAgentSession(scene_id="group:memory"),
        burst=_burst(),
        raw_events=[],
        active_open_loops=[],
        toolkit=_Toolkit(),
        max_steps=5,
        max_tool_calls=1,
    )

    assert result.decision.reason == "预算耗尽，直接决定"
    assert completions.calls[1]["tool_choice"] == "none"
    assert trace["steps"][1]["forced_final"] is True
    assert metrics.social["retrieval_tool_calls"] == 1
    assert metrics.social["retrieval_forced_finals"] == 1


@pytest.mark.asyncio
async def test_tool_execution_error_is_fed_back_not_raised():
    completions = _Completions([_tool_response(), _final_response("工具坏了，按现有上下文决定")])
    metrics = RuntimeMetrics()
    core = SocialCognitionCore(
        RuntimeConfig(bot_qq=42),
        registry=_Registry(completions),
        metrics=metrics,
    )

    result, trace = await core.execute(
        session=GroupAgentSession(scene_id="group:memory"),
        burst=_burst(),
        raw_events=[],
        active_open_loops=[],
        toolkit=_Toolkit(error=PermissionError("scope denied")),
    )

    assert result.decision.reason == "工具坏了，按现有上下文决定"
    final_tool_message = completions.calls[1]["messages"][-1]
    assert final_tool_message["role"] == "tool"
    assert "scope denied" in final_tool_message["content"]
    assert trace["steps"][0]["tool_calls"][0]["error"] == "scope denied"
    assert metrics.social["retrieval_tool_errors"] == 1


@pytest.mark.asyncio
async def test_malformed_tool_arguments_are_fed_back_not_raised():
    completions = _Completions([_tool_response(arguments="{not json"), _final_response("参数坏了也能决定")])
    metrics = RuntimeMetrics()
    toolkit = _Toolkit()
    core = SocialCognitionCore(
        RuntimeConfig(bot_qq=42),
        registry=_Registry(completions),
        metrics=metrics,
    )

    result, trace = await core.execute(
        session=GroupAgentSession(scene_id="group:memory"),
        burst=_burst(),
        raw_events=[],
        active_open_loops=[],
        toolkit=toolkit,
    )

    assert result.decision.reason == "参数坏了也能决定"
    assert toolkit.calls == []
    final_tool_message = completions.calls[1]["messages"][-1]
    assert final_tool_message["role"] == "tool"
    assert "invalid_arguments" in final_tool_message["content"]
    assert trace["steps"][0]["tool_calls"][0]["error"]
    assert metrics.social["retrieval_tool_errors"] == 1


@pytest.mark.asyncio
async def test_assistant_tool_message_is_minimal_for_provider_compat():
    completions = _Completions([_tool_response(), _final_response("消息字段干净")])
    core = SocialCognitionCore(
        RuntimeConfig(bot_qq=42),
        registry=_Registry(completions),
        metrics=RuntimeMetrics(),
    )

    await core.execute(
        session=GroupAgentSession(scene_id="group:memory"),
        burst=_burst(),
        raw_events=[],
        active_open_loops=[],
        toolkit=_Toolkit(),
    )

    assistant = next(m for m in completions.calls[1]["messages"] if m.get("tool_calls"))
    assert set(assistant.keys()) == {"role", "content", "tool_calls"}
    assert set(assistant["tool_calls"][0].keys()) == {"id", "type", "function"}


@pytest.mark.asyncio
async def test_fallback_failure_after_primary_failure_raises_and_records_both():
    primary = _Completions([RuntimeError("主供应商不可用")])
    fallback = _Completions([RuntimeError("回退供应商也不可用")])
    metrics = RuntimeMetrics()
    core = SocialCognitionCore(
        RuntimeConfig(bot_qq=42),
        registry=_Registry(primary, fallback),
        metrics=metrics,
    )

    with pytest.raises(RuntimeError):
        await core.execute(
            session=GroupAgentSession(scene_id="group:memory"),
            burst=_burst(),
            raw_events=[],
            active_open_loops=[],
        )

    provider_errors = metrics.snapshot()["provider_errors"]
    assert provider_errors["primary"] == 1
    assert provider_errors["backup"] == 1


@pytest.mark.asyncio
async def test_forced_final_ignored_by_provider_raises():
    completions = _Completions([_tool_response()])
    metrics = RuntimeMetrics()
    toolkit = _Toolkit()
    core = SocialCognitionCore(
        RuntimeConfig(bot_qq=42),
        registry=_Registry(completions),
        metrics=metrics,
    )

    with pytest.raises(RuntimeError, match="forced final"):
        await core.execute(
            session=GroupAgentSession(scene_id="group:memory"),
            burst=_burst(),
            raw_events=[],
            active_open_loops=[],
            toolkit=toolkit,
            max_steps=1,
        )

    assert toolkit.calls == []
    assert metrics.social["retrieval_forced_finals"] == 1
