"""V5 Stage 2 (ADR-0038): layered persona — group register, FAST session
application, deferred merge-only patches, voice exemplars, prompt composition.
"""

import asyncio

import pytest

from len_bot.config import RuntimeConfig
from len_bot.cognition.session import (
    GroupAgentSession,
    GroupAgentSessionReducer,
    GroupRegisterState,
    SocialWorldPatch,
    TopicState,
)
from len_bot.testing.social import social_result
from len_bot.cognition.social_core import SocialCoreContextAssembler
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.events.store import EventStore
from len_bot.testing.scenario_runner import ScenarioRunner


def _human_event(scene_id: str, actor: str, text: str) -> Event:
    return Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id=actor,
        payload={"raw_text": text, "sender": {"nickname": actor}},
    )


def test_group_register_counts_human_message_style():
    session = GroupAgentSession(scene_id="group:r")
    reducer = GroupAgentSessionReducer
    for text in ["？", "草", "笑死我了哈哈哈哈", "这个真的可以吗？", "6"]:
        session = reducer.reduce(session, _human_event("group:r", "user:1", text), "user:bot")

    register = session.group_register
    assert register.sample_size == 5
    assert register.question_count == 2
    assert register.fragment_count >= 2
    assert register.short_reactions.get("草") == 1
    rendered = register.render()
    assert "中位长度" in rendered
    assert "“草”×1" in rendered


def test_bot_own_messages_do_not_pollute_register():
    session = GroupAgentSession(scene_id="group:r")
    bot_event = Event(
        event_type=EventType.MESSAGE_SENT,
        scene_id="group:r",
        actor_id="user:bot",
        payload={"raw_text": "哈哈哈哈哈哈long"},
    )
    session = GroupAgentSessionReducer.reduce(session, bot_event, "user:bot")
    assert session.group_register.sample_size == 0


def test_apply_fast_cognition_advances_cursor_without_touching_social_world():
    session = GroupAgentSession(scene_id="group:f")
    session.social_world.mood = "热闹"
    session.last_cognized_event_rowid = 3

    updated = GroupAgentSessionReducer.apply_cognition(session, social_result(reason="state unchanged"), through_event_rowid=9)

    assert updated.last_cognized_event_rowid == 9
    assert updated.social_world.mood == "热闹"
    assert updated.version == session.version + 1


def test_deferred_patch_merges_never_replaces():
    session = GroupAgentSession(scene_id="group:p")
    session.social_world.topics = [
        TopicState(id="topic:e1", subject="旧话题", status="active"),
        TopicState(id="topic:e2", subject="进行中", status="active"),
    ]
    session.social_world.social_dynamics = ["互怼"]

    patch = SocialWorldPatch.model_validate({
        "mood": "上头",
        "open_topics": [{"id": "topic:e3", "subject": "删库事件", "context": "有人删库"}],
        "close_topic_ids": ["topic:e1"],
        "social_dynamics_add": ["互怼", "玩梗"],
        "group_identity": {"familiarity": "familiar", "common_topics_add": ["游戏"]},
    })
    updated = GroupAgentSessionReducer.apply_deferred_patch(session, patch)

    by_id = {t.id: t for t in updated.social_world.topics}
    assert by_id["topic:e1"].status == "closed"
    assert by_id["topic:e2"].status == "active"
    assert by_id["topic:e3"].subject == "删库事件"
    assert updated.social_world.mood == "上头"
    assert updated.social_world.social_dynamics == ["互怼", "玩梗"]
    assert updated.group_identity.familiarity == "familiar"
    assert updated.group_identity.common_topics == ["游戏"]


def test_reducer_applies_deferred_patch_event_through_lawful_path():
    session = GroupAgentSession(scene_id="group:e")
    event = Event(
        event_type=EventType.REFLECTION_RECORDED,
        scene_id="group:e",
        actor_id="system:reflection",
        payload={
            "social_revision": 0,
            "patch": {"mood": "安静", "social_dynamics_add": ["夜聊"]},
        },
    )
    updated = GroupAgentSessionReducer.reduce(session, event, "user:bot")
    assert updated.social_world.mood == "安静"
    assert updated.social_world.social_dynamics == ["夜聊"]
    # A malformed patch event must never crash the single-writer loop.
    bad = Event(
        event_type=EventType.REFLECTION_RECORDED,
        scene_id="group:e",
        actor_id="system:reflection",
        payload={"kind": "deferred_patch", "patch": "not-a-dict"},
    )
    assert GroupAgentSessionReducer.reduce(updated, bad, "user:bot") is not None


@pytest.mark.asyncio
async def test_voice_exemplar_selection_is_stable_and_read_only(tmp_path):
    store = EventStore(str(tmp_path / "voice.db"))
    await store.initialize()
    try:
        first = await store.add_voice_example("group:v", "我操,怎么又是我", context="你代码又炸了", tag="tease")
        second = await store.add_voice_example("group:v", "？")
        await store.add_voice_example("", "你最好是在开玩笑")  # global

        picked = await store.select_voice_examples("group:v")
        assert len(picked) == 3
        assert first["id"] in [item["id"] for item in picked]
        assert any(item["content"] == "你最好是在开玩笑" for item in picked)

        picked_again = await store.select_voice_examples("group:v")
        assert picked_again == picked
        assert all(item["use_count"] == 0 for item in await store.list_voice_examples())

        await store.set_voice_example_enabled(second["id"], False)
        listed = await store.list_voice_examples("group:v")
        enabled = [item for item in listed if item["content"] == "？" and item["enabled"]]
        assert enabled == []

        assert await store.delete_voice_example(first["id"]) is True
        assert await store.delete_voice_example(first["id"]) is False
    finally:
        await store.close()


def test_fast_context_carries_persona_layers(tmp_path):
    config = RuntimeConfig(bot_qq=12345678)
    session = GroupAgentSession(scene_id="group:c")
    session.group_register.observe(session.group_register, "草")
    session.self_social_state.engagement = "chatting"

    assembler = SocialCoreContextAssembler(config)
    messages = assembler.assemble(
        session=session,
        burst=Stimulus(
            scene_id="group:c",
            stimulus_type=StimulusType.SINGLE_MESSAGE,
            source_event_ids=["e1"],
            actor_id="user:1",
            combined_text="我刚把线上库删了",
        ),
        raw_events=[],
        active_open_loops=[{"id": "loop_1", "target_actor_id": "user:1", "intent": "确认"}],
        voice_examples=[{"context": "你代码又炸了", "content": "怎么又是我"}],
    )

    system = messages[0]["content"]
    user = messages[1]["content"]
    assert "【IDENTITY CORE】" in system and "行为倾向" in system
    assert "SILENCE" in system or "silence" in system
    assert "【CURRENT SOCIAL STATE】" in user and "chatting" in user
    assert "【GROUP REGISTER】" in user
    assert "loop_1" in user
    assert "怎么又是我" in user
    assert "我刚把线上库删了" in user


def test_full_context_contains_identity_core_and_register():
    config = RuntimeConfig(bot_qq=12345678)
    session = GroupAgentSession(scene_id="group:c2")
    session.group_register.observe(session.group_register, "哈哈哈哈")
    context = SocialCoreContextAssembler(config).assemble(
        session=session,
        burst=Stimulus(
            scene_id="group:c2",
            stimulus_type=StimulusType.SINGLE_MESSAGE,
            source_event_ids=[],
            actor_id="user:1",
            combined_text="在吗",
        ),
        raw_events=[],
        active_open_loops=[],
        voice_examples=[{"content": "6"}],
    )
    system = context[0]["content"]
    user = context[1]["content"]
    assert "【IDENTITY CORE】" in system
    assert "【GROUP REGISTER】" in user
    assert "【VOICE EXAMPLES】" in user
