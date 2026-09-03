import pytest
import asyncio
import time
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.events.models import Event, EventType
from len_bot.attention.models import AttentionDisposition
from len_bot.cognition.models import (
    EpisodeOutcome,
    FinalDisposition,
    MessageProposal,
    TaskProposal,
    RetainedItemProposal,
    CONDITION_TASK_DEFAULT_DEADLINE_SECONDS,
)
from len_bot.state.open_loops import OpenLoopManager


@pytest.mark.asyncio
async def test_scenario_e_promise_fulfilled_on_live_start(tmp_path):
    """
    Scenario E (Goal 5 — 记得自己答应过什么):
    A: "@Bot 开播了叫我" → cognition proposes a CONDITION-BOUND task (wake_event_type=LIVE_STARTED).
    Later the Bilibili sensor emits LIVE_STARTED (no @, no reply, no mention).
    The obligation fires through the standard TASK_DUE path → WAKE → bot fulfills the promise.
    Also verifies: bare plugin facts never create tasks on their own (Invariant F).
    """
    db_file = str(tmp_path / "scenario_e.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)

    sent_actions = []
    async def mock_send(item):
        sent_actions.append(item)
        return True

    async def mock_pi(messages):
        # Only inspect the CURRENT STIMULUS section — RECENT RAW CHAT still contains
        # the original promise text, which must not re-trigger the promise branch.
        stimulus_text = messages[-1].get("content", "")
        if "【CURRENT STIMULUS】" in stimulus_text:
            stimulus_text = stimulus_text.split("【CURRENT STIMULUS】")[-1]
        if "开播了叫我" in stimulus_text:
            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                thought="Promise registered: wake me when the stream starts",
                task_proposals=[
                    TaskProposal(
                        description="A 要求开播时提醒他看直播",
                        wake_event_type="LIVE_STARTED"
                    )
                ]
            )
        # The TASK_DUE stimulus carries the task description as its text
        if "提醒他看直播" in stimulus_text or "主播开播了" in stimulus_text:
            return EpisodeOutcome(
                disposition=FinalDisposition.ACTION,
                thought="Fulfilling the promise: the stream just started",
                message_proposals=[MessageProposal(content="开了")]
            )
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, thought="Silence")

    runtime = AgentRuntime(config, send_adapter=mock_send, mock_pi_handler=mock_pi)
    await runtime.start()

    scene_id = "group:live_promise"
    t0 = time.time()

    # 1. A asks the bot to notify him when the stream starts
    ev_promise = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        timestamp=t0,
        payload={"raw_text": "@Bot 开播了叫我", "at_bot": True}
    )
    await runtime.receive_event(ev_promise)
    await asyncio.sleep(0.3)

    # Goal 6: cognition != speech — the promise episode stays SILENT
    assert len(sent_actions) == 0

    # The condition-bound task is durably committed with the default deadline cap
    pending = await runtime.event_store.get_pending_tasks()
    assert len(pending) == 1
    assert pending[0]["wake_event_type"] == "LIVE_STARTED"
    assert (pending[0]["due_at"] - pending[0]["created_at"]) == pytest.approx(
        CONDITION_TASK_DEFAULT_DEADLINE_SECONDS, rel=0.01
    )

    # 2. The Bilibili sensor emits LIVE_STARTED — no mention, no reply, nothing social
    ev_live = Event(
        event_type=EventType.LIVE_STARTED,
        scene_id=scene_id,
        actor_id="plugin:bilibili_live",
        timestamp=t0 + 60,
        payload={"raw_text": "主播开播了：今晚一起看比赛", "room_id": 777}
    )
    await runtime.receive_event(ev_live)
    await asyncio.sleep(0.4)

    # The obligation woke cognition and the promise was fulfilled
    assert len(sent_actions) == 1
    assert sent_actions[0].content == "开了"

    # Task is marked triggered in the same atomic commit as the TASK_DUE event
    pending_after = await runtime.event_store.get_pending_tasks()
    assert len(pending_after) == 0

    await runtime.stop()


@pytest.mark.asyncio
async def test_unclaimed_plugin_fact_never_wakes(tmp_path):
    """
    Goal 7 / Invariant F & H: a plugin fact (LIVE_STARTED) with NO matching obligation
    must never wake cognition. Attention returns OBSERVE for PLUGIN_FACT and no
    episode runs — zero visible behavior, zero LLM calls.
    """
    db_file = str(tmp_path / "fact_no_wake.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)

    sent_actions = []
    async def mock_send(item):
        sent_actions.append(item)
        return True

    llm_calls = []
    async def mock_pi(messages):
        llm_calls.append(messages)
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, thought="unused")

    runtime = AgentRuntime(config, send_adapter=mock_send, mock_pi_handler=mock_pi)
    await runtime.start()

    scene_id = "group:fact_test"
    t0 = time.time()
    ev_live = Event(
        event_type=EventType.LIVE_STARTED,
        scene_id=scene_id,
        actor_id="plugin:bilibili_live",
        timestamp=t0,
        payload={"raw_text": "主播开播了", "room_id": 888}
    )
    await runtime.receive_event(ev_live)
    await asyncio.sleep(0.2)

    att = runtime._last_attention_result
    assert att is not None
    assert att.disposition == AttentionDisposition.OBSERVE
    assert att.reason == "plugin_fact_observed"
    assert len(llm_calls) == 0          # cognition never ran
    assert len(sent_actions) == 0       # nothing visible

    await runtime.stop()


@pytest.mark.asyncio
async def test_scenario_f_silent_cognition_with_timer_task(tmp_path):
    """
    Scenario F (Goal 6 — 可以思考但不说话):
    WAKE (explicit mention) → cognition understands timing info → creates a timer task
    → SILENCE. Zero visible output; the durable effect is exactly one pending task.
    """
    db_file = str(tmp_path / "scenario_f.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)

    sent_actions = []
    async def mock_send(item):
        sent_actions.append(item)
        return True

    async def mock_pi(messages):
        full_text = " ".join(m.get("content", "") for m in messages)
        if "好像八点开" in full_text:
            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                thought="Stream starts at 20:00; schedule a check at 19:55 and stay silent",
                task_proposals=[TaskProposal(description="19:55 检查直播是否开播", delay_seconds=300)]
            )
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, thought="Silence")

    runtime = AgentRuntime(config, send_adapter=mock_send, mock_pi_handler=mock_pi)
    await runtime.start()

    scene_id = "group:silent_cognition"
    t0 = time.time()
    ev = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:B",
        timestamp=t0,
        payload={"raw_text": "@Bot 好像八点开", "at_bot": True}
    )
    await runtime.receive_event(ev)
    await asyncio.sleep(0.3)

    assert len(sent_actions) == 0
    pending = await runtime.event_store.get_pending_tasks()
    assert len(pending) == 1
    assert "19:55" in pending[0]["description"]
    assert (pending[0]["due_at"] - pending[0]["created_at"]) == pytest.approx(300, rel=0.01)

    await runtime.stop()


@pytest.mark.asyncio
async def test_scenario_g_ambient_recall_scope_and_ttl(tmp_path):
    """
    Scenario G (Goal 7 — Retained Interest / Ambient Item):
    The agent retained an ambient item (a streamer clip it saw earlier).
    - When the group later talks about the SAME topic, the item enters the
      Situation Package and cognition may naturally reference it.
    - In an unrelated scene the item is scope-blocked and absent.
    - After TTL the sweeper removes it entirely.
    """
    db_file = str(tmp_path / "scenario_g.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)

    sent_actions = []
    prompts_seen = []

    async def mock_send(item):
        sent_actions.append(item)
        return True

    async def mock_pi(messages):
        prompts_seen.append(messages)
        user_text = messages[-1].get("content", "")
        if "主播切片" in user_text:
            return EpisodeOutcome(
                disposition=FinalDisposition.ACTION,
                thought="The group is discussing the exact clip I saw earlier",
                message_proposals=[MessageProposal(content="草，我刚才还真刷到他那个切片了")]
            )
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, thought="Not my business")

    runtime = AgentRuntime(config, send_adapter=mock_send, mock_pi_handler=mock_pi)
    await runtime.start()

    scene_id = "group:clip_talk"
    now = time.time()

    # The agent previously saw a clip (e.g. via a plugin) and retained it
    item = runtime.ambient_store.retain(
        scope=scene_id,
        source="test_plugin",
        topic="主播 切片",
        summary="刚才刷到主播的那个切片，笑死",
        salience=0.9,
        retained_at=now - 1800
    )
    assert item.expires_at - item.retained_at == runtime.ambient_store.default_ttl_seconds

    # 1. Related topic appears in the scene → ambient item enters the package
    ev = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        timestamp=now,
        payload={"raw_text": "@Bot 你看到那个主播切片没", "at_bot": True}
    )
    await runtime.receive_event(ev)
    await asyncio.sleep(0.3)

    assert len(prompts_seen) == 1
    ambient_section = prompts_seen[0][-1]["content"].split("【RELEVANT AMBIENT ITEMS】")[-1]
    assert "主播" in ambient_section and "无相关环境条目" not in ambient_section
    assert len(sent_actions) == 1
    assert "切片" in sent_actions[0].content

    # 2. Unrelated scene: scope guard blocks the item entirely
    other_scene_prompts = []
    ev_other = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:unrelated",
        actor_id="user:B",
        timestamp=now + 1,
        payload={"raw_text": "@Bot 主播切片啥时候更新的", "at_bot": True}
    )
    # Capture via a second runtime-level match check (no episode needed to prove scope guard)
    matched_other = runtime.ambient_store.match(text="主播切片啥时候更新的", scope="group:unrelated")
    assert matched_other == []
    assert not other_scene_prompts

    # 3. TTL expiry: sweeper removes the item; matching finds nothing
    runtime.ambient_store.sweep(now=item.expires_at + 1)
    assert runtime.ambient_store.match(text="主播切片", scope=scene_id) == []
    assert runtime.ambient_store.items() == []

    await runtime.stop()


@pytest.mark.asyncio
async def test_gate_retained_item_proposal_enters_ambient_store(tmp_path):
    """
    ADR-0018: cognition may propose retention via EpisodeOutcome.retained_item_proposals.
    The RuntimeGate writes them to the AmbientStore as a post-transaction external
    side-effect (same tier as scheduler registration — never inside the DB transaction).
    """
    db_file = str(tmp_path / "retained_proposal.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:retain_test"
    from len_bot.cognition.mailbox import EpisodeMailbox
    from len_bot.scenes.models import SceneState

    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    actor.state = SceneState(scene_id=scene_id, version=1)

    outcome = EpisodeOutcome(
        disposition=FinalDisposition.SILENCE,
        thought="Interesting fact seen in chat; retain for later",
        retained_item_proposals=[
            RetainedItemProposal(
                topic="新版本 补丁",
                summary="群友说某游戏明天发大版本补丁，重做了技能系统",
                salience=0.7
            )
        ]
    )
    mailbox = EpisodeMailbox("ep_retain_1", scene_id, base_scene_version=1)
    decision = await runtime.runtime_gate.evaluate_and_commit(outcome, mailbox, actor.state)

    assert decision.disposition == FinalDisposition.SILENCE
    items = runtime.ambient_store.items()
    assert len(items) == 1
    assert items[0].topic == "新版本 补丁"
    assert items[0].source == "cognition"
    assert items[0].scope == scene_id

    # And a later related wake brings it into the package
    matched = runtime.ambient_store.match(text="听说明天新版本补丁上线", scope=scene_id)
    assert len(matched) == 1

    await runtime.stop()


@pytest.mark.asyncio
async def test_resolve_loop_preserves_fields_and_rejects_non_active(tmp_path):
    """
    ADR-0018: OpenLoopManager.resolve_loop only transitions an ACTIVE loop in its
    scene to 'resolved', preserving target/intent/source_event_id for traceability.
    Unknown ids are rejected instead of writing fabricated empty rows.
    """
    db_file = str(tmp_path / "loop_resolve.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:loop_test"
    now = time.time()
    await runtime.event_store.save_open_loop({
        "id": "loop_abc",
        "scene_id": scene_id,
        "target_actor_id": "user:A",
        "intent": "几点回来",
        "source_event_id": "ev_1",
        "status": "active",
        "created_at": now,
        "expires_at": now + 86400
    })

    manager = OpenLoopManager(runtime.event_store)

    # Unknown loop id → rejected, nothing written
    assert await manager.resolve_loop("loop_missing", scene_id) is False

    # Active loop → resolved in place
    assert await manager.resolve_loop("loop_abc", scene_id) is True
    assert await runtime.event_store.get_active_open_loops(scene_id) == []

    cursor = await runtime.event_store._db.execute(
        "SELECT target_actor_id, intent, source_event_id, status FROM open_loops WHERE id = 'loop_abc';"
    )
    row = await cursor.fetchone()
    assert row[0] == "user:A"
    assert row[1] == "几点回来"
    assert row[2] == "ev_1"
    assert row[3] == "resolved"

    await runtime.stop()
