"""V5 Stage 1 (ADR-0038): FAST social cognition path.

Casual bursts take the one-shot FAST route; escalation and structural routing
to FULL; FAST results ride the exact same actor/gate authority paths.
"""

import asyncio

import pytest

from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.cognition.session import (
    FastCognitionResult,
    FastDecisionAction,
    SocialCognitionResult,
)
from len_bot.testing.scenario_runner import ScenarioRunner
from len_bot.testing.social import social_result


def fast_result(decision: str, reason: str = "ok", messages: list[dict] | None = None) -> dict:
    return {
        "decision": decision,
        "reason": reason,
        "messages": messages or [],
    }


@pytest.mark.asyncio
async def test_casual_burst_takes_fast_path_and_replies_short(tmp_path):
    """Casual reaction: one FAST call produces a short visible reply, no FULL call."""
    fast_calls: list[list[dict]] = []

    async def fast_handler(messages):
        fast_calls.append(messages)
        return fast_result("speak", "反应一下", [{"content": "？"}])

    runner = ScenarioRunner(config=RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "t.db"), debounce_idle_ms=20, debounce_max_ms=50), mock_fast_handler=fast_handler)
    await runner.setup()
    try:
        await runner.step_message("group:v5", user_id=1001, text="我刚把线上库删了")
        await runner.settle(0.5)

        assert len(fast_calls) == 1
        assert [action.content for action in runner.sent_actions] == ["？"]
        metrics = runner.runtime.metrics.social
        assert metrics["cognition_fast_calls"] == 1
        assert metrics["cognition_fast_speak"] == 1
        assert metrics["cognition_full_calls"] == 0
        assert metrics["bursts_total"] == 1
    finally:
        await runner.teardown()


@pytest.mark.asyncio
async def test_fast_silence_is_first_class(tmp_path):
    async def fast_handler(messages):
        return fast_result("silence", "没有插话位置")

    runner = ScenarioRunner(config=RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "t.db"), debounce_idle_ms=20, debounce_max_ms=50), mock_fast_handler=fast_handler)
    await runner.setup()
    try:
        await runner.step_message("group:v5s", user_id=1001, text="今天好冷")
        await runner.settle(0.5)

        assert runner.sent_actions == []
        metrics = runner.runtime.metrics.social
        assert metrics["cognition_fast_silence"] == 1
        assert metrics["intentional_silence"] == 1
    finally:
        await runner.teardown()


@pytest.mark.asyncio
async def test_fast_escalation_runs_full_cognition_once(tmp_path):
    fast_calls: list = []
    full_calls: list = []

    async def fast_handler(messages):
        fast_calls.append(messages)
        return fast_result("full", "需要查旧聊天记录")

    async def full_handler(messages):
        full_calls.append(messages)
        return social_result(reason="完整认知结论", content="查到了,上周你说过")

    runner = ScenarioRunner(
        config=RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "t.db"), debounce_idle_ms=20, debounce_max_ms=50),
        mock_social_handler=full_handler,
        mock_fast_handler=fast_handler,
    )
    await runner.setup()
    try:
        await runner.step_message("group:v5e", user_id=1001, text="@Bot 我们之前说到哪了", at_bot=True)
        await runner.settle(0.5)

        assert len(fast_calls) == 1
        assert len(full_calls) == 1
        assert len(runner.sent_actions) == 1
        metrics = runner.runtime.metrics.social
        assert metrics["cognition_fast_to_full"] == 1
        assert metrics["cognition_full_calls"] == 1
        # The escalated FULL result is the only one that commits: one visible message.
        assert [a.content for a in runner.sent_actions] == ["查到了,上周你说过"]
    finally:
        await runner.teardown()


@pytest.mark.asyncio
async def test_task_due_goes_structurally_to_full(tmp_path):
    full_calls: list = []
    fast_calls: list = []

    async def fast_handler(messages):
        fast_calls.append(messages)
        return fast_result("speak", "不应被调用")

    async def full_handler(messages):
        full_calls.append(messages)
        return social_result(reason="任务语义需要完整认知")

    runner = ScenarioRunner(
        config=RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "t.db"), debounce_idle_ms=20, debounce_max_ms=50),
        mock_social_handler=full_handler,
        mock_fast_handler=fast_handler,
    )
    await runner.setup()
    try:
        # A next-wake TASK_DUE burst must skip FAST structurally.
        due_event = Event(
            event_type=EventType.TASK_DUE,
            scene_id="group:v5t",
            actor_id="system:scheduler",
            payload={"task_id": "task_v5", "description": "看看群里", "kind": "next_wake"},
            metadata={"origin_mode": "live"},
        )
        await runner.runtime.receive_event(due_event)
        await runner.settle(0.5)

        assert fast_calls == []
        assert len(full_calls) == 1
        assert runner.runtime.metrics.social["cognition_fast_calls"] == 0
    finally:
        await runner.teardown()


@pytest.mark.asyncio
async def test_fast_speak_with_expected_reply_activates_loop_only_on_send(tmp_path):
    async def fast_handler(messages):
        return fast_result(
            "speak",
            "问一句",
            [{"content": "你删的哪个库？", "expect_reply": True, "reply_target": "user:1001", "reply_intent": "线上库"}],
        )

    runner = ScenarioRunner(config=RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "t.db"), debounce_idle_ms=20, debounce_max_ms=50), mock_fast_handler=fast_handler)
    await runner.setup()
    try:
        await runner.step_message("group:v5l", user_id=1001, text="我好像把线上库删了")
        await runner.settle(0.5)
        assert len(runner.sent_actions) == 1

        loops = await runner.runtime.event_store.get_active_open_loops("group:v5l")
        if runner.sent_actions:
            # MESSAGE_SENT commits the loop atomically with the sent event.
            assert loops, "open loop must be activated after physical send"
            assert loops[0]["target_actor_id"] == "user:1001"
            assert loops[0]["source_stimulus_id"], "ADR-0029 provenance must be recorded"
    finally:
        await runner.teardown()


@pytest.mark.asyncio
async def test_style_guard_triggers_single_corrective_retry(tmp_path):
    calls: list[list[dict]] = []

    async def fast_handler(messages):
        calls.append(messages)
        if len(calls) == 1:
            return fast_result("speak", "复读", [{"content": "草哈哈哈哈"}])
        return fast_result("speak", "换个说法", [{"content": "6"}])

    runner = ScenarioRunner(config=RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "t.db"), debounce_idle_ms=20, debounce_max_ms=50), mock_fast_handler=fast_handler)
    await runner.setup()
    try:
        # Recent bot history already contains the same opener twice.
        runner.runtime.style_guard.observe("group:v5r", "草哈哈哈哈哈")
        runner.runtime.style_guard.observe("group:v5r", "草哈哈哈哈哈哈")
        runner.runtime.style_guard.observe("group:v5r", "没事")

        await runner.step_message("group:v5r", user_id=1001, text="笑死我了")
        await runner.settle(0.5)

        assert len(calls) == 2, "strong anomaly must trigger exactly one corrective retry"
        assert "风格守卫" in calls[1][-1]["content"]
        assert [a.content for a in runner.sent_actions] == ["6"]
        metrics = runner.runtime.metrics.social
        assert metrics["style_slop_flags"] == 1
        assert metrics["style_retries"] == 1
    finally:
        await runner.teardown()


@pytest.mark.asyncio
async def test_stale_fast_result_rejected_by_actor(tmp_path):
    """Same cursor staleness contract as FULL: a fast result computed against an
    outdated observation cursor must be rejected."""
    from len_bot.scenes.actor import SceneActor
    from len_bot.events.store import EventStore

    store = EventStore(str(tmp_path / "actor.db"))
    await store.initialize()
    actor = SceneActor("group:stale", "user:1", store)
    await actor.start()
    try:
        event = Event(
            event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id="group:stale",
            actor_id="user:2",
            payload={"raw_text": "hello"},
        )
        await store.commit_scene_event(
            event=event,
            scene_state_data={"scene_id": "group:stale", "version": 1},
            group_session_data={"scene_id": "group:stale"},
        )
        actor.group_session.last_observed_event_rowid = 5

        result = FastCognitionResult.model_validate(fast_result("speak", "r", [{"content": "hi"}]))
        accepted = await actor.submit_fast_cognition(
            result=result, through_event_rowid=4, source_event_ids=["e1"], mode="live"
        )
        assert accepted is False
    finally:
        await actor.stop()
        await store.close()
