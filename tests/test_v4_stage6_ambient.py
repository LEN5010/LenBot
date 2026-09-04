import asyncio
import time

import pytest
from pydantic import ValidationError

from len_bot.actions.models import ActionItem
from len_bot.cognition.session import (
    NextWakeIntentProposal,
    SelfSocialStateUpdate,
    SocialCognitionResult,
    SocialDecision,
    SocialDecisionAction,
    SocialMessageProposal,
    SocialPerception,
    SocialTaskProposal,
    SocialWorldState,
)
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.runtime.agent_runtime import AgentRuntime


def cognition_result(
    *,
    reason: str,
    content: str | None = None,
    wake_reason: str | None = None,
    wake_after: float = 300.0,
) -> SocialCognitionResult:
    return SocialCognitionResult(
        perception=SocialPerception(summary="understood", world_state=SocialWorldState()),
        self_state=SelfSocialStateUpdate(
            engagement="participating" if content else "observing",
            social_position="participant" if content else "observer",
            current_interest="medium",
            inclination_to_speak="high" if content else "low",
        ),
        decision=SocialDecision(
            action=SocialDecisionAction.SPEAK if content else SocialDecisionAction.SILENCE,
            reason=reason,
        ),
        message_proposals=[SocialMessageProposal(content=content)] if content else [],
        future_attention=(
            NextWakeIntentProposal(
                reason=wake_reason,
                wake_at=time.time() + wake_after,
                source_event_ids=[],
            )
            if wake_reason
            else None
        ),
    )


async def wait_for_cognition(runtime: AgentRuntime, scene_id: str, count: int) -> list[dict]:
    for _ in range(200):
        traces = await runtime.event_store.query_traces(
            scene_id=scene_id,
            kind="social_cognition",
            limit=100,
        )
        if len(traces) >= count:
            return traces
        await asyncio.sleep(0.01)
    raise AssertionError(f"social cognition did not reach {count} trace(s)")


@pytest.mark.asyncio
async def test_next_wake_commits_as_authoritative_task_and_context_projection(tmp_path):
    prompts: list[str] = []
    calls = 0

    async def social_core(messages):
        nonlocal calls
        calls += 1
        prompts.append(messages[-1]["content"])
        if calls == 1:
            return cognition_result(
                reason="稍后再看",
                wake_reason="等直播状态变化",
                wake_after=1.0,
            )
        return cognition_result(reason="继续观察")

    runtime = AgentRuntime(
        RuntimeConfig(
            bot_qq=42,
            db_path=str(tmp_path / "next-wake.db"),
            next_wake_min_interval_seconds=60.0,
            debounce_idle_ms=10,
        ),
        mock_social_handler=social_core,
    )
    await runtime.start()
    scene_id = "group:ambient"

    await runtime.receive_event(Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        payload={"raw_text": "@Bot 等开播吧", "at_bot": True},
    ))
    await wait_for_cognition(runtime, scene_id, 1)

    pending = await runtime.event_store.get_pending_next_wake(scene_id)
    assert pending is not None
    assert pending["reason"] == "等直播状态变化"
    assert pending["wake_at"] - pending["created_at"] >= 59.0
    assert "next_wake_intent" not in runtime.scene_manager.get_group_session(scene_id).model_dump()

    await runtime.receive_event(Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:B",
        payload={"raw_text": "还没开"},
    ))
    await wait_for_cognition(runtime, scene_id, 2)
    assert pending["task_id"] in prompts[1]
    assert '"wake_at"' in prompts[1]
    await runtime.stop()


def test_generic_task_cannot_claim_reserved_next_wake_kind():
    with pytest.raises(ValidationError, match="reserved for future_attention"):
        SocialCognitionResult(
            perception=SocialPerception(summary="understood", world_state=SocialWorldState()),
            self_state=SelfSocialStateUpdate(
                engagement="observing",
                social_position="observer",
                current_interest="low",
                inclination_to_speak="low",
            ),
            decision=SocialDecision(action=SocialDecisionAction.SILENCE, reason="wait"),
            task_proposals=[
                SocialTaskProposal(
                    description="forged wake",
                    delay_seconds=1,
                    payload={"kind": "next_wake"},
                )
            ],
        )


@pytest.mark.asyncio
async def test_new_next_wake_supersedes_database_and_scheduler_state(tmp_path):
    calls = 0

    async def social_core(_messages):
        nonlocal calls
        calls += 1
        return cognition_result(
            reason="继续等",
            wake_reason=f"wake-{calls}",
            wake_after=300 + calls,
        )

    runtime = AgentRuntime(
        RuntimeConfig(
            bot_qq=42,
            db_path=str(tmp_path / "supersede.db"),
            debounce_idle_ms=10,
        ),
        mock_social_handler=social_core,
    )
    await runtime.start()
    scene_id = "group:supersede"
    for expected_count, (actor, text) in enumerate(
        (("user:A", "@Bot 等等"), ("user:B", "有变化吗")),
        start=1,
    ):
        await runtime.receive_event(Event(
            event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id=scene_id,
            actor_id=actor,
            payload={"raw_text": text, "at_bot": text.startswith("@Bot")},
        ))
        await wait_for_cognition(runtime, scene_id, expected_count)

    cursor = await runtime.event_store._db.execute(
        "SELECT status, payload FROM tasks WHERE scene_id = ? ORDER BY created_at ASC;",
        (scene_id,),
    )
    rows = await cursor.fetchall()
    assert [row[0] for row in rows] == ["cancelled", "pending"]
    queued_wakes = [
        task for task in runtime.scheduler._heap
        if task.scene_id == scene_id and task.payload.get("kind") == "next_wake"
    ]
    assert len(queued_wakes) == 1
    assert queued_wakes[0].payload["reason"] == "wake-2"
    await runtime.stop()


@pytest.mark.asyncio
async def test_next_wake_recovers_fires_in_shadow_and_cannot_self_renew(tmp_path):
    db_path = str(tmp_path / "wake-restart.db")
    config = RuntimeConfig(bot_qq=42, db_path=db_path)
    scene_id = "group:restart-wake"

    async def first_core(_messages):
        return cognition_result(
            reason="稍后回来",
            wake_reason="看看直播是否开始",
            wake_after=300,
        )

    first = AgentRuntime(config, mock_social_handler=first_core)
    await first.start()
    await first.set_shadow_mode(True)
    await first.receive_event(Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        payload={"raw_text": "@Bot 等开播吧", "at_bot": True},
    ))
    await wait_for_cognition(first, scene_id, 1)
    pending = await first.event_store.get_pending_next_wake(scene_id)
    assert pending is not None
    assert pending["origin_mode"] == "shadow"
    task_id = pending["task_id"]
    await first.stop()

    sent: list[ActionItem] = []

    async def send(action: ActionItem) -> bool:
        sent.append(action)
        return True

    async def wake_core(_messages):
        return cognition_result(
            reason="醒来报告，同时想再次自唤醒",
            content="我又看了一眼",
            wake_reason="没有新证据也继续醒",
            wake_after=300,
        )

    second = AgentRuntime(config, send_adapter=send, mock_social_handler=wake_core)
    await second.start()
    assert second.shadow_mode is True
    assert task_id in second.scheduler._known_task_ids

    assert await second.scheduler.trigger_task_now(task_id) is True
    traces = await wait_for_cognition(second, scene_id, 2)
    for _ in range(100):
        if second.shadow_would_send_log:
            break
        await asyncio.sleep(0.01)

    assert sent == []
    assert [item["content"] for item in second.shadow_would_send_log] == ["我又看了一眼"]
    assert await second.event_store.get_pending_next_wake(scene_id) is None
    latest = traces[0]
    assert latest["payload"]["result"]["future_attention"] is None

    cursor = await second.event_store._db.execute(
        "SELECT status FROM tasks WHERE id = ?;",
        (task_id,),
    )
    assert (await cursor.fetchone())[0] == "triggered"
    await second.stop()


@pytest.mark.asyncio
async def test_next_wake_may_renew_after_new_social_evidence(tmp_path):
    calls = 0

    async def social_core(_messages):
        nonlocal calls
        calls += 1
        if calls == 1:
            return cognition_result(
                reason="稍后看看",
                wake_reason="第一次 wake",
                wake_after=300,
            )
        if calls == 2:
            return cognition_result(reason="记住新消息，暂不说话")
        return cognition_result(
            reason="有新消息，所以允许继续关注",
            wake_reason="有证据的续期",
            wake_after=300,
        )

    runtime = AgentRuntime(
        RuntimeConfig(
            bot_qq=42,
            db_path=str(tmp_path / "renew-with-evidence.db"),
            debounce_idle_ms=10,
        ),
        mock_social_handler=social_core,
    )
    await runtime.start()
    scene_id = "group:renew"
    await runtime.receive_event(Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        payload={"raw_text": "@Bot 过会儿看看", "at_bot": True},
    ))
    await wait_for_cognition(runtime, scene_id, 1)
    first_wake = await runtime.event_store.get_pending_next_wake(scene_id)

    await runtime.receive_event(Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:B",
        payload={"raw_text": "情况有变化"},
    ))
    await wait_for_cognition(runtime, scene_id, 2)
    assert await runtime.scheduler.trigger_task_now(first_wake["task_id"]) is True
    await wait_for_cognition(runtime, scene_id, 3)

    renewed = await runtime.event_store.get_pending_next_wake(scene_id)
    assert renewed is not None
    assert renewed["task_id"] != first_wake["task_id"]
    assert renewed["reason"] == "有证据的续期"
    await runtime.stop()
