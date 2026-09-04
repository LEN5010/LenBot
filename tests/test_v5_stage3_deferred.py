"""V5 Stage 3 (ADR-0038): deferred cognition — reflection-proposed social world
patches merge into the session through the lawful event path, off the reply
critical path, without breaking immediate-state causality.
"""

import asyncio

import pytest

from len_bot.config import RuntimeConfig
from len_bot.cognition.session import FastCognitionResult, FastDecisionAction
from len_bot.events.models import Event, EventType
from len_bot.testing.scenario_runner import ScenarioRunner
from len_bot.testing.social import social_result


@pytest.mark.asyncio
async def test_deferred_patch_event_commits_through_scene_actor(tmp_path):
    runner = ScenarioRunner(
        config=RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "t.db"), debounce_idle_ms=20, debounce_max_ms=50),
        mock_fast_handler=lambda messages: _fast_silence(),
    )
    await runner.setup()
    scene_id = "group:d1"
    try:
        await runner.step_message(scene_id, user_id=1001, text="聊聊天")
        await runner.settle(0.4)

        patch_event = Event(
            event_type=EventType.SOCIAL_COGNITION_RECORDED,
            scene_id=scene_id,
            actor_id="system:reflection",
            timestamp=__import__("time").time(),
            payload={
                "kind": "deferred_patch",
                "patch": {
                    "mood": "放松",
                    "open_topics": [
                        {"id": "topic:seed", "subject": "闲聊", "context": "群友闲聊"}
                    ],
                },
            },
            metadata={"source": "quiet_window_reflection"},
        )
        await runner.runtime.receive_event(patch_event)
        await runner.settle(0.4)

        actor = await runner.runtime.scene_manager.get_or_create_actor(scene_id)
        assert actor.group_session.social_world.mood == "放松"
        assert actor.group_session.social_world.topics[0].subject == "闲聊"

        persisted = await runner.runtime.event_store.load_group_agent_session(scene_id)
        assert persisted["social_world"]["mood"] == "放松"
    finally:
        await runner.teardown()


async def _fast_silence():
    return {"decision": FastDecisionAction.SILENCE, "reason": "deferred test", "messages": []}


@pytest.mark.asyncio
async def test_quiet_window_reflection_applies_llm_world_patch(tmp_path):
    """The reflector's SocialWorldPatch proposal is applied after the atomic
    reflection batch commit — deferred cognition never blocks the reply path."""
    from len_bot.cognition.session import SocialWorldPatch
    from len_bot.memory.models import EpisodeRecord

    runner = ScenarioRunner(
        config=RuntimeConfig(
            bot_qq=12345678,
            db_path=str(tmp_path / "t.db"),
            debounce_idle_ms=20,
            debounce_max_ms=50,
            reflection_quiet_window_seconds=999.0,  # auto-reflection must not consume the range
        ),
        mock_fast_handler=lambda messages: _fast_silence(),
    )
    await runner.setup()
    scene_id = "group:d2"

    captured: dict = {}

    async def stub_reflector(events):
        captured["event_ids"] = [e.id for e in events]
        episode = EpisodeRecord(
            scene_id=scene_id,
            title="t",
            summary="s",
            source_event_ids=[e.id for e in events],
            participants=[],
            tags=[],
            created_at=0.0,
        )
        patch = SocialWorldPatch(
            mood="上头",
            open_topics=[{
                "id": f"topic:{events[-1].id}",
                "subject": "删库",
                "context": "有人说把线上库删了",
            }],
        )
        return episode, [], patch

    try:
        await runner.step_message(scene_id, user_id=1001, text="删库跑路")
        await runner.settle(0.5)
        # Patch is grounded in real event ids captured from the reflected range.
        runner.runtime.reflection_engine.llm_reflector = stub_reflector
        await runner.runtime._quiet_window_reflect(scene_id)
        await runner.settle(0.4)

        actor = await runner.runtime.scene_manager.get_or_create_actor(scene_id)
        assert actor.group_session.social_world.mood == "上头"
        assert any(t.subject == "删库" for t in actor.group_session.social_world.topics)
        # Reflection cursor advanced — the same events are never reflected twice.
        cursor = await runner.runtime.memory_store.get_reflection_cursor(scene_id)
        assert cursor > 0
    finally:
        await runner.teardown()


@pytest.mark.asyncio
async def test_deterministic_reflection_proposes_no_patch(tmp_path):
    """Keyless fallback reflection keeps working and proposes no world patch."""
    runner = ScenarioRunner(
        config=RuntimeConfig(
            bot_qq=12345678,
            db_path=str(tmp_path / "t.db"),
            debounce_idle_ms=20,
            debounce_max_ms=50,
            reflection_quiet_window_seconds=0.2,
        ),
        mock_fast_handler=lambda messages: _fast_silence(),
    )
    await runner.setup()
    scene_id = "group:d3"
    try:
        await runner.step_message(scene_id, user_id=1001, text="普通闲聊")
        await runner.settle(0.5)
        await runner.runtime._quiet_window_reflect(scene_id)
        await runner.settle(0.3)

        actor = await runner.runtime.scene_manager.get_or_create_actor(scene_id)
        assert actor.group_session.social_world.mood == "unknown"
        cursor = await runner.runtime.memory_store.get_reflection_cursor(scene_id)
        assert cursor > 0
    finally:
        await runner.teardown()
