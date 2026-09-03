import pytest
import asyncio
import time
from len_bot.config import RuntimeConfig
from len_bot.testing.scenario_runner import ScenarioRunner
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal, TaskProposal
from len_bot.events.models import StimulusType

@pytest.mark.asyncio
async def test_v1b_cross_time_and_open_loop_execution(tmp_path):
    """
    Proves V1-B Persistent Execution:
    1. Bot schedules future task -> Scheduler fires autonomously across time -> Pi executes proactive episode.
    2. Bot establishes Open Loop -> Unrelated users don't wake Bot -> Target user answers -> Open Loop resolves.
    3. Expired open loops are cleaned up via TTL sweep.
    """
    db_file = str(tmp_path / "v1b_persistent.db")
    config = RuntimeConfig(
        bot_qq=12345678,
        db_path=db_file,
        debounce_idle_ms=30,
        debounce_max_ms=100
    )

    task_executed = False
    open_loop_resolved = False

    async def mock_pi_cognition(messages: list[dict[str, str]]) -> EpisodeOutcome:
        nonlocal task_executed, open_loop_resolved
        user_prompt = messages[1]["content"]
        current_stimulus_text = user_prompt.split("【CURRENT STIMULUS】")[-1]

        # 1. Initial trigger: User A says "@Bot 查一下今晚有啥安排"
        if "查一下今晚有啥安排" in current_stimulus_text:
            return EpisodeOutcome(
                disposition=FinalDisposition.ACTION,
                decision_reason="Answer A and ask when they will arrive; also schedule a proactive check in 0.3s",
                message_proposals=[
                    MessageProposal(
                        content="安排在直播，你大概几点能来？",
                        expect_reply=True,
                        reply_target="user:1001",
                        reply_intent="arrival_time"
                    )
                ],
                task_proposals=[
                    TaskProposal(
                        description="检查直播间推流状态",
                        delay_seconds=0.25
                    )
                ]
            )

        # 2. Proactive Task execution when Scheduler fires
        if "检查直播间推流状态" in current_stimulus_text:
            task_executed = True
            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                decision_reason="Proactive check completed: stream is ready. Keeping silence."
            )

        # 3. User A replies: "我七点到"
        if "我七点到" in current_stimulus_text:
            # Check if active open loop is visible in the context!
            assert "arrival_time" in user_prompt
            open_loop_resolved = True
            # Extract loop ID from prompt
            loop_id = ""
            for line in user_prompt.splitlines():
                if "[ID: loop_" in line:
                    loop_id = line.split("[ID: ")[1].split("]")[0]
                    break

            return EpisodeOutcome(
                disposition=FinalDisposition.ACTION,
                decision_reason="User A answered the arrival time. Resolving open loop and confirming.",
                message_proposals=[
                    MessageProposal(content="好的收到，七点见！")
                ],
                resolve_open_loop_ids=[loop_id] if loop_id else []
            )

        return EpisodeOutcome(
            disposition=FinalDisposition.SILENCE,
            decision_reason="No need to speak"
        )

    runner = ScenarioRunner(config=config, mock_pi_handler=mock_pi_cognition)
    await runner.setup()

    scene_id = "group:888"

    # =========================================================================
    # Step 1: User A talks to Bot. Bot answers, sets Open Loop & schedules Task
    # =========================================================================
    await runner.step_message(scene_id, user_id=1001, text="@Bot 查一下今晚有啥安排", at_bot=True)
    await runner.settle(0.1)

    assert len(runner.sent_actions) == 1
    assert "安排在直播" in runner.sent_actions[0].content

    # Assert Open Loop is active in SQLite
    loops = await runner.runtime.event_store.get_active_open_loops(scene_id)
    assert len(loops) == 1
    assert loops[0]["target_actor_id"] == "user:1001"
    assert loops[0]["intent"] == "arrival_time"

    # =========================================================================
    # Step 2: Cross-Time Continuity.
    # Wait 0.4s for Scheduler to fire the task autonomously!
    # No human messages sent during this window.
    # =========================================================================
    await asyncio.sleep(0.4)
    await runner.settle(0.1)

    # Assert Task was executed by Scheduler & Pi
    assert task_executed is True

    # Assert Task status in SQLite is now 'triggered'
    cursor = await runner.runtime.event_store._db.execute("SELECT status FROM tasks WHERE scene_id = ?;", (scene_id,))
    task_row = await cursor.fetchone()
    assert task_row is not None
    assert task_row[0] == "triggered"

    # =========================================================================
    # Step 3: Social Continuity & Attention Selectivity
    # User B sends an unrelated message ("今天天气真不错")
    # Expected: Attention returns OBSERVE, Bot does NOT wake.
    # =========================================================================
    before_count = len(runner.sent_actions)
    await runner.step_message(scene_id, user_id=1002, text="今天天气真不错")
    await runner.settle(0.1)
    # No new message sent
    assert len(runner.sent_actions) == before_count

    # =========================================================================
    # Step 4: Target User A returns and answers ("我七点到")
    # Expected: Open Loop Attention WAKE -> Pi resolves loop -> Confirms
    # =========================================================================
    await runner.step_message(scene_id, user_id=1001, text="我七点到")
    await runner.settle(0.1)

    assert open_loop_resolved is True
    assert len(runner.sent_actions) == before_count + 1
    assert "好的收到，七点见！" in runner.sent_actions[-1].content

    # Assert Open Loop status is now 'resolved'
    active_loops = await runner.runtime.event_store.get_active_open_loops(scene_id)
    assert len(active_loops) == 0

    # =========================================================================
    # Step 5: TTL GC Sweeper test
    # Create a dummy expired open loop and sweep it
    # =========================================================================
    now = time.time()
    await runner.runtime.event_store.save_open_loop({
        "id": "loop_stale_999",
        "scene_id": scene_id,
        "target_actor_id": "user:1003",
        "intent": "old_question",
        "source_event_id": "ep_old",
        "status": "active",
        "created_at": now - 10000,
        "expires_at": now - 100 # expired!
    })

    expired = await runner.runtime.open_loop_manager.sweep_ttl_expiration()
    assert "loop_stale_999" in expired

    await runner.teardown()
