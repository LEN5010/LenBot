import pytest
import asyncio
import time
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.testing.scenario_runner import ScenarioRunner
from len_bot.events.models import Event, EventType
from len_bot.scenes.models import SceneState, ThreadStatus, ParticipationThread
from len_bot.attention.models import AttentionDisposition
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.pi_core import PiAgentCore

@pytest.mark.asyncio
async def test_scenario_a_default_silence(tmp_path):
    """
    Scenario A (Goal 1 — 默认沉默):
    50 human messages of general group chatter without mentioning or addressing the bot.
    Bot must ingest, update Scene, remain in OBSERVE/TRACK, and send 0 messages.
    """
    db_file = str(tmp_path / "scenario_a.db")
    config = RuntimeConfig(
        bot_qq=12345678,
        db_path=db_file,
        debounce_idle_ms=20,
        debounce_max_ms=50
    )

    sent_actions = []
    async def mock_send(item):
        sent_actions.append(item)
        return True

    runtime = AgentRuntime(config, send_adapter=mock_send)
    await runtime.start()

    scene_id = "group:idle_chat"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)

    chat_corpus = [
        "草这游戏更新了", "又更新啥了", "修了昨天那个bug", "那还行",
        "今天中午吃啥", "黄焖鸡米饭", "我也想吃", "走起",
        "今天天气真好", "外面有点热", "确实挺晒的", "带伞没",
        "有人开黑吗", "几点", "晚上八点吧", "加我一个",
        "明天放假吗", "不放", "悲", "别提了",
        "今天周几了", "周四", "明天就是周五", "快了快了",
        "买了新键盘", "啥轴的", "红轴", "挺安静的",
        "刚刚打了一把", "赢了吗", "被暴打了", "哈哈哈",
        "看直播吗", "谁的直播", "官方那个", "几点开播",
        "好像七点半", "那还早", "先打把排位", "拉我拉我",
        "晚上吃火锅不", "太辣了受不了", "微辣就行", "行吧",
        "这周作业写了没", "没写", "我也没", "明晚一起补"
    ]

    t = time.time()
    for i, text in enumerate(chat_corpus):
        ev = Event(
            event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id=scene_id,
            actor_id=f"user:{1000 + (i % 5)}",
            timestamp=t + i * 5,
            payload={"raw_text": text}
        )
        await runtime.receive_event(ev)

    # Allow debouncer and actors to settle
    await asyncio.sleep(0.3)

    # INVARIANT ASSERTION: ZERO visible speech!
    assert len(sent_actions) == 0

    state = runtime.scene_manager.get_scene_state(scene_id)
    assert state is not None
    assert state.version >= len(chat_corpus)
    assert state.bot_engagement == "idle" or state.bot_engagement == "observing"

    await runtime.stop()

@pytest.mark.asyncio
async def test_scenario_b_natural_continuation_without_at(tmp_path):
    """
    Scenario B (Goal 2 — 无需每次 @ 也能自然延续对话):
    A mentions @Bot to ask about match. Bot answers.
    B asks '几点来着' (no @, no reply).
    Bot naturally continues because active ParticipationThread exists on '比赛'.
    A says '十一点吧', B says '那还挺晚'.
    Bot naturally comments '确实，打完估计都后半夜了'.
    """
    db_file = str(tmp_path / "scenario_b.db")
    config = RuntimeConfig(
        bot_qq=12345678,
        db_path=db_file,
        debounce_idle_ms=20,
        debounce_max_ms=50
    )

    sent_actions = []
    async def mock_send(item):
        sent_actions.append(item)
        return True

    # Cognitive policy simulating natural continuation
    async def mock_pi(messages):
        stimulus_text = messages[-1].get("content", "")
        if "【CURRENT STIMULUS】" in stimulus_text:
            stimulus_text = stimulus_text.split("【CURRENT STIMULUS】")[-1]

        if "几点来着" in stimulus_text:
            return EpisodeOutcome(
                disposition=FinalDisposition.ACTION,
                thought="Inquiring about match time in active thread",
                message_proposals=[MessageProposal(content="十一点吧")]
            )
        elif "那还挺晚" in stimulus_text:
            return EpisodeOutcome(
                disposition=FinalDisposition.ACTION,
                thought="Agreeing on late match time in active thread",
                message_proposals=[MessageProposal(content="确实，打完估计都后半夜了")]
            )
        elif "你今晚看比赛吗" in stimulus_text:
            return EpisodeOutcome(
                disposition=FinalDisposition.ACTION,
                thought="Direct question about match",
                message_proposals=[MessageProposal(content="看啊，不出意外应该看")],
                state_annotations={"topic": "比赛"}
            )
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, thought="No need to speak")

    runtime = AgentRuntime(config, send_adapter=mock_send, mock_pi_handler=mock_pi)
    await runtime.start()

    scene_id = "group:match_chat"
    t0 = time.time()

    # Step 1: A asks @Bot
    ev1 = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        timestamp=t0,
        payload={"raw_text": "@Bot 你今晚看比赛吗", "at_bot": True}
    )
    await runtime.receive_event(ev1)
    await asyncio.sleep(0.3)

    assert len(sent_actions) == 1
    assert sent_actions[0].content == "看啊，不出意外应该看"

    state1 = runtime.scene_manager.get_scene_state(scene_id)
    assert state1.current_thread is not None
    assert state1.current_thread.status == ThreadStatus.ACTIVE
    assert state1.current_thread.topic == "比赛"

    # Step 2: B asks "几点来着" (NO @, NO reply)
    # Natural continuation because active thread exists!
    state1.recent_bot_message_at = t0 + 1.5  # Ensure > 1.0s elapsed
    ev2 = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:B",
        timestamp=t0 + 5,
        payload={"raw_text": "几点来着"}
    )
    await runtime.receive_event(ev2)
    await asyncio.sleep(0.3)

    assert len(sent_actions) == 2
    assert sent_actions[1].content == "十一点吧"

    # Step 3: B says "那还挺晚" (NO @, NO reply)
    state2 = runtime.scene_manager.get_scene_state(scene_id)
    state2.recent_bot_message_at = t0 + 6.5
    ev3 = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:B",
        timestamp=t0 + 10,
        payload={"raw_text": "那还挺晚"}
    )
    await runtime.receive_event(ev3)
    await asyncio.sleep(0.3)

    assert len(sent_actions) == 3
    assert sent_actions[2].content == "确实，打完估计都后半夜了"

    await runtime.stop()

@pytest.mark.asyncio
async def test_scenario_c_active_exit_on_topic_drift(tmp_path):
    """
    Scenario C (Goal 3 — 能主动退出已经不属于自己的话题):
    Bot just commented on the match ('确实，打完估计都后半夜了').
    A suddenly changes subject: '话说你作业写完没'.
    Cognition detects topic drift, outputs SILENCE and closes thread.
    B and C follow up with homework banter.
    Bot does NOT speak. Thread remains closed.
    """
    db_file = str(tmp_path / "scenario_c.db")
    config = RuntimeConfig(
        bot_qq=12345678,
        db_path=db_file,
        debounce_idle_ms=20,
        debounce_max_ms=50
    )

    sent_actions = []
    async def mock_send(item):
        sent_actions.append(item)
        return True

    async def mock_pi(messages):
        full_text = " ".join(m.get("content", "") for m in messages)
        if "作业写完没" in full_text:
            # Cognition decides: Topic has drifted to homework, not my business. Close thread!
            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                thought="Topic drifted from match to homework; stepping out of conversation.",
                state_annotations={"close_thread": True, "topic_drift": "homework"}
            )
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, thought="Silence")

    runtime = AgentRuntime(config, send_adapter=mock_send, mock_pi_handler=mock_pi)
    await runtime.start()

    scene_id = "group:drift_test"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    t0 = time.time()

    # Seed active ParticipationThread from previous conversation
    actor.state = SceneState(
        scene_id=scene_id,
        version=10,
        participants=["user:12345678", "user:A", "user:B"],
        activity_level="active",
        active_topic="比赛",
        bot_engagement="active",
        recent_bot_message_at=t0 - 2.0,
        consecutive_bot_messages=1,
        intervening_messages_since_bot=0
    )
    actor.state.current_thread = ParticipationThread(
        thread_id="th_match",
        scene_id=scene_id,
        topic="比赛",
        participants=["user:12345678", "user:A", "user:B"],
        status=ThreadStatus.ACTIVE,
        started_at=t0 - 10,
        last_relevant_at=t0 - 2
    )

    # 1. A changes subject to homework: "话说你作业写完没"
    ev_drift = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        timestamp=t0,
        payload={"raw_text": "话说你作业写完没"}
    )
    await runtime.receive_event(ev_drift)
    await asyncio.sleep(0.3)

    # Bot stays SILENT!
    assert len(sent_actions) == 0

    # Thread has been closed by cognition decision!
    state_after_drift = runtime.scene_manager.get_scene_state(scene_id)
    assert state_after_drift.current_thread.status == ThreadStatus.CLOSED

    # 2. B and C continue homework chat
    ev_b = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:B",
        timestamp=t0 + 10,
        payload={"raw_text": "没，别骂了"}
    )
    await runtime.receive_event(ev_b)
    await asyncio.sleep(0.2)

    ev_c = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:C",
        timestamp=t0 + 20,
        payload={"raw_text": "我甚至还没开始"}
    )
    await runtime.receive_event(ev_c)
    await asyncio.sleep(0.2)

    # INVARIANT: Bot stayed completely silent throughout homework chat! Zero messages sent!
    assert len(sent_actions) == 0

    await runtime.stop()

@pytest.mark.asyncio
async def test_scenario_h_semantic_staleness_and_interim_resolution(tmp_path):
    """
    Scenario H & Goal 8 — Cognition 被新聊天修正与语义过时判断:
    A: @Bot 这是什么东西
    While Bot cognition is in flight (executing mock tool delay):
    B in group says: "这是昨天的测试工具，群公告有链接"
    A says: "哦懂了"
    Interim context arrives at step boundary.
    Bot realizes the query is already answered by B.
    Bot outputs FinalDisposition.SILENCE.
    ZERO visible stale messages sent!
    """
    db_file = str(tmp_path / "scenario_h.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)

    sent_actions = []
    async def mock_send(item):
        sent_actions.append(item)
        return True

    async def mock_pi_delayed(messages):
        full_text = " ".join(m.get("content", "") for m in messages)
        # Check if interim social activity shows B already answered
        if "测试工具" in full_text and "哦懂了" in full_text:
            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                thought="User B already answered and User A understood. Old response is stale; choosing SILENCE."
            )
        # Initial pass without interim context
        return EpisodeOutcome(
            disposition=FinalDisposition.ACTION,
            thought="Found the answer",
            message_proposals=[MessageProposal(content="这是一款自动化测试工具。")]
        )

    runtime = AgentRuntime(config, send_adapter=mock_send, mock_pi_handler=mock_pi_delayed)
    await runtime.start()

    scene_id = "group:staleness_test"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    t0 = time.time()

    # 1. Mailbox receives initial question
    mailbox = EpisodeMailbox("ep_stale_1", scene_id, base_scene_version=1)

    # 2. While cognition is running, B and A chat in the scene
    b_msg = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:B",
        timestamp=t0 + 1,
        payload={"raw_text": "这是昨天的测试工具，群公告有链接"}
    )
    a_msg = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        timestamp=t0 + 2,
        payload={"raw_text": "哦懂了"}
    )
    mailbox.post(b_msg)
    mailbox.post(a_msg)

    # 3. PiAgentCore executes with the mailbox containing interim context
    core = PiAgentCore(config, mock_handler=mock_pi_delayed)
    outcome, _trace = await core.execute_episode(
        messages=[{"role": "user", "content": "这是什么东西"}],
        mailbox=mailbox
    )

    # INVARIANT: Model inspected interim context and chose SILENCE!
    assert outcome.disposition == FinalDisposition.SILENCE
    assert "stale" in outcome.thought.lower() or "silence" in outcome.thought.lower()

    # 4. Gate validates and commits SILENCE
    decision = await runtime.runtime_gate.evaluate_and_commit(outcome, mailbox, actor.state)
    assert decision.disposition == FinalDisposition.SILENCE
    assert len(sent_actions) == 0

    await runtime.stop()
