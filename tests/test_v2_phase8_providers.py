import pytest
import asyncio
import json
import time
from types import SimpleNamespace

from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.runtime.metrics import RuntimeMetrics
from len_bot.events.models import Event, EventType
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal
from len_bot.cognition.react_core import ReActAgentCore
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.router import CognitiveTier


@pytest.mark.asyncio
async def test_legacy_model_config_one_time_migration(tmp_path):
    """
    ADR-0020: a DB carrying the legacy single-provider `model_config` is migrated
    ONCE into `provider_config` at startup; afterwards the legacy key is dead.
    """
    db_file = str(tmp_path / "migrate.db")
    runtime1 = AgentRuntime(RuntimeConfig(bot_qq=1, db_path=db_file))
    await runtime1.start()
    # First boot writes the seed provider_config
    assert await runtime1.event_store.get_dynamic_config("provider_config")
    await runtime1.stop()

    # Simulate a V1 installation: write legacy config, wipe provider_config
    runtime2 = AgentRuntime(RuntimeConfig(bot_qq=1, db_path=db_file))
    await runtime2.start()
    await runtime2.event_store.save_dynamic_config("model_config", {
        "openai_base_url": "https://legacy.example/v1",
        "default_model": "legacy-chat",
        "deliberate_model": "legacy-reasoner"
    })
    async with runtime2.event_store._write_lock:
        await runtime2.event_store._db.execute("DELETE FROM runtime_dynamic_configs WHERE key='provider_config';")
        await runtime2.event_store._db.commit()
    await runtime2.stop()

    runtime3 = AgentRuntime(RuntimeConfig(bot_qq=1, db_path=db_file))
    await runtime3.start()

    normal = runtime3.provider_registry.resolve(CognitiveTier.NORMAL)
    deliberate = runtime3.provider_registry.resolve(CognitiveTier.DELIBERATE)
    assert normal.provider_id == "default"
    assert normal.model == "legacy-chat"
    assert deliberate.model == "legacy-reasoner"
    assert normal.client.base_url.host == "legacy.example"

    # Migration persisted: deleting again would NOT change resolution semantics
    saved = await runtime3.event_store.get_dynamic_config("provider_config")
    assert saved["routing"]["normal"]["model"] == "legacy-chat"

    await runtime3.stop()


class _FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        # Snapshot the message list at call time (the live trajectory keeps growing)
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def _tool_call_response():
    message = SimpleNamespace(
        tool_calls=[SimpleNamespace(
            id="tc_1",
            function=SimpleNamespace(name="search_messages", arguments=json.dumps({"query": "直播"}))
        )],
        content=None
    )
    message.model_dump = lambda: {"role": "assistant", "content": None, "tool_calls": [
        {"id": "tc_1", "type": "function", "function": {"name": "search_messages", "arguments": '{"query": "直播"}'}}
    ]}
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def _final_response():
    outcome = EpisodeOutcome(
        disposition=FinalDisposition.SILENCE,
        decision_reason="done after heavy evidence"
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=None, content=outcome.model_dump_json()))],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=45)
    )


class _FakeToolkit:
    def get_tool_definitions(self):
        return [{"type": "function", "function": {"name": "search_messages", "parameters": {}}}]

    async def execute(self, name, args):
        return "证据" * 800  # 1600 chars > 1200 → complexity trigger


@pytest.mark.asyncio
async def test_scenario_k_escalation_persona_continuity_and_metrics(tmp_path):
    """
    Scenario K (Goal 10 — 模型升级不能人格切换) + ADR-0020 metrics:
    Mid-episode escalation Normal→Deliberate swaps ONLY the model: the exact same
    working_messages trajectory (identity + tool history) carries over. Every call
    is recorded in routing metrics with latency/tokens; the escalation reason too.
    """
    fake_normal_completions = _FakeCompletions([_tool_call_response()])
    fake_deliberate_completions = _FakeCompletions([_final_response()])

    class FakeRegistry:
        def resolve(self, tier):
            if tier == CognitiveTier.DELIBERATE:
                return SimpleNamespace(provider_id="p2", model="reasoner-model",
                                       client=SimpleNamespace(chat=SimpleNamespace(completions=fake_deliberate_completions)))
            return SimpleNamespace(provider_id="p1", model="chat-model",
                                   client=SimpleNamespace(chat=SimpleNamespace(completions=fake_normal_completions)))

    metrics = RuntimeMetrics()
    core = ReActAgentCore(
        RuntimeConfig(bot_qq=1),
        registry=FakeRegistry(),
        metrics=metrics,
    )
    mailbox = EpisodeMailbox("ep_k", "group:x", 0)
    system_prompt = [{"role": "system", "content": "【IDENTITY】你是 Len"}]

    outcome, _trace = await core.execute_episode(system_prompt + [{"role": "user", "content": "查一下"}], mailbox, toolkit=_FakeToolkit())
    assert outcome.disposition == FinalDisposition.SILENCE

    # Step trace records the tier/provider/model chain (ADR-0022)
    assert [s["tier"] for s in _trace["steps"]] == ["normal", "deliberate"]
    assert _trace["steps"][0]["model"] == "chat-model"
    assert _trace["steps"][1]["model"] == "reasoner-model"
    assert _trace["escalations"] and "tool_payload_length" in _trace["escalations"][0]["reason"]

    # Two calls: first on normal model, second (after escalation) on deliberate model
    assert len(fake_normal_completions.calls) == 1
    assert len(fake_deliberate_completions.calls) == 1
    assert fake_normal_completions.calls[0]["model"] == "chat-model"
    assert fake_deliberate_completions.calls[0]["model"] == "reasoner-model"

    # Persona continuity: the same system prompt object and trajectory carried over,
    # extended only by the assistant tool-call message and the tool result.
    first_msgs = fake_normal_completions.calls[0]["messages"]
    second_msgs = fake_deliberate_completions.calls[0]["messages"]
    assert second_msgs[0] is first_msgs[0]  # identical identity block
    assert len(second_msgs) == len(first_msgs) + 2
    assert second_msgs[-1]["role"] == "tool"

    # Routing metrics recorded per (tier, provider, model)
    snap = metrics.snapshot()
    routes = {(r["tier"], r["provider_id"], r["model"]): r for r in snap["routes"]}
    assert routes[("normal", "p1", "chat-model")]["calls"] == 1
    assert routes[("normal", "p1", "chat-model")]["prompt_tokens"] == 0  # usage None on tool call
    assert routes[("deliberate", "p2", "reasoner-model")]["calls"] == 1
    assert routes[("deliberate", "p2", "reasoner-model")]["prompt_tokens"] == 120
    assert routes[("deliberate", "p2", "reasoner-model")]["completion_tokens"] == 45
    assert snap["escalation_total"] == 1
    assert "tool_payload_length" in " ".join(snap["recent_escalation_reasons"])


@pytest.mark.asyncio
async def test_retrieval_tools_emit_complexity_marker(tmp_path):
    """
    ADR-0020: [COMPLEXITY: HIGH] has a real producer — retrieval tools append the
    marker when the evidence set is large, feeding the escalation heuristic.
    """
    db_file = str(tmp_path / "marker.db")
    runtime = AgentRuntime(RuntimeConfig(bot_qq=1, db_path=db_file))
    await runtime.start()

    scene_id = "group:marker"
    t0 = time.time()
    for i in range(10):
        ev = Event(
            event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id=scene_id,
            actor_id=f"user:{i}",
            timestamp=t0 + i,
            payload={"raw_text": f"直播相关历史消息 {i}"}
        )
        await runtime.receive_event(ev)
    await asyncio.sleep(0.3)

    from len_bot.tools.retrieval import RetrievalToolkit
    toolkit = RetrievalToolkit(
        event_store=runtime.event_store,
        allowed_scopes=[scene_id, "global-safe"],
        default_scene_id=scene_id
    )
    result = await toolkit.execute("search_messages", {"query": "直播"})
    assert "[COMPLEXITY: HIGH]" in result

    await runtime.stop()


@pytest.mark.asyncio
async def test_llm_error_recorded_in_provider_metrics(tmp_path):
    """ADR-0020: LLM call failures land in per-provider error counters."""
    class FailingCompletions:
        async def create(self, **kwargs):
            raise RuntimeError("provider down")

    class FailingRegistry:
        def resolve(self, tier):
            return SimpleNamespace(provider_id="p_err", model="m_err",
                                   client=SimpleNamespace(chat=SimpleNamespace(completions=FailingCompletions())))

    metrics = RuntimeMetrics()
    core = ReActAgentCore(RuntimeConfig(bot_qq=1), registry=FailingRegistry(), metrics=metrics)
    mailbox = EpisodeMailbox("ep_err", "group:x", 0)
    outcome, _trace = await core.execute_episode([{"role": "user", "content": "hi"}], mailbox)

    # Failure surfaces as a SILENCE outcome (never an exception to the runtime)
    assert outcome.disposition == FinalDisposition.SILENCE
    snap = metrics.snapshot()
    assert snap["provider_errors"]["p_err"] == 1
    routes = {(r["tier"], r["provider_id"], r["model"]): r for r in snap["routes"]}
    assert routes[("normal", "p_err", "m_err")]["errors"] == 1
