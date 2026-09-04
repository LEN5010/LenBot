import asyncio

import pytest

from len_bot.cognition.session import (
    SelfSocialStateUpdate,
    SocialCognitionResult,
    SocialDecision,
    SocialDecisionAction,
    SocialPerception,
    SocialMessageProposal,
    SocialWorldState,
    TopicState,
)
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.runtime.agent_runtime import AgentRuntime


def silent_result(summary: str = "understood") -> SocialCognitionResult:
    return SocialCognitionResult(
        perception=SocialPerception(
            summary=summary,
            world_state=SocialWorldState(),
        ),
        self_state=SelfSocialStateUpdate(
            engagement="observing",
            social_position="observer",
            current_interest="low",
            inclination_to_speak="low",
        ),
        decision=SocialDecision(
            action=SocialDecisionAction.SILENCE,
            reason="no opening",
        ),
    )


@pytest.mark.asyncio
async def test_social_core_shadow_updates_session_without_sending(tmp_path):
    observed_messages: list[list[dict[str, str]]] = []

    async def social_handler(messages):
        observed_messages.append(messages)
        return SocialCognitionResult(
            perception=SocialPerception(
                summary="大家仍在等今晚的直播",
                world_state=SocialWorldState(
                    mood="casual",
                    activity="active",
                    topics=[
                        TopicState(
                            id="topic_live",
                            subject="今晚直播",
                            participants=["user:A"],
                            context="还没有开播",
                        )
                    ],
                ),
            ),
            self_state=SelfSocialStateUpdate(
                engagement="observing",
                social_position="observer",
                current_interest="medium",
                inclination_to_speak="low",
            ),
            decision=SocialDecision(
                action=SocialDecisionAction.SILENCE,
                reason="目前只是群友在等待，没有自然插话位置",
            ),
        )

    runtime = AgentRuntime(
        RuntimeConfig(
            bot_qq=42,
            db_path=str(tmp_path / "social-shadow.db"),
            debounce_idle_ms=20,
        ),
        mock_social_handler=social_handler,
    )
    await runtime.start()
    await runtime.set_shadow_mode(True)
    event = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:social-core",
        actor_id="user:A",
        payload={"raw_text": "怎么还没来", "at_bot": True},
    )
    await runtime.receive_event(event)

    for _ in range(100):
        session = runtime.scene_manager.get_group_session(event.scene_id)
        if session and session.last_cognized_event_rowid:
            break
        await asyncio.sleep(0.01)

    session = runtime.scene_manager.get_group_session(event.scene_id)
    assert session.social_world.topics[0].subject == "今晚直播"
    assert session.self_social_state.engagement == "observing"
    assert session.last_cognized_event_rowid == session.last_observed_event_rowid
    assert observed_messages
    prompt = observed_messages[0][1]["content"]
    assert "【CURRENT SOCIAL STATE】" in prompt
    assert "【RECENT RAW CONVERSATION】" in prompt
    assert "怎么还没来" in prompt

    events = await runtime.event_store.get_recent_events(event.scene_id)
    assert [item.event_type for item in events].count(EventType.SOCIAL_COGNITION_RECORDED) == 1
    assert EventType.MESSAGE_SENT not in [item.event_type for item in events]
    traces = await runtime.event_store.query_traces(
        scene_id=event.scene_id, kind="social_cognition"
    )
    assert traces[0]["payload"]["result"]["decision"]["action"] == "silence"
    await runtime.stop()


def test_social_cognition_result_rejects_silence_with_message():
    with pytest.raises(ValueError, match="silence cannot contain"):
        SocialCognitionResult(
            perception=SocialPerception(
                summary="understood",
                world_state=SocialWorldState(),
            ),
            self_state=SelfSocialStateUpdate(
                engagement="observing",
                social_position="observer",
                current_interest="low",
                inclination_to_speak="low",
            ),
            decision=SocialDecision(
                action=SocialDecisionAction.SILENCE,
                reason="no opening",
            ),
            message_proposals=[SocialMessageProposal(content="should not be here")],
        )


@pytest.mark.asyncio
async def test_scene_actor_rejects_social_cognition_from_stale_observation(tmp_path):
    runtime = AgentRuntime(
        RuntimeConfig(
            bot_qq=42,
            db_path=str(tmp_path / "stale-social.db"),
            debounce_idle_ms=5_000,
        )
    )
    await runtime.start()
    scene_id = "group:stale-social"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    first = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        payload={"raw_text": "first"},
    )
    await runtime.receive_event(first)
    await actor._queue.join()
    stale_rowid = actor.group_session.last_observed_event_rowid

    second = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:B",
        payload={"raw_text": "newer"},
    )
    await runtime.receive_event(second)
    await actor._queue.join()

    accepted = await actor.submit_social_cognition(
        result=silent_result(),
        through_event_rowid=stale_rowid,
        source_event_ids=[first.id],
    )

    assert accepted is False
    assert actor.group_session.last_cognized_event_rowid == 0
    events = await runtime.event_store.get_recent_events(scene_id)
    assert EventType.SOCIAL_COGNITION_RECORDED not in [event.event_type for event in events]
    await runtime.stop()
