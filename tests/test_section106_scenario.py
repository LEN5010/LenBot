import pytest
import asyncio
import time
from len_bot.config import RuntimeConfig
from len_bot.testing.scenario_runner import ScenarioRunner
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal, TaskProposal
from len_bot.attention.models import AttentionDisposition

@pytest.mark.asyncio
async def test_section106_livestream_benchmark_scenario(tmp_path):
    db_file = str(tmp_path / "section106.db")
    config = RuntimeConfig(
        bot_qq=12345678,
        db_path=db_file,
        debounce_idle_ms=50,
        debounce_max_ms=200,
        monitored_keywords=["直播", "开播", "几点"]
    )

    # Define mock Pi response behavior based on the current stimulus
    async def mock_pi_cognition(messages: list[dict[str, str]]) -> EpisodeOutcome:
        user_prompt = messages[1]["content"]
        current_stimulus_text = user_prompt.split("【CURRENT STIMULUS】")[-1]
        if "你是不是也看那个" in current_stimulus_text:
            return EpisodeOutcome(
                disposition=FinalDisposition.ACTION,
                decision_reason="Directly asked by A if I watch it; respond enthusiastically",
                message_proposals=[
                    MessageProposal(content="看啊，今天不是说有新东西么")
                ]
            )
        elif "好像八点" in current_stimulus_text:
            # At 18:47, Bot hears "好像八点" and chooses SILENCE while scheduling future task!
            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                decision_reason="B provided temporal fact (20:00). I should stay silent now and check at 19:55",
                task_proposals=[
                    TaskProposal(description="19:55 检查 XX 直播状态", delay_seconds=480)
                ]
            )
        return EpisodeOutcome(
            disposition=FinalDisposition.SILENCE,
            decision_reason="No need to speak"
        )

    runner = ScenarioRunner(config=config, mock_pi_handler=mock_pi_cognition)
    await runner.setup()

    scene_id = "group:999"

    # ==========================================
    # Step 1: 18:42
    # A: 晚上直播有人看吗
    # B: 看啊
    # Expected: State updated, Attention OBSERVE/TRACK, 0 messages sent
    # ==========================================
    t_1842 = time.time()
    await runner.step_message(scene_id, user_id=1001, text="晚上直播有人看吗", timestamp=t_1842)
    await runner.settle(0.1)
    
    await runner.step_message(scene_id, user_id=1002, text="看啊", timestamp=t_1842 + 1)
    await runner.settle(0.1)

    state_1842 = runner.runtime.scene_manager.get_scene_state(scene_id)
    assert state_1842 is not None
    assert state_1842.version == 2
    assert "user:1001" in state_1842.participants
    assert "user:1002" in state_1842.participants
    assert len(runner.sent_actions) == 0  # Zero messages sent!

    # ==========================================
    # Step 2: 18:45
    # A: @Bot 你是不是也看那个
    # Expected: Hard Attention WAKE, Bot sends message!
    # ==========================================
    t_1845 = t_1842 + 180
    await runner.step_message(scene_id, user_id=1001, text="@Bot 你是不是也看那个", at_bot=True, timestamp=t_1845)
    await runner.settle(0.2)

    assert len(runner.sent_actions) == 1
    assert runner.sent_actions[0].content == "看啊，今天不是说有新东西么"

    state_1845 = runner.runtime.scene_manager.get_scene_state(scene_id)
    assert state_1845.bot_engagement == "active"
    assert state_1845.consecutive_bot_messages == 1

    # ==========================================
    # Step 3: 18:47
    # B: 好像八点
    # Expected: WAKE -> Pi chooses SILENCE -> Task created -> NO messages sent!
    # ==========================================
    # Reset cooldown so B's keyword or active engagement can trigger cognition
    state_1845.recent_bot_message_at = time.time() - 400
    t_1847 = t_1845 + 120
    await runner.step_message(scene_id, user_id=1002, text="好像八点", timestamp=t_1847)
    await runner.settle(0.2)

    # Verify no new message was sent (still 1 message total)
    assert len(runner.sent_actions) == 1

    # Verify Task was committed to SQLite
    cursor = await runner.runtime.event_store._db.execute("SELECT description, status FROM tasks WHERE scene_id = ?;", (scene_id,))
    tasks = await cursor.fetchall()
    assert len(tasks) == 1
    assert "19:55 检查 XX 直播状态" in tasks[0][0]
    assert tasks[0][1] == "pending"

    await runner.teardown()
