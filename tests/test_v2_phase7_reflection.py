import pytest
import asyncio
import time
from types import SimpleNamespace

from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.events.models import Event, EventType
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal
from len_bot.memory.models import (
    EpisodeRecord,
    MemoryProposal,
    MemoryItem,
    MemoryCertainty,
    MemoryKind,
    MemoryStatus,
)
from len_bot.memory.reflection import ReflectionEngine
from len_bot.memory.reflector import LLMReflector


def _msg(scene_id, actor_id, text, t, **extra):
    payload = {"raw_text": text, **extra}
    return Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id=actor_id,
        timestamp=t,
        payload=payload
    )


@pytest.mark.asyncio
async def test_quiet_window_reflection_single_episode_per_block(tmp_path):
    """
    ADR-0019 §10.3: a conversation block followed by a quiet window produces
    EXACTLY ONE episode. The old '==5 intervening messages' count trigger is gone.
    """
    config = RuntimeConfig(
        bot_qq=12345678,
        db_path=str(tmp_path / "quiet.db"),
        reflection_quiet_window_seconds=0.1
    )
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:quiet"
    t0 = time.time()
    for i in range(6):
        await runtime.receive_event(_msg(scene_id, f"user:{i % 3}", f"闲聊内容{i}", t0 + i))
    await asyncio.sleep(0.5)

    episodes = await runtime.memory_store.get_episodes(scene_id)
    assert len(episodes) == 1
    # Fallback episode covers the whole unreflected range
    assert len(episodes[0].source_event_ids) == 6

    # Quiet again with NO new events → no new episodes
    await asyncio.sleep(0.3)
    assert len(await runtime.memory_store.get_episodes(scene_id)) == 1

    await runtime.stop()


@pytest.mark.asyncio
async def test_reflection_cursor_covers_only_increment(tmp_path):
    """
    ADR-0019 §10.4: the cursor advances to the last reflected rowid; a second
    quiet window reflects ONLY the new events — the same range is never
    summarized twice.
    """
    config = RuntimeConfig(
        bot_qq=12345678,
        db_path=str(tmp_path / "cursor.db"),
        reflection_quiet_window_seconds=0.1
    )
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:cursor"
    t0 = time.time()
    first_block = [_msg(scene_id, "user:1", f"第一段{i}", t0 + i) for i in range(3)]
    for e in first_block:
        await runtime.receive_event(e)
    await asyncio.sleep(0.5)

    # Second activity block after the first reflection
    t1 = time.time() + 10
    second_block = [_msg(scene_id, "user:2", f"第二段{i}", t1 + i) for i in range(2)]
    for e in second_block:
        await runtime.receive_event(e)
    await asyncio.sleep(0.5)

    episodes = await runtime.memory_store.get_episodes(scene_id)
    assert len(episodes) == 2
    first_ids = set(episodes[1].source_event_ids)  # oldest episode
    second_ids = set(episodes[0].source_event_ids)
    assert first_ids == {e.id for e in first_block}
    assert second_ids == {e.id for e in second_block}
    assert first_ids.isdisjoint(second_ids)

    await runtime.stop()


@pytest.mark.asyncio
async def test_llm_reflector_produces_evidence_gated_memories(tmp_path):
    """
    ADR-0019 §10.5: the LLM reflector is wired through ReflectionEngine; its
    MemoryProposals pass the MemoryGate with real evidence and land in L2 storage.
    """
    config = RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "reflector.db"))
    runtime = AgentRuntime(config)  # mock_pi_handler=None → production wiring path
    await runtime.start()

    # Simulated provider response (no network): typed proposal over the event range
    reflector_output = (
        '{"title": "开黑讨论", "summary": "A 说想玩新游戏但嫌累", "tags": ["游戏"], '
        '"memory_proposals": [{"subject": "user:A", "kind": "topic_interest", '
        '"key": "gaming", "value": "对某新游戏兴趣高但嫌开黑累", '
        '"certainty": "likely", "visibility": "scene", '
        '"human_readable_assertion": "A 对某新游戏兴趣高但嫌开黑累"}]}'
    )

    async def fake_create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reflector_output))])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    reflector = LLMReflector(resolver=lambda: (fake_client, "test-model"))

    scene_id = "group:reflect_llm"
    t0 = time.time()
    events = [_msg(scene_id, "user:A", "好想玩那个新游戏啊，但开黑好累", t0)]
    await runtime.receive_event(events[0])
    await asyncio.sleep(0.1)

    engine = ReflectionEngine(runtime.memory_store, runtime.memory_gate, llm_reflector=reflector)
    record = await engine.run_micro_reflection(scene_id, events)

    assert record.title == "开黑讨论"
    assert record.source_event_ids == [events[0].id]
    memories = await runtime.memory_store.query_memories(
        allowed_scopes=[scene_id, "global-safe"], subject="user:A"
    )
    assert len(memories) == 1
    assert memories[0].kind == MemoryKind.TOPIC_INTEREST
    assert memories[0].evidence == [events[0].id]

    await runtime.stop()


@pytest.mark.asyncio
async def test_memory_kind_legacy_pattern_migration(tmp_path):
    """
    ADR-0019 §10.2: legacy free-form kind='pattern' rows migrate one-time to
    'social_pattern'; MemoryKind validation then loads them without error.
    """
    config = RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "kinds.db"))
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:legacy"
    now = time.time()
    await runtime.memory_store._db.execute(
        """
        INSERT INTO memories (id, subject, kind, key, value, temporal, certainty, scope, visibility,
                              evidence, status, human_readable_assertion, created_at, last_confirmed_at)
        VALUES ('mem_legacy', 'user:X', 'pattern', 'activity', '晚上活跃', 'persistent',
                'likely', ?, 'scene', '[]', 'active', 'group:X 晚上活跃', ?, ?);
        """,
        (scene_id, now, now)
    )
    await runtime.memory_store._db.commit()

    # Fresh store instance on the same DB re-runs the one-time migration
    from len_bot.memory.store import MemoryStore
    second_store = MemoryStore(runtime.event_store._db, write_lock=runtime.event_store._write_lock)
    await second_store.initialize()

    memories = await second_store.query_memories(allowed_scopes=[scene_id])
    assert len(memories) == 1
    assert memories[0].kind == MemoryKind.SOCIAL_PATTERN

    await runtime.stop()


@pytest.mark.asyncio
async def test_person_card_in_situation_package(tmp_path):
    """
    ADR-0019 §11.1: the sender snapshot survives into the Situation Package as a
    person card (display name + group role + per-person memories) — no more bare
    'user:123456' in prompts.
    """
    prompts_seen = []

    async def mock_pi(messages):
        prompts_seen.append(messages)
        return EpisodeOutcome(disposition=FinalDisposition.SILENCE, thought="ok")

    config = RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "person.db"))
    runtime = AgentRuntime(config, mock_pi_handler=mock_pi)
    await runtime.start()

    scene_id = "group:person_card"
    # Per-person memory about user:A
    await runtime.memory_store.save_memory(MemoryItem(
        subject="user:A",
        kind=MemoryKind.RECURRING_ROLE,
        key="organizer",
        value="经常组织开黑活动",
        scope=scene_id,
        evidence=["seed"],
        human_readable_assertion="A 经常组织开黑活动",
        created_at=time.time(),
        last_confirmed_at=time.time()
    ))

    t0 = time.time()
    ev = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        timestamp=t0,
        payload={
            "raw_text": "@Bot 在吗",
            "at_bot": True,
            "sender": {"nickname": "小A", "card": "A班长", "role": "admin"}
        }
    )
    await runtime.receive_event(ev)
    await asyncio.sleep(0.3)

    assert len(prompts_seen) == 1
    user_content = prompts_seen[0][-1]["content"]
    actor_section = user_content.split("【CURRENT ACTOR】")[1].split("【ACTIVE OPEN LOOPS】")[0]
    assert "A班长" in actor_section          # display name: card wins over nickname
    assert "user:A" in actor_section          # actor id preserved
    assert "admin" in actor_section           # group role
    assert "A 经常组织开黑活动" in actor_section  # subject-scoped memory

    await runtime.stop()


@pytest.mark.asyncio
async def test_decay_sweeper_runs_in_maintenance_loop(tmp_path):
    """
    ADR-0019: decay_memories is part of the runtime maintenance heartbeat — a
    stale TENTATIVE belief decays below threshold and is forgotten without any
    manual invocation.
    """
    config = RuntimeConfig(
        bot_qq=12345678,
        db_path=str(tmp_path / "decay.db"),
        maintenance_interval_seconds=0.05
    )
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:decay"
    old = time.time() - 70 * 86400.0  # > 2 half-lives unconfirmed
    await runtime.memory_store.save_memory(MemoryItem(
        subject="user:B",
        kind=MemoryKind.PREFERENCE,
        key="food",
        value="可能喜欢香菜",
        certainty=MemoryCertainty.TENTATIVE,
        scope=scene_id,
        evidence=["seed"],
        human_readable_assertion="B 可能喜欢香菜",
        created_at=old,
        last_confirmed_at=old
    ))

    await asyncio.sleep(0.4)

    memories = await runtime.memory_store.query_memories(allowed_scopes=[scene_id])
    assert memories == []  # forgotten
    raw = await runtime.memory_store._db.execute("SELECT status FROM memories WHERE subject='user:B';")
    row = await raw.fetchone()
    assert row[0] == MemoryStatus.FORGOTTEN.value

    await runtime.stop()


@pytest.mark.asyncio
async def test_scenario_j_natural_memory_recall(tmp_path):
    """
    Scenario J (Goal 9 — 平常表现为'记得', 需要时才'翻记录'):
    A person memory exists from days ago. When A mentions the topic again, the
    Situation Package carries the belief and cognition answers NATURALLY.
    The prompt rules mandate natural recall and forbid record-recitation;
    retrieval tools are reserved for evidence requests.
    """
    prompts_seen = []
    sent_actions = []

    async def mock_send(item):
        sent_actions.append(item)
        return True

    async def mock_pi(messages):
        prompts_seen.append(messages)
        return EpisodeOutcome(
            disposition=FinalDisposition.ACTION,
            thought="I remember A said he was too lazy to play",
            message_proposals=[MessageProposal(content="你前几天不是还说懒得开黑么")]
        )

    config = RuntimeConfig(bot_qq=12345678, db_path=str(tmp_path / "recall.db"))
    runtime = AgentRuntime(config, send_adapter=mock_send, mock_pi_handler=mock_pi)
    await runtime.start()

    scene_id = "group:remember"
    await runtime.memory_store.save_memory(MemoryItem(
        subject="user:A",
        kind=MemoryKind.TOPIC_INTEREST,
        key="gaming",
        value="前几天聊过想玩某游戏但懒得开黑",
        scope=scene_id,
        evidence=["seed"],
        human_readable_assertion="A 前几天聊过想玩某游戏但懒得开黑",
        created_at=time.time(),
        last_confirmed_at=time.time()
    ))

    t0 = time.time()
    ev = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:A",
        timestamp=t0,
        payload={"raw_text": "@Bot 最近又想玩那个了", "at_bot": True}
    )
    await runtime.receive_event(ev)
    await asyncio.sleep(0.3)

    assert len(prompts_seen) == 1
    user_content = prompts_seen[0][-1]["content"]
    memory_section = user_content.split("【RELEVANT BELIEFS & MEMORY】")[1].split("【RELEVANT AMBIENT ITEMS】")[0]
    assert "A 前几天聊过想玩某游戏但懒得开黑" in memory_section
    # Rule 8: natural recall, evidence lookup only on demand
    assert "自然口吻" in prompts_seen[0][0]["content"]

    assert len(sent_actions) == 1
    assert sent_actions[0].content == "你前几天不是还说懒得开黑么"

    await runtime.stop()
