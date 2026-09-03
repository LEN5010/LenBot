import pytest
import time
from len_bot.config import RuntimeConfig
from len_bot.scenes.models import SceneState
from len_bot.state.interest import InterestModel
from len_bot.attention.budget import SpeakingBudget
from len_bot.attention.initiative import InitiativeEngine, InitiativeDisposition
from len_bot.cognition.router import CognitionRouter, CognitiveTier
from len_bot.testing.scenario_runner import ScenarioRunner
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal

def test_interest_model_scoring():
    model = InterestModel()
    score, topics = model.score_text("今晚 CS2 Major 决赛太精彩了")
    assert "esports" in topics or "gaming" in topics
    assert score >= 0.7

    score_low, topics_low = model.score_text("今天天气真好，去散步了")
    assert score_low == 0.0

def test_speaking_budget_anti_chatterbox():
    budget = SpeakingBudget(base_threshold=0.60)
    now = time.time()
    
    # 1. Fresh scene: threshold is baseline
    state_fresh = SceneState(scene_id="group:1", consecutive_bot_messages=0)
    th_fresh = budget.calculate_threshold(state_fresh, now)
    assert th_fresh == 0.60

    # 2. Bot spoke 10 seconds ago: penalty applied
    state_recent = SceneState(
        scene_id="group:1",
        consecutive_bot_messages=1,
        recent_bot_message_at=now - 10
    )
    th_recent = budget.calculate_threshold(state_recent, now)
    assert th_recent >= 0.90  # Much harder to speak proactively!

    # 3. Bot spoke consecutively 2 times: hard blocked (threshold capped at 1.0)
    state_monologue = SceneState(
        scene_id="group:1",
        consecutive_bot_messages=2,
        recent_bot_message_at=now - 5
    )
    th_monologue = budget.calculate_threshold(state_monologue, now)
    assert th_monologue == 1.0

def test_cognition_router_escalation():
    config = RuntimeConfig(default_model="model-chat", deliberate_model="model-reasoner")
    router = CognitionRouter(config)

    assert router.get_model_for_tier(CognitiveTier.NORMAL) == "model-chat"
    assert router.get_model_for_tier(CognitiveTier.DELIBERATE) == "model-reasoner"

    # Short tool result: no escalation
    assert router.should_escalate(CognitiveTier.NORMAL, step_count=1, latest_tool_result="简单结果") is False

    # Heavy payload (>1200 chars): triggers escalation!
    heavy_payload = "复杂直播切片数据分析 " * 150
    assert router.should_escalate(CognitiveTier.NORMAL, step_count=1, latest_tool_result=heavy_payload) is True

    # Explicit complexity tag: triggers escalation!
    complex_flag_payload = "[COMPLEXITY: HIGH] 双方产生严重事实分歧，需要深入比对历史证据"
    assert router.should_escalate(CognitiveTier.NORMAL, step_count=1, latest_tool_result=complex_flag_payload) is True

@pytest.mark.asyncio
async def test_agency_initiative_and_anti_spam_flow(tmp_path):
    """
    Proves V1-E:
    Bot actively chimes in when high interest topic is discussed without being @'d,
    but speaking budget prevents it from spamming consecutively.
    """
    db_file = str(tmp_path / "v1e_agency.db")
    config = RuntimeConfig(
        bot_qq=12345678,
        db_path=db_file,
        debounce_idle_ms=30,
        debounce_max_ms=100
    )

    async def mock_pi(messages: list[dict[str, str]]) -> EpisodeOutcome:
        user_prompt = messages[1]["content"]
        if "Major 决赛" in user_prompt:
            return EpisodeOutcome(
                disposition=FinalDisposition.ACTION,
                thought="High interest in CS2 Major; voluntarily join discussion",
                message_proposals=[MessageProposal(content="今晚 Major 决赛我也在看，感觉这把很悬！")]
            )
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, thought="Stay quiet")

    runner = ScenarioRunner(config=config, mock_pi_handler=mock_pi)
    await runner.setup()
    scene_id = "group:666"

    # 1. First trigger: User discusses high-interest topic without mentioning Bot
    await runner.step_message(scene_id, user_id=1001, text="今晚 CS2 Major 决赛大家看好谁？", at_bot=False)
    await runner.settle(0.15)

    # Bot voluntarily initiated speech!
    assert len(runner.sent_actions) == 1
    assert "今晚 Major 决赛我也在看" in runner.sent_actions[0].content

    # 2. Immediately, User replies: "我也觉得"
    # Even if topic is still CS2, Bot just spoke (consecutive_bot_messages == 1 and recent_bot_at < 60s)
    # Speaking budget blocks proactive initiative!
    await runner.step_message(scene_id, user_id=1002, text="确实，CS2 这届比赛太精彩了", at_bot=False)
    await runner.settle(0.15)

    # Total sent actions must STILL BE 1 (No spam! Bot kept silent)
    assert len(runner.sent_actions) == 1

    await runner.teardown()
