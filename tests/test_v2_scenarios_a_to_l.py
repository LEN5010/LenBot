"""
LenBot V2 Scenario Benchmark (V2 plan §三十三) — 12 个命名 Scenario 对应 12 条
用户体验目标。这是验收层：每个 Scenario 是一个 Goal 的端到端行为断言，
确定性、离线（mock cognition / 注入时钟），全部经由真实 Runtime 组件。

  A  Idle Group          Goal 1  默认沉默
  B  Active Participation Goal 2  无 @ 自然延续
  C  Topic Drift         Goal 3  主动退出
  D  OpenLoop            Goal 4  跨时间挂起与 resolve
  E  Promise             Goal 5  履行承诺（条件 obligation）
  F  Silent Cognition    Goal 6  思考但不说话
  G  Ambient Recall      Goal 7  Retained Interest
  H  Explicit Cancel     Goal 8  明确取消 → zero stale response
  I  Semantic Resolution Goal 8  语义过时 → SILENCE
  J  Memory              Goal 9  自然"记得"而非翻记录
  K  Model Escalation    Goal 10 人格连续性
  L  Privacy             Goal 12 跨 scope 泄漏 = 0
"""
import pytest
import asyncio
import json
import time
from types import SimpleNamespace

from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.events.models import Event, EventType
from len_bot.attention.models import AttentionDisposition
from len_bot.cognition.models import (
    EpisodeOutcome, FinalDisposition, MessageProposal, TaskProposal, RetainedItemProposal,
    SocialStateProposal, ThreadTransition,
)
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.pi_core import PiAgentCore
from len_bot.runtime.metrics import RuntimeMetrics
from len_bot.scenes.models import SceneState, ParticipationThread, ThreadStatus
from len_bot.memory.models import MemoryItem, MemoryKind


def _runtime(tmp_path, mock_pi=None, name="scen"):
    config = RuntimeConfig(
        bot_qq=12345678,
        db_path=str(tmp_path / f"{name}.db"),
        debounce_idle_ms=15,
        debounce_max_ms=40,
        reflection_quiet_window_seconds=60,
    )
    sent = []
    async def mock_send(item):
        sent.append(item)
        return True
    runtime = AgentRuntime(config, send_adapter=mock_send, mock_pi_handler=mock_pi)
    return runtime, sent


def _msg(scene_id, actor_id, text, t, **extra):
    return Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=scene_id,
        actor_id=actor_id, timestamp=t, payload={"raw_text": text, **extra},
    )


@pytest.mark.asyncio
async def test_scenario_a_idle_group_default_silence(tmp_path):
    """Goal 1: 30 条普通闲聊 → 0 条可见发言(可见沉默是 Goal 1 的验收口径)."""
    async def mock_pi(messages):
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, decision_reason="s")
    runtime, sent = _runtime(tmp_path, mock_pi, "a")
    await runtime.start()
    t0 = time.time()
    chat = ["草这游戏更新了", "又更新啥了", "修了bug", "那还行", "中午吃啥", "黄焖鸡"] * 5
    for i, text in enumerate(chat):
        await runtime.receive_event(_msg("group:a", f"user:{i % 4}", text, t0 + i * 5))
    await asyncio.sleep(0.3)
    assert len(sent) == 0
    await runtime.stop()


@pytest.mark.asyncio
async def test_scenario_b_active_participation_without_at(tmp_path):
    """Goal 2: @ 进入对话后, 无 @ 的相邻消息可自然续接."""
    async def mock_pi(messages):
        stimulus = messages[-1]["content"].split("【CURRENT STIMULUS】")[-1]
        if "你今晚看比赛吗" in stimulus:
            return EpisodeOutcome(disposition=FinalDisposition.ACTION, decision_reason="direct question",
                                  message_proposals=[MessageProposal(content="看啊")],
                                  social_state_proposal=SocialStateProposal(topic="比赛"))
        if "几点来着" in stimulus:
            return EpisodeOutcome(disposition=FinalDisposition.ACTION, decision_reason="continuation",
                                  message_proposals=[MessageProposal(content="十一点吧")])
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, decision_reason="s")
    runtime, sent = _runtime(tmp_path, mock_pi, "b")
    await runtime.start()
    scene_id = "group:b"
    t0 = time.time()
    await runtime.receive_event(_msg(scene_id, "user:A", "@Bot 你今晚看比赛吗", t0, at_bot=True))
    await asyncio.sleep(0.3)
    assert len(sent) == 1
    state = runtime.scene_manager.get_scene_state(scene_id)
    state.recent_bot_message_at = t0 + 1.5
    await runtime.receive_event(_msg(scene_id, "user:B", "几点来着", t0 + 5))
    await asyncio.sleep(0.3)
    assert len(sent) == 2
    assert sent[1].content == "十一点吧"
    await runtime.stop()


@pytest.mark.asyncio
async def test_scenario_c_topic_drift_exit(tmp_path):
    """Goal 3: 话题漂移 → close_thread → 不机械续接."""
    async def mock_pi(messages):
        stimulus = messages[-1]["content"].split("【CURRENT STIMULUS】")[-1]
        if "作业写完没" in stimulus:
            return EpisodeOutcome(disposition=FinalDisposition.SILENCE, decision_reason="drifted",
                                  social_state_proposal=SocialStateProposal(thread_transition=ThreadTransition.CLOSE))
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, decision_reason="s")
    runtime, sent = _runtime(tmp_path, mock_pi, "c")
    await runtime.start()
    scene_id = "group:c"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    t0 = time.time()
    actor.state = SceneState(scene_id=scene_id, version=5, activity_level="active",
                             active_topic="比赛", bot_engagement="active",
                             recent_bot_message_at=t0 - 2, consecutive_bot_messages=1)
    actor.state.current_thread = ParticipationThread(
        thread_id="th1", scene_id=scene_id, topic="比赛", status=ThreadStatus.ACTIVE,
        participants=["user:A", "user:B"], started_at=t0 - 10, last_relevant_at=t0 - 2)
    await runtime.receive_event(_msg(scene_id, "user:A", "话说你作业写完没", t0))
    await asyncio.sleep(0.3)
    assert len(sent) == 0
    assert runtime.scene_manager.get_scene_state(scene_id).current_thread.status == ThreadStatus.CLOSED
    await runtime.receive_event(_msg(scene_id, "user:B", "没别骂了", t0 + 5))
    await asyncio.sleep(0.2)
    assert len(sent) == 0
    await runtime.stop()


@pytest.mark.asyncio
async def test_scenario_d_openloop_cross_time_resolution(tmp_path):
    """Goal 4: Bot 提问挂 loop; 数分钟后 A 无 reply 回答 → 关联 + resolve + SILENCE."""
    async def mock_pi(messages):
        stimulus = messages[-1]["content"].split("【CURRENT STIMULUS】")[-1]
        if "你大概几点回来" in stimulus:
            return EpisodeOutcome(disposition=FinalDisposition.ACTION, decision_reason="asking",
                                  message_proposals=[MessageProposal(
                                      content="你大概几点回来？", expect_reply=True,
                                      reply_target="user:A", reply_intent="回家时间")])
        if "七点左右吧" in stimulus:
            import re
            loop_id_match = re.search(r"\[ID: (loop_[0-9a-f]+)\]", messages[-1]["content"])
            return EpisodeOutcome(disposition=FinalDisposition.SILENCE, decision_reason="answered; no reply needed",
                                  resolve_open_loop_ids=[loop_id_match.group(1)] if loop_id_match else [])
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, decision_reason="s")
    runtime, sent = _runtime(tmp_path, mock_pi, "d")
    await runtime.start()
    scene_id = "group:d"
    t0 = time.time()
    await runtime.receive_event(_msg(scene_id, "user:A", "@Bot 你大概几点回来啊", t0, at_bot=True))
    await asyncio.sleep(0.3)
    assert len(sent) == 1
    loops = await runtime.event_store.get_active_open_loops(scene_id)
    assert len(loops) == 1 and loops[0]["target_actor_id"] == "user:A"
    loop_id = loops[0]["id"]

    # 数分钟后 A 无 reply 直接回答
    await runtime.receive_event(_msg(scene_id, "user:A", "七点左右吧", t0 + 300))
    await asyncio.sleep(0.3)
    # Goal 4: resolve 不代表必须回复 — 0 新增可见消息
    assert len(sent) == 1
    assert await runtime.event_store.get_active_open_loops(scene_id) == []
    cursor = await runtime.event_store._db.execute("SELECT status FROM open_loops WHERE id=?;", (loop_id,))
    assert (await cursor.fetchone())[0] == "resolved"
    await runtime.stop()


@pytest.mark.asyncio
async def test_scenario_e_promise_fulfilled_on_live_start(tmp_path):
    """Goal 5: "开播叫我" → 条件任务 → LIVE_STARTED → 自动履行."""
    async def mock_pi(messages):
        stimulus = messages[-1]["content"].split("【CURRENT STIMULUS】")[-1]
        if "开播了叫我" in stimulus:
            return EpisodeOutcome(disposition=FinalDisposition.SILENCE, decision_reason="promise",
                                  task_proposals=[TaskProposal(description="提醒A看直播",
                                                               wake_event_type="LIVE_STARTED")])
        if "提醒A看直播" in stimulus:
            return EpisodeOutcome(disposition=FinalDisposition.ACTION, decision_reason="fulfil",
                                  message_proposals=[MessageProposal(content="开了")])
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, decision_reason="s")
    runtime, sent = _runtime(tmp_path, mock_pi, "e")
    await runtime.start()
    scene_id = "group:e"
    t0 = time.time()
    await runtime.receive_event(_msg(scene_id, "user:A", "@Bot 开播了叫我", t0, at_bot=True))
    await asyncio.sleep(0.3)
    assert len(sent) == 0
    await runtime.receive_event(Event(
        event_type=EventType.LIVE_STARTED, scene_id=scene_id, actor_id="plugin:bilibili_live",
        timestamp=t0 + 60, payload={"raw_text": "主播开播了", "room_id": 1}))
    await asyncio.sleep(0.4)
    assert len(sent) == 1 and sent[0].content == "开了"
    await runtime.stop()


@pytest.mark.asyncio
async def test_scenario_f_silent_cognition(tmp_path):
    """Goal 6: WAKE → 创建任务 → SILENCE. cognition != speech."""
    async def mock_pi(messages):
        stimulus = messages[-1]["content"].split("【CURRENT STIMULUS】")[-1]
        if "好像八点开" in stimulus:
            return EpisodeOutcome(disposition=FinalDisposition.SILENCE, decision_reason="schedule check",
                                  task_proposals=[TaskProposal(description="19:55 检查开播", delay_seconds=300)])
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, decision_reason="s")
    runtime, sent = _runtime(tmp_path, mock_pi, "f")
    await runtime.start()
    await runtime.receive_event(_msg("group:f", "user:B", "@Bot 好像八点开", time.time(), at_bot=True))
    await asyncio.sleep(0.3)
    assert len(sent) == 0
    assert len(await runtime.event_store.get_pending_tasks()) == 1
    await runtime.stop()


@pytest.mark.asyncio
async def test_scenario_g_ambient_recall(tmp_path):
    """Goal 7: retained item 在相关话题出现时进入情境, 由 cognition 自然引用."""
    prompts = []
    async def mock_pi(messages):
        prompts.append(messages[-1]["content"])
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, decision_reason="s")
    runtime, sent = _runtime(tmp_path, mock_pi, "g")
    await runtime.start()
    scene_id = "group:g"
    runtime.ambient_store.retain(scope=scene_id, source="plugin", topic="主播 切片",
                                 summary="刚才刷到主播的切片", salience=0.9)
    await runtime.receive_event(_msg(scene_id, "user:A", "@Bot 你看那个主播切片没", time.time(), at_bot=True))
    await asyncio.sleep(0.3)
    assert prompts and "主播的切片" in prompts[0]
    assert runtime.ambient_store.match(text="完全无关的作业话题", scope=scene_id) == []
    await runtime.stop()


@pytest.mark.asyncio
async def test_scenario_h_explicit_cancel_zero_stale_response(tmp_path):
    """Goal 8a: 用户说"算了" → gate 拒绝 → zero visible stale response."""
    sent = []
    async def mock_send(item):
        sent.append(item)
        return True
    runtime = AgentRuntime(RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "h.db")),
                           send_adapter=mock_send)
    await runtime.start()
    scene_id = "group:h"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    t0 = time.time()
    mailbox = EpisodeMailbox("ep_h", scene_id, base_scene_version=1)
    # 认知运行期间用户明确取消
    mailbox.post(_msg(scene_id, "user:A", "算了，不去了", t0 + 1))
    outcome = EpisodeOutcome(disposition=FinalDisposition.ACTION, decision_reason="found it",
                             message_proposals=[MessageProposal(content="查到了，是……")])
    decision = await runtime.runtime_gate.evaluate_and_commit(outcome, mailbox, actor.state)
    assert decision.disposition == FinalDisposition.SILENCE
    assert len(sent) == 0
    assert runtime.metrics.social["cancellations_honored"] >= 1
    await runtime.stop()


@pytest.mark.asyncio
async def test_scenario_i_semantic_resolution_silence(tmp_path):
    """Goal 8b: 群友已解答且提问者确认 → Pi 检测语义过时 → SILENCE."""
    async def mock_pi(messages):
        full = " ".join(m["content"] for m in messages)
        if "测试工具" in full and "哦懂了" in full:
            return EpisodeOutcome(disposition=FinalDisposition.SILENCE,
                                  decision_reason="already answered by others; stale")
        return EpisodeOutcome(disposition=FinalDisposition.ACTION, decision_reason="answering",
                              message_proposals=[MessageProposal(content="这是……")])
    runtime, sent = _runtime(tmp_path, mock_pi, "i")
    await runtime.start()
    scene_id = "group:i"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    t0 = time.time()
    mailbox = EpisodeMailbox("ep_i", scene_id, base_scene_version=1)
    mailbox.post(_msg(scene_id, "user:B", "这是昨天的测试工具，公告有链接", t0 + 1))
    mailbox.post(_msg(scene_id, "user:A", "哦懂了", t0 + 2))
    core = runtime.pi_core
    outcome, trace = await core.execute_episode(
        messages=[{"role": "user", "content": "@Bot 这是什么东西"}], mailbox=mailbox)
    assert outcome.disposition == FinalDisposition.SILENCE
    assert trace["interim_injections"] >= 1
    decision = await runtime.runtime_gate.evaluate_and_commit(outcome, mailbox, actor.state)
    assert decision.disposition == FinalDisposition.SILENCE
    await runtime.stop()


@pytest.mark.asyncio
async def test_scenario_j_natural_memory_recall(tmp_path):
    """Goal 9: 自然"记得"; 索证才走 retrieval(规则 8 在 prompt 层强制)."""
    prompts = []
    async def mock_pi(messages):
        prompts.append(messages)
        return EpisodeOutcome(disposition=FinalDisposition.ACTION, decision_reason="remember naturally",
                              message_proposals=[MessageProposal(content="你前几天不是还说懒得开黑么")])
    runtime, sent = _runtime(tmp_path, mock_pi, "j")
    await runtime.start()
    await runtime.memory_store.save_memory(MemoryItem(
        subject="user:A", kind=MemoryKind.TOPIC_INTEREST, key="gaming",
        value="前几天聊过想玩但懒得开黑", scope="group:j", evidence=["seed"],
        human_readable_assertion="A 前几天聊过想玩某游戏但懒得开黑"))
    await runtime.receive_event(_msg("group:j", "user:A", "@Bot 最近又想玩那个了", time.time(), at_bot=True))
    await asyncio.sleep(0.3)
    assert len(sent) == 1 and "懒得开黑" in sent[0].content
    rules = prompts[0][0]["content"]
    assert "自然口吻" in rules and "search_messages" in rules
    await runtime.stop()


@pytest.mark.asyncio
async def test_scenario_k_model_escalation_persona_continuity(tmp_path):
    """Goal 10: Normal→Deliberate 只换模型, 同一人格与轨迹."""
    normal_calls, deliberate_calls = [], []

    def _tool_call_response():
        msg = SimpleNamespace(
            tool_calls=[SimpleNamespace(id="t1", function=SimpleNamespace(
                name="search_messages", arguments=json.dumps({"query": "直播"})))],
            content=None)
        msg.model_dump = lambda: {"role": "assistant", "tool_calls": []}
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)

    def _final_response():
        outcome = EpisodeOutcome(disposition=FinalDisposition.ACTION, decision_reason="deep dig done",
                                 message_proposals=[MessageProposal(content="大概搞明白了")])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            tool_calls=None, content=outcome.model_dump_json()))],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20))

    class FakeCompletions:
        def __init__(self, sink, responses):
            self.sink = sink
            self.responses = list(responses)
        async def create(self, **kwargs):
            self.sink.append(kwargs)
            return self.responses.pop(0)

    class FakeRegistry:
        def resolve(self, tier):
            if tier.value == "deliberate":
                return SimpleNamespace(provider_id="p", model="deliberate-model",
                                       client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(
                                           deliberate_calls, [_final_response()]))))
            return SimpleNamespace(provider_id="p", model="normal-model",
                                   client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(
                                       normal_calls, [_tool_call_response()]))))

    class FakeToolkit:
        def get_tool_definitions(self): return []
        async def execute(self, name, args): return "证据" * 800  # >1200 chars → escalate

    core = PiAgentCore(RuntimeConfig(bot_qq=1), registry=FakeRegistry(), metrics=RuntimeMetrics())
    outcome, trace = await core.execute_episode(
        [{"role": "system", "content": "【IDENTITY】你是 Len"},
         {"role": "user", "content": "帮我深挖一下"}],
        EpisodeMailbox("ep_k", "group:k", 0), toolkit=FakeToolkit())
    assert outcome.message_proposals[0].content == "大概搞明白了"
    assert [s["tier"] for s in trace["steps"]] == ["normal", "deliberate"]
    assert normal_calls[0]["model"] == "normal-model"
    assert deliberate_calls[0]["model"] == "deliberate-model"
    # 同一 system prompt 对象贯穿两层 — 人格连续
    assert deliberate_calls[0]["messages"][0] is normal_calls[0]["messages"][0]


@pytest.mark.asyncio
async def test_scenario_l_privacy_cross_scope_leak_zero(tmp_path):
    """Goal 12: private:A 的秘密永远无法进入 group:X 的检索结果."""
    async def mock_pi(messages):
        # 私聊消息会硬唤醒 cognition; mock 保持全链路离线
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, decision_reason="offline")
    runtime, sent = _runtime(tmp_path, mock_pi, "l")
    await runtime.start()
    private_scene = "private:1001"
    group_scene = "group:l"
    # A 在私聊里倾诉; 反思形成 scoped 记忆 (visibility=scene)
    await runtime.receive_event(_msg(private_scene, "user:1001", "我明天可能辞职", time.time()))
    await asyncio.sleep(0.2)
    await runtime.memory_store.save_memory(MemoryItem(
        subject="user:1001", kind=MemoryKind.FACT, key="job_intent",
        value="明天可能辞职", scope=private_scene,
        evidence=["seed"], human_readable_assertion="A 明天可能辞职"))
    t0 = time.time()
    await runtime.receive_event(_msg(group_scene, "user:1001", "今天聊点啥", t0))
    await runtime.receive_event(_msg(group_scene, "user:1001", "大伙最近有啥打算", t0 + 1))
    await asyncio.sleep(0.3)

    group_scopes = [group_scene, "global-safe"]
    # 1. Memory 检索: private 信念不可达
    memories = await runtime.memory_store.query_memories(allowed_scopes=group_scopes, limit=50)
    assert all(m.scope != private_scene for m in memories)
    assert not any("辞职" in (m.value or "") for m in memories)
    # 2. Raw 检索: private 事件不可达
    hits = await runtime.event_store.search_messages("辞职", allowed_scopes=group_scopes)
    assert hits == []
    # 3. read_context / person history 越界同样为空
    ctx = await runtime.event_store.read_context("whatever", allowed_scopes=group_scopes)
    assert ctx == []
    history = await runtime.event_store.query_person_history("user:1001", allowed_scopes=group_scopes)
    assert all(h["scene_id"] != private_scene for h in history)
    # 4. 反向验证: 私聊 scope 内可以看到(知道 != 到处可用)
    own = await runtime.memory_store.query_memories(allowed_scopes=[private_scene], limit=10)
    assert any("辞职" in m.value for m in own)
    await runtime.stop()
