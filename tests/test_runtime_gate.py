import pytest
import asyncio
import time
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.actions.models import ActionItem
from len_bot.actions.queue import ActionQueue
from len_bot.scenes.models import SceneState
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal, TaskProposal
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.runtime.gate import RuntimeGate

@pytest.mark.asyncio
async def test_gate_two_phase_commit_and_open_loop(tmp_path):
    db_file = str(tmp_path / "test_gate.db")
    store = EventStore(db_file)
    await store.initialize()

    sent_actions: list[ActionItem] = []
    async def mock_send(item: ActionItem) -> bool:
        sent_actions.append(item)
        return True

    action_queue = ActionQueue(store, send_adapter=mock_send)
    await action_queue.start()

    gate = RuntimeGate(store, action_queue)
    scene_state = SceneState(scene_id="group:1", version=10)
    mailbox = EpisodeMailbox("ep_1", "group:1", 10)

    # The agent outputs an action asking A for arrival time, plus a future task
    outcome = EpisodeOutcome(
        disposition=FinalDisposition.ACTION,
        decision_reason="Ask A arrival time and set checking task",
        message_proposals=[
            MessageProposal(
                content="你几点来？",
                expect_reply=True,
                reply_target="user:A",
                reply_intent="arrival_time"
            )
        ],
        task_proposals=[
            TaskProposal(description="19:55 查开播", delay_seconds=600)
        ]
    )

    decision = await gate.evaluate_and_commit(outcome, mailbox, scene_state)
    assert decision.disposition == FinalDisposition.ACTION

    # Wait for ActionQueue worker to process send & dual-write Open Loop
    await asyncio.sleep(0.1)

    # 1. Message was sent
    assert len(sent_actions) == 1
    assert sent_actions[0].content == "你几点来？"

    # 2. Open Loop is committed to SQLite
    loops = await store.get_active_open_loops("group:1")
    assert len(loops) == 1
    assert loops[0]["target_actor_id"] == "user:A"
    assert loops[0]["intent"] == "arrival_time"

    await action_queue.stop()
    await store.close()

@pytest.mark.asyncio
async def test_gate_staleness_cancellation(tmp_path):
    db_file = str(tmp_path / "test_stale.db")
    store = EventStore(db_file)
    await store.initialize()

    action_queue = ActionQueue(store)
    gate = RuntimeGate(store, action_queue)
    scene_state = SceneState(scene_id="group:1", version=10)
    mailbox = EpisodeMailbox("ep_1", "group:1", 10)

    # User cancels while episode was running
    cancel_event = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:1",
        actor_id="user:A",
        timestamp=time.time(),
        payload={"raw_text": "算了不用查了"}
    )
    mailbox.post(cancel_event)

    outcome = EpisodeOutcome(
        disposition=FinalDisposition.ACTION,
        decision_reason="I found the live stream",
        message_proposals=[MessageProposal(content="八点开播")]
    )

    decision = await gate.evaluate_and_commit(outcome, mailbox, scene_state)
    # Must be rejected and converted to SILENCE! (ADR-0002)
    assert decision.disposition == FinalDisposition.SILENCE
    assert "Gate rejected" in decision.reason

    await store.close()
