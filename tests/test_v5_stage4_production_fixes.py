"""V5 Stage 4 (ADR-0038 production feedback fixes):

1. FAST may autonomously send multiple short messages; omitted optional
   message fields must parse (production truncation fix).
2. A stale/preempted FULL re-run keeps FULL depth instead of downgrading to
   FAST (the "?/何意味" answer loss).
3. Unfulfilled promises detected by reflection become task PROPOSALS committed
   through the RuntimeGate (Invariant 4 refined — reflection still executes
   nothing itself).
"""

import asyncio

import pytest

from len_bot.config import RuntimeConfig
from len_bot.cognition.models import TaskProposal
from len_bot.cognition.session import FastDecisionAction
from len_bot.memory.models import EpisodeRecord
from len_bot.testing.scenario_runner import ScenarioRunner
from len_bot.testing.social import social_result


@pytest.mark.asyncio
async def test_fast_sends_multiple_messages_autonomously(tmp_path):
    """FAST decides the message count itself; two short messages both go out."""
    async def fast_handler(messages):
        return {
            "decision": FastDecisionAction.SPEAK,
            "reason": "两个人都在等我,分别回",
            # Only `content` — omitted optional fields must parse fine.
            "messages": [{"content": "24点？我直接通宵守着你"}, {"content": "急了？"}],
        }

    runner = ScenarioRunner(
        config=RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "t.db"), debounce_idle_ms=20, debounce_max_ms=50),
        mock_fast_handler=fast_handler,
    )
    await runner.setup()
    try:
        await runner.step_message("group:m1", user_id=1001, text="[@bot] 早上24点叫我起床", at_bot=True)
        await runner.settle(0.5)
        assert [a.content for a in runner.sent_actions] == ["24点？我直接通宵守着你", "急了？"]
        assert runner.runtime.metrics.social["gate_action"] == 1
    finally:
        await runner.teardown()


@pytest.mark.asyncio
async def test_stale_full_rerun_keeps_full_depth(tmp_path):
    """A stale FULL result re-runs as FULL on the merged burst — the deeper
    judgement must not be lost to a FAST downgrade."""
    fast_calls: list = []
    full_calls: list = []
    first_full_started = asyncio.Event()
    release_full = asyncio.Event()

    async def fast_handler(messages):
        fast_calls.append(messages)
        return {"decision": FastDecisionAction.FULL, "reason": "需要完整认知", "messages": []}

    async def full_handler(messages):
        full_calls.append(messages)
        if len(full_calls) == 1:
            first_full_started.set()
            await release_full.wait()
        return social_result(reason="完整结论", content="完整重跑回答")

    runner = ScenarioRunner(
        config=RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "t.db"), debounce_idle_ms=20, debounce_max_ms=50),
        mock_social_handler=full_handler,
        mock_fast_handler=fast_handler,
    )
    await runner.setup()
    scene_id = "group:depth"
    try:
        await runner.step_message(scene_id, user_id=1001, text="？何意味", at_bot=False)
        await asyncio.wait_for(first_full_started.wait(), timeout=5.0)

        # Newer human events advance the observation cursor while FULL thinks.
        # Non-mention message: exercises the stale-rejection path (no preemption).
        await runner.step_message(scene_id, user_id=1002, text="修一下就睡觉")
        await asyncio.sleep(0.3)
        release_full.set()
        await runner.settle(0.8)

        assert len(fast_calls) == 1, "escalation FAST must run exactly once"
        assert len(full_calls) == 2, "the merged re-run must keep FULL depth"
        assert any(a.content == "完整重跑回答" for a in runner.sent_actions)
        assert runner.runtime.metrics.social["stale_outcomes_rejected"] >= 1
    finally:
        await runner.teardown()


@pytest.mark.asyncio
async def test_reflection_deferred_task_committed_via_gate(tmp_path):
    """Reflector-detected unfulfilled promise becomes a durable task through
    the RuntimeGate's atomic task path."""
    runner = ScenarioRunner(
        config=RuntimeConfig(
            bot_qq=12345678,
            db_path=str(tmp_path / "t.db"),
            debounce_idle_ms=20,
            debounce_max_ms=50,
            reflection_quiet_window_seconds=999.0,
        ),
        mock_fast_handler=lambda messages: _fast_silence(),
    )
    await runner.setup()
    scene_id = "group:promise"

    async def stub_reflector(events):
        episode = EpisodeRecord(
            scene_id=scene_id,
            title="承诺",
            summary="Bot 答应明早叫人起床",
            source_event_ids=[e.id for e in events],
            participants=[],
            tags=[],
            created_at=0.0,
        )
        deferred = TaskProposal(
            description="明早九点叫木水起床",
            delay_seconds=9 * 3600.0,
            payload={"source": "quiet_window_reflection"},
        )
        return episode, [], None, deferred

    try:
        await runner.step_message(scene_id, user_id=1001, text="[@bot] 早上九点叫我起床", at_bot=True)
        await runner.settle(0.4)
        runner.runtime.reflection_engine.llm_reflector = stub_reflector
        await runner.runtime._quiet_window_reflect(scene_id)
        await runner.settle(0.4)

        cursor = await runner.runtime.event_store._db.execute(
            "SELECT COUNT(*) FROM tasks WHERE payload LIKE '%deferred_reflection_task%' AND description LIKE '%木水%';"
        )
        (count,) = await cursor.fetchone()
        assert count == 1, "the deferred promise task must be durably committed"
        assert runner.runtime.metrics.social["deferred_tasks_committed"] == 1
    finally:
        await runner.teardown()


async def _fast_silence():
    return {"decision": FastDecisionAction.SILENCE, "reason": "quiet", "messages": []}
