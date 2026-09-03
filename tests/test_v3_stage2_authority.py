import pytest
import asyncio
import time
from pydantic import ValidationError

from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.events.models import Event, EventType
from len_bot.cognition.models import (
    EpisodeOutcome, FinalDisposition, MessageProposal, TaskProposal,
    SocialStateProposal, ThreadTransition
)
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.scenes.models import SceneState, ParticipationThread, ThreadStatus
from len_bot.memory.models import MemoryProposal, MemoryKind, MemoryCertainty

@pytest.mark.asyncio
async def test_cross_scene_open_loop_rollback(tmp_path):
    """ADR-0025: Attempting to resolve an open loop belonging to another scene must rollback the transaction."""
    db_file = str(tmp_path / "cross_scene_loop.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_1 = "group:scene_1"
    scene_2 = "group:scene_2"
    actor_1 = await runtime.scene_manager.get_or_create_actor(scene_1)

    # 1. Seed an open loop in scene_2
    loop_in_scene_2 = "loop_scene_2_secret"
    await runtime.event_store.save_open_loop({
        "id": loop_in_scene_2,
        "scene_id": scene_2,
        "target_actor_id": "user:200",
        "intent": "private_matter",
        "source_event_id": "ev_s2",
        "status": "active",
        "created_at": time.time(),
        "expires_at": time.time() + 3600
    })

    # 2. Episode in scene_1 attempts to resolve loop_in_scene_2
    outcome = EpisodeOutcome(
        disposition=FinalDisposition.ACTION,
        decision_reason="Attempt cross-scene loop hijack",
        message_proposals=[MessageProposal(content="Hijack attempt")],
        task_proposals=[TaskProposal(description="Hijack task", delay_seconds=100)],
        resolve_open_loop_ids=[loop_in_scene_2]
    )
    mailbox = EpisodeMailbox("ep_hijack", scene_1, base_scene_version=actor_1.state.version)

    decision = await actor_1.submit_proposal(
        episode_id="ep_hijack",
        outcome=outcome,
        mailbox=mailbox,
        runtime_gate=runtime.runtime_gate
    )

    # 3. Gate must reject and rollback!
    assert decision.disposition == FinalDisposition.SILENCE
    assert decision.accepted is False
    assert "rollback" in decision.reason.lower() or "not active or does not belong" in decision.reason.lower()

    # Verify open loop in scene_2 remains ACTIVE!
    loops = await runtime.event_store.get_active_open_loops(scene_2)
    assert len(loops) == 1
    assert loops[0]["id"] == loop_in_scene_2
    assert loops[0]["status"] == "active"

    # Tasks table must have 0 tasks (atomic rollback)
    pending_tasks = await runtime.event_store.get_pending_tasks()
    assert len(pending_tasks) == 0

    await runtime.stop()


def test_social_state_proposal_validation():
    """ADR-0025: SocialStateProposal strictly validates thread_transition via StrEnum."""
    # Valid transitions
    p1 = SocialStateProposal(topic="gaming", thread_transition=ThreadTransition.CLOSE)
    assert p1.thread_transition == "close"

    p2 = SocialStateProposal(thread_transition="fade")
    assert p2.thread_transition == ThreadTransition.FADE

    # Invalid transition raises ValidationError at Pydantic layer
    with pytest.raises(ValidationError):
        SocialStateProposal(thread_transition="invalid_transition_status")


@pytest.mark.asyncio
async def test_gate_rejection_emits_zero_social_state(tmp_path):
    """ADR-0025: Gate rejection produces zero social state annotations in SceneActor."""
    db_file = str(tmp_path / "zero_side_effect.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:test_zero"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    actor.state.active_topic = "original_topic"

    mailbox = EpisodeMailbox("ep_cancelled", scene_id, base_scene_version=actor.state.version)
    mailbox._cancelled = True
    mailbox._cancellation_reason = "User said cancel"

    outcome = EpisodeOutcome(
        disposition=FinalDisposition.ACTION,
        decision_reason="Should not apply",
        message_proposals=[MessageProposal(content="Hello")],
        social_state_proposal=SocialStateProposal(topic="new_topic", thread_transition=ThreadTransition.CLOSE)
    )

    decision = await actor.submit_proposal(
        episode_id="ep_cancelled",
        outcome=outcome,
        mailbox=mailbox,
        runtime_gate=runtime.runtime_gate
    )

    assert decision.accepted is False
    # Scene topic must still be original_topic!
    assert actor.state.active_topic == "original_topic"

    await runtime.stop()
