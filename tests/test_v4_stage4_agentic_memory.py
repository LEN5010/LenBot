import json
from types import SimpleNamespace

import pytest

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


def _tool_response():
    tool_call = SimpleNamespace(
        id="memory_call",
        function=SimpleNamespace(
            name="query_memory",
            arguments=json.dumps({"subject": "user:A"}),
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
                "arguments": '{"subject":"user:A"}',
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
    def __init__(self):
        self.calls = []

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
        return "A 最近一直在修同一个项目，经常用‘又炸了’描述构建失败。"


class _Registry:
    def __init__(self, primary, fallback=None):
        self.primary = primary
        self.fallback = fallback

    def resolve(self, _tier):
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
