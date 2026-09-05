import asyncio
import time

import pytest

from len_bot.actions.models import ActionItem
from len_bot.cognition.session import (
    SelfSocialStateUpdate,
    SocialCognitionResult,
    SocialDecision,
    SocialDecisionAction,
    SocialMessageProposal,
    SocialPerception,
    SocialTaskProposal,
    SocialWorldPatch,
    TopicState,
)
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.runtime.agent_runtime import AgentRuntime


def result(
    *,
    summary: str,
    reason: str,
    world: SocialWorldPatch | None = None,
    content: str | None = None,
    tasks: list[SocialTaskProposal] | None = None,
) -> SocialCognitionResult:
    action = SocialDecisionAction.SPEAK if content else SocialDecisionAction.SILENCE
    return SocialCognitionResult(
        perception=SocialPerception(
            summary=summary,
            world_patch=world or SocialWorldPatch(),
        ),
        self_state=SelfSocialStateUpdate(
            engagement="participating" if content else "observing",
            social_position="participant" if content else "observer",
            current_interest="medium",
            inclination_to_speak="high" if content else "low",
        ),
        decision=SocialDecision(action=action, reason=reason),
        message_proposals=[SocialMessageProposal(content=content)] if content else [],
        task_proposals=tasks or [],
    )


async def wait_for_cognition(runtime: AgentRuntime, scene_id: str, count: int = 1) -> None:
    for _ in range(200):
        traces = await runtime.event_store.query_traces(
            scene_id=scene_id,
            kind="social_cognition",
            limit=100,
        )
        if len(traces) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"social cognition did not reach {count} trace(s)")


@pytest.mark.asyncio
async def test_direct_mention_and_implicit_continuation_use_one_social_core(tmp_path):
    sent: list[ActionItem] = []
    prompts: list[str] = []

    async def send(action: ActionItem) -> bool:
        sent.append(action)
        return True

    async def social_core(messages):
        prompt = messages[-1]["content"]
        prompts.append(prompt)
        if "今晚播吗" in prompt.split("【CURRENT BURST】")[-1]:
            return result(
                summary="A 在问今晚是否直播",
                reason="明确询问",
                content="应该播",
                world=SocialWorldPatch(
                    open_topics=[TopicState(id="live", subject="今晚直播", participants=["user:A"])]
                ),
            )
        assert '"subject": "今晚直播"' in prompt
        return result(
            summary="没有关键词，但这是对今晚直播迟迟未开的自然延续",
            reason="顺着刚才的话题接一句",
            content="确实有点晚了",
            world=SocialWorldPatch(
                open_topics=[TopicState(id="live", subject="今晚直播", participants=["user:A"])]
            ),
        )

    runtime = AgentRuntime(
        RuntimeConfig(bot_qq=42, db_path=str(tmp_path / "continuity.db"), debounce_idle_ms=10),
        send_adapter=send,
        mock_social_handler=social_core,
    )
    await runtime.start()
    scene_id = "group:continuity"

    await runtime.receive_event(Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        payload={"raw_text": "@Bot 今晚播吗", "at_bot": True},
    ))
    await wait_for_cognition(runtime, scene_id)
    await runtime.receive_event(Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        payload={"raw_text": "怎么还没来"},
    ))
    await wait_for_cognition(runtime, scene_id, 2)
    await asyncio.sleep(0.05)

    assert [action.content for action in sent] == ["应该播", "确实有点晚了"]
    assert len(prompts) == 2
    await runtime.stop()


@pytest.mark.asyncio
async def test_fast_human_banter_is_understood_then_intentionally_silent(tmp_path):
    calls = 0

    async def social_core(_messages):
        nonlocal calls
        calls += 1
        return result(
            summary="A 和 B 正在高速互相接梗",
            reason="看懂了，但没有自然插话位置",
            world=SocialWorldPatch(
                mood="playful",
                activity="active",
                social_dynamics_add=["A 和 B 正在互相接梗"],
            ),
        )

    runtime = AgentRuntime(
        RuntimeConfig(
            bot_qq=42,
            db_path=str(tmp_path / "banter.db"),
            debounce_idle_ms=20,
            debounce_max_ms=100,
        ),
        mock_social_handler=social_core,
    )
    await runtime.start()
    scene_id = "group:banter"
    for actor, text in [("user:A", "你又来"), ("user:B", "经典"), ("user:A", "少来")]:
        await runtime.receive_event(Event(
            event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id=scene_id,
            actor_id=actor,
            payload={"raw_text": text},
        ))
    await wait_for_cognition(runtime, scene_id)

    session = runtime.scene_manager.get_group_session(scene_id)
    assert calls == 1
    assert session.social_world.mood == "playful"
    assert runtime.metrics.social["intentional_silence"] == 1
    assert runtime.metrics.social["gate_action"] == 0
    await runtime.stop()


@pytest.mark.asyncio
async def test_explicit_action_proposal_reaches_deterministic_task_commit(tmp_path):
    async def social_core(_messages):
        return result(
            summary="用户明确要求十分钟后提醒",
            reason="明确行动请求需要确认",
            content="行，十分钟后叫你",
            tasks=[SocialTaskProposal(description="提醒用户回来", due_at=time.time()+600, proposal_id="wake", source_event_ids=[request_event.id])],
        )

    runtime = AgentRuntime(
        RuntimeConfig(bot_qq=42, db_path=str(tmp_path / "task.db")),
        mock_social_handler=social_core,
    )
    await runtime.start()
    scene_id = "group:task"
    request_event = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        payload={"raw_text": "@Bot 十分钟后叫我", "at_bot": True},
    )
    await runtime.receive_event(request_event)
    await wait_for_cognition(runtime, scene_id)

    tasks = await runtime.event_store.get_pending_tasks()
    assert len(tasks) == 1
    assert tasks[0]["scene_id"] == scene_id
    assert tasks[0]["description"] == "提醒用户回来"
    await runtime.stop()


@pytest.mark.asyncio
async def test_plugin_fact_enters_social_core_without_attention_prefilter(tmp_path):
    seen: list[str] = []

    async def social_core(messages):
        prompt = messages[-1]["content"]
        seen.append(prompt)
        return result(summary="直播状态发生变化", reason="群里没有人在等，保持沉默")

    runtime = AgentRuntime(
        RuntimeConfig(bot_qq=42, db_path=str(tmp_path / "plugin.db")),
        mock_social_handler=social_core,
    )
    await runtime.start()
    scene_id = "group:plugin"
    await runtime.receive_event(Event(
        event_type=EventType.LIVE_STARTED,
        scene_id=scene_id,
        actor_id="plugin:bilibili",
        payload={"raw_text": "主播开播了", "room_id": 1},
    ))
    await wait_for_cognition(runtime, scene_id)

    assert len(seen) == 1
    assert "主播开播了" in seen[0]
    assert runtime.metrics.social["intentional_silence"] == 1
    await runtime.stop()


@pytest.mark.asyncio
async def test_stale_social_result_is_not_sent_and_latest_context_is_recognized(tmp_path):
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0
    sent: list[ActionItem] = []

    async def send(action: ActionItem) -> bool:
        sent.append(action)
        return True

    async def social_core(messages):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await release_first.wait()
            return result(summary="准备回答旧问题", reason="旧答案", content="我查到了")
        assert "已经有人说了" in messages[-1]["content"]
        return result(summary="群友已经解决问题", reason="无需重复回答")

    runtime = AgentRuntime(
        RuntimeConfig(bot_qq=42, db_path=str(tmp_path / "stale.db"), debounce_idle_ms=10),
        send_adapter=send,
        mock_social_handler=social_core,
    )
    await runtime.start()
    scene_id = "group:stale"
    await runtime.receive_event(Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        payload={"raw_text": "@Bot 帮我查下", "at_bot": True},
    ))
    await first_started.wait()
    await runtime.receive_event(Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:B",
        payload={"raw_text": "已经有人说了"},
    ))
    await asyncio.sleep(0.05)
    release_first.set()
    await wait_for_cognition(runtime, scene_id)

    assert calls >= 2
    assert sent == []
    traces = await runtime.event_store.query_traces(scene_id=scene_id, kind="social_cognition")
    assert traces[0]["payload"]["cognition"]["interim_batches"] >= 1
    await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_gate_keeps_only_hard_anti_loop_ceiling(tmp_path):
    sent: list[ActionItem] = []

    async def send(action: ActionItem) -> bool:
        sent.append(action)
        return True

    async def social_core(_messages):
        return result(summary="仍想发言", reason="有内容可说", content="第五句")

    runtime = AgentRuntime(
        RuntimeConfig(bot_qq=42, db_path=str(tmp_path / "ceiling.db")),
        send_adapter=send,
        mock_social_handler=social_core,
    )
    await runtime.start()
    scene_id = "group:ceiling"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    actor.state.consecutive_bot_messages = 4
    await runtime.receive_event(Event(
        event_type=EventType.TASK_DUE,
        scene_id=scene_id,
        actor_id="system:scheduler",
        payload={"raw_text": "继续说"},
    ))
    await wait_for_cognition(runtime, scene_id)

    assert sent == []
    traces = await runtime.event_store.query_traces(scene_id=scene_id, kind="social_cognition")
    assert "hard anti-loop ceiling" in traces[0]["payload"]["gate"]["reason"]
    await runtime.stop()
