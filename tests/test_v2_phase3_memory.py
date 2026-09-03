import pytest
import asyncio
import time
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.events.models import Event, EventType
from len_bot.memory.models import MemoryItem, MemoryProposal, MemoryStatus, MemoryCertainty
from len_bot.tools.retrieval import RetrievalToolkit
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal

@pytest.mark.asyncio
async def test_scenario_d_cross_scene_preference_and_privacy_boundary(tmp_path):
    """
    Scenario D (Goal 4 & Invariant G, ADR-0024):
    - Public preference in Group 1 is promoted to global-safe scope and retrieved in Group 2.
    - Private chat secret is strictly blocked by SQL scope when queried in Group 2.
    """
    db_file = str(tmp_path / "scenario_d.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    actor_a = "user:1001"
    group_1 = "group:rust_study"
    group_2 = "group:system_programming"
    private_chat = f"private:{actor_a}"

    # 1. In Group 1: Seed real message and memory about Rust
    ev_rust = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=group_1,
        actor_id=actor_a,
        timestamp=time.time() - 3600 * 24 * 14,  # 2 weeks ago
        payload={"raw_text": "我最近在学 Rust，所有权机制真折磨"}
    )
    await runtime.event_store.commit_scene_event(ev_rust, {"scene_id": group_1})

    mem_rust = MemoryItem(
        subject=actor_a,
        kind="preference",
        key="programming_language",
        value="rust",
        certainty=MemoryCertainty.STRONG,
        scope=group_1,
        evidence=[ev_rust.id],
        status=MemoryStatus.ACTIVE,
        human_readable_assertion="User 1001 正在学习 Rust 编程语言并研究系统底层"
    )
    await runtime.memory_store.save_memory(mem_rust)
    promoted = await runtime.memory_store.promote_memory(mem_rust.id)
    assert promoted is not None
    assert promoted.scope == "global-safe"
    assert f"promoted_from:{mem_rust.id}:" in promoted.human_readable_assertion

    # 2. In Private Chat: Seed confidential secret (strictly scoped to private chat)
    ev_secret = Event(
        event_type=EventType.PRIVATE_MESSAGE_RECEIVED,
        scene_id=private_chat,
        actor_id=actor_a,
        timestamp=time.time() - 3600 * 24,
        payload={"raw_text": "我的银行卡密码是 987654"}
    )
    await runtime.event_store.commit_scene_event(ev_secret, {"scene_id": private_chat})

    mem_secret = MemoryItem(
        subject=actor_a,
        kind="fact",
        key="bank_card_password",
        value="987654",
        certainty=MemoryCertainty.EXPLICIT,
        scope=private_chat,
        evidence=[ev_secret.id],
        status=MemoryStatus.ACTIVE,
        human_readable_assertion="User 1001 的银行卡秘密密码是 987654"
    )
    await runtime.memory_store.save_memory(mem_secret)

    # 3. Two weeks later in Group 2: Agent operates under allowed_scopes=[group_2, "global-safe"]
    toolkit = RetrievalToolkit(
        event_store=runtime.event_store,
        allowed_scopes=[group_2, "global-safe"],
        default_scene_id=group_2,
        memory_store=runtime.memory_store
    )

    # PART 1: Query memories for actor_a
    # Rust preference (global) MUST be retrieved!
    mem_result = await toolkit.execute("query_memory", {"subject": actor_a})
    assert "Rust" in mem_result or "rust" in mem_result
    assert "正在学习 Rust" in mem_result

    # PART 2: Invariant G Privacy Enforcement
    # The private chat secret MUST NOT exist in query_memory results under [group_2]!
    assert "987654" not in mem_result
    assert "bank_card_password" not in mem_result

    # Even if an adversary explicitly queries with query="987654" or key="bank_card_password":
    adversary_query = await toolkit.execute("query_memory", {"subject": actor_a, "key": "bank_card_password"})
    assert "987654" not in adversary_query
    assert "未找到匹配" in adversary_query

    # Direct search_messages in Group 2 must also NEVER reveal private chat message!
    search_res = await toolkit.execute("search_messages", {"query": "银行卡"})
    assert "987654" not in search_res
    assert "未找到匹配" in search_res

    # 4. In Private Chat: Querying under allowed_scopes=[private_chat] CAN retrieve the secret
    private_toolkit = RetrievalToolkit(
        event_store=runtime.event_store,
        allowed_scopes=[private_chat],
        default_scene_id=private_chat,
        memory_store=runtime.memory_store
    )
    priv_mem = await private_toolkit.execute("query_memory", {"subject": actor_a, "key": "bank_card_password"})
    assert "987654" in priv_mem

    await runtime.stop()

@pytest.mark.asyncio
async def test_scenario_e_slot_superseding_and_belief_trajectory(tmp_path):
    """
    Scenario E (Goal 5 — 记忆冲突消解与演化历程):
    - Initial belief: A likes Sichuan hotpot.
    - Updated belief: A胃不好改吃清淡粤菜 (cantonese_food).
    - Memory 1 is marked SUPERSEDED, pointing to Memory 2 via superseded_by.
    - Querying active memories returns ONLY Cantonese food.
    - Querying with include_history=True returns the full evolution trajectory.
    """
    db_file = str(tmp_path / "scenario_e.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:foodies"
    actor_a = "user:2002"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    t0 = time.time()

    # 1. Plant Event 1: Likes Sichuan hotpot
    ev1 = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id=actor_a,
        timestamp=t0 - 3600 * 24 * 7,
        payload={"raw_text": "我最喜欢吃川味火锅"}
    )
    await runtime.event_store.commit_scene_event(ev1, {"scene_id": scene_id})

    prop1 = MemoryProposal(
        subject=actor_a,
        kind="preference",
        key="food",
        value="sichuan_hotpot",
        certainty=MemoryCertainty.STRONG,
        scope=scene_id,
        evidence=[ev1.id],
        human_readable_assertion="User 2002 最喜欢吃川味火锅"
    )
    res1 = await runtime.memory_gate.commit_proposal(prop1)
    assert res1.success is True
    mem1_id = res1.memory_item.id

    # 2. Plant Event 2: Changes to Cantonese food due to stomachache
    ev2 = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id=actor_a,
        timestamp=t0,
        payload={"raw_text": "我现在胃不好吃不了辣了，最近改吃清淡的粤菜了"}
    )
    await runtime.event_store.commit_scene_event(ev2, {"scene_id": scene_id})

    prop2 = MemoryProposal(
        subject=actor_a,
        kind="preference",
        key="food",
        value="cantonese_food",
        certainty=MemoryCertainty.EXPLICIT,
        scope=scene_id,
        evidence=[ev2.id],
        human_readable_assertion="User 2002 因胃不好改吃清淡粤菜"
    )
    res2 = await runtime.memory_gate.commit_proposal(prop2)
    assert res2.success is True
    mem2_id = res2.memory_item.id

    # 3. Assert Slot Superseding links in SQLite
    all_slots = await runtime.memory_store.get_memory_history(
        allowed_scopes=[scene_id],
        subject=actor_a,
        kind="preference",
        key="food"
    )
    assert len(all_slots) == 2

    # Map by status
    active_item = next(m for m in all_slots if m.status == MemoryStatus.ACTIVE)
    superseded_item = next(m for m in all_slots if m.status == MemoryStatus.SUPERSEDED)

    assert active_item.id == mem2_id
    assert active_item.value == "cantonese_food"
    assert superseded_item.id == mem1_id
    assert superseded_item.value == "sichuan_hotpot"
    assert superseded_item.superseded_by == mem2_id

    # 4. Toolkit query_memory behavior
    toolkit = RetrievalToolkit(
        event_store=runtime.event_store,
        allowed_scopes=[scene_id],
        default_scene_id=scene_id,
        memory_store=runtime.memory_store
    )

    # Standard query returns ONLY active Cantonese food
    active_view = await toolkit.execute("query_memory", {"subject": actor_a, "key": "food"})
    assert "粤菜" in active_view
    assert "川味火锅" not in active_view

    # History query returns full trajectory showing previous preference and update
    history_view = await toolkit.execute("query_memory", {
        "subject": actor_a,
        "key": "food",
        "include_history": True
    })
    assert "粤菜" in history_view
    assert "川味火锅" in history_view
    assert f"superseded_by: {mem2_id}" in history_view

    await runtime.stop()

@pytest.mark.asyncio
async def test_temporal_decay_and_sweeper(tmp_path):
    """
    Tests temporal decay of memory scores over time:
    - Tentative unaccessed memory decays and is marked FORGOTTEN.
    - Frequently accessed / strong memory maintains high decay_score.
    - Recent memory remains fresh.
    """
    db_file = str(tmp_path / "test_decay.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:decay_test"
    now = time.time()

    # 1. Tentative memory created 60 days ago with 0 accesses
    m_tentative = MemoryItem(
        subject="user:old",
        kind="fact",
        key="game",
        value="genshin",
        certainty=MemoryCertainty.TENTATIVE,
        scope=scene_id,
        status=MemoryStatus.ACTIVE,
        human_readable_assertion="疑似在玩原神",
        created_at=now - 86400 * 60,
        last_confirmed_at=now - 86400 * 60,
        access_count=0,
        decay_score=1.0
    )
    await runtime.memory_store.save_memory(m_tentative)

    # 2. Strong memory created 60 days ago but accessed 10 times
    m_strong = MemoryItem(
        subject="user:vip",
        kind="habit",
        key="sleep",
        value="night_owl",
        certainty=MemoryCertainty.STRONG,
        scope=scene_id,
        status=MemoryStatus.ACTIVE,
        human_readable_assertion="长期夜猫子",
        created_at=now - 86400 * 60,
        last_confirmed_at=now - 86400 * 60,
        access_count=10,
        decay_score=1.0
    )
    await runtime.memory_store.save_memory(m_strong)

    # 3. Recent memory created today
    m_recent = MemoryItem(
        subject="user:fresh",
        kind="preference",
        key="drink",
        value="cola",
        certainty=MemoryCertainty.LIKELY,
        scope=scene_id,
        status=MemoryStatus.ACTIVE,
        human_readable_assertion="喜欢喝可乐",
        created_at=now - 3600,
        last_confirmed_at=now - 3600,
        access_count=1,
        decay_score=1.0
    )
    await runtime.memory_store.save_memory(m_recent)

    # 4. Trigger decay sweeper with 30-day half life
    decayed_count = await runtime.memory_store.decay_memories(current_time=now, half_life_days=30.0)
    assert decayed_count == 3

    # 5. Check resulting statuses
    # Tentative memory decayed below 0.25 (factor=0.25) -> status becomes FORGOTTEN!
    tentative_rows = await runtime.memory_store.query_memories([scene_id], subject="user:old")
    assert len(tentative_rows) == 0  # no longer in active query!

    cursor = await runtime.event_store._db.execute("SELECT status, decay_score FROM memories WHERE id = ?;", (m_tentative.id,))
    row = await cursor.fetchone()
    assert row[0] == "forgotten"
    assert row[1] < 0.3

    # Strong memory reinforced by access_count remains active with healthy score
    strong_rows = await runtime.memory_store.query_memories([scene_id], subject="user:vip")
    assert len(strong_rows) == 1
    assert strong_rows[0].status == MemoryStatus.ACTIVE
    assert strong_rows[0].decay_score > 0.4

    # Recent memory remains fresh
    recent_rows = await runtime.memory_store.query_memories([scene_id], subject="user:fresh")
    assert len(recent_rows) == 1
    assert recent_rows[0].decay_score >= 0.9

    await runtime.stop()
