import pytest
import asyncio
import time
from len_bot.events.models import Event, EventType
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.runtime.gate import RuntimeGate, GateDecision
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.config import RuntimeConfig
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal
from len_bot.scenes.actor import SceneActor

def test_mailbox_filters_internal_and_system_events():
    """ADR-0026, §6: mailbox.post() must only accept conversational human messages."""
    mb = EpisodeMailbox("ep_1", "group:1", base_scene_version=1)

    # 1. State annotation event -> must be discarded
    anno_ev = Event(
        event_type=EventType.STATE_ANNOTATION,
        scene_id="group:1",
        actor_id="system",
        timestamp=time.time(),
        metadata={"social_state": {"topic": "test"}}
    )
    mb.post(anno_ev)
    assert len(mb.get_interim_events()) == 0
    assert mb.has_unseen_interim() is False

    # 2. Task due event -> must be discarded
    task_ev = Event(
        event_type=EventType.TASK_DUE,
        scene_id="group:1",
        actor_id="system:scheduler",
        timestamp=time.time(),
        payload={"task_id": "t1"}
    )
    mb.post(task_ev)
    assert len(mb.get_interim_events()) == 0

    # 3. Real human message -> must be accepted
    msg_ev = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:1",
        actor_id="user:100",
        timestamp=time.time(),
        payload={"raw_text": "有人在吗"}
    )
    mb.post(msg_ev)
    assert len(mb.get_interim_events()) == 1
    assert mb.has_unseen_interim() is True

    # After reading unseen events:
    unseen = mb.fetch_unseen_interim_events()
    assert len(unseen) == 1
    assert mb.has_unseen_interim() is False


@pytest.mark.asyncio
async def test_gate_rejects_stale_when_unseen_interim_arrived(tmp_path):
    """ADR-0026, §8.2: Gate rejects outcome when unread interim messages arrived."""
    db_file = str(tmp_path / "stale_gate.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:stale_test"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    mailbox = EpisodeMailbox("ep_stale", scene_id, base_scene_version=actor.state.version)

    # Human posted a message while episode was computing
    interim_msg = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:B",
        timestamp=time.time(),
        payload={"raw_text": "已经解决了不用回了"}
    )
    mailbox.post(interim_msg)
    assert mailbox.has_unseen_interim() is True

    outcome = EpisodeOutcome(
        disposition=FinalDisposition.ACTION,
        decision_reason="Attempting to send answer",
        message_proposals=[MessageProposal(content="我来答")]
    )

    decision = await actor.submit_proposal(
        episode_id="ep_stale",
        outcome=outcome,
        mailbox=mailbox,
        runtime_gate=runtime.runtime_gate
    )

    assert decision.disposition == FinalDisposition.SILENCE
    assert decision.accepted is False
    assert runtime.metrics.social["stale_outcomes_rejected"] == 1

    await runtime.stop()
