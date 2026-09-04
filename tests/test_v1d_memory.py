import pytest
import time
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.memory.models import MemoryProposal, MemoryStatus, MemoryCertainty, EpisodeRecord
from len_bot.memory.store import MemoryStore
from len_bot.memory.gate import MemoryGate
from len_bot.memory.reflection import ReflectionEngine

@pytest.mark.asyncio
async def test_v1d_memory_gate_evidence_and_conflict(tmp_path):
    """
    Proves V1-D:
    1. Anti-hallucination Evidence Gating: proposals without real evidence are rejected.
    2. Conflict Resolution: new conflicting beliefs supersede old entries rather than erasing history.
    """
    db_file = str(tmp_path / "v1d_mem.db")
    event_store = EventStore(db_file)
    await event_store.initialize()

    mem_store = MemoryStore(event_store._db)
    await mem_store.initialize()

    gate = MemoryGate(mem_store, event_store)

    # 1. Reject proposal with fake/hallucinated evidence
    fake_proposal = MemoryProposal(
        subject="user:1001",
        kind="preference",
        key="food",
        value="spicy",
        scope="group:100",
        evidence=["fake_event_99999"],
        human_readable_assertion="User 1001 likes spicy food"
    )
    fake_res = await gate.commit_proposal(fake_proposal)
    assert fake_res.success is False
    assert "Rejected" in fake_res.reason

    # 2. Plant a REAL event into EventStore
    real_event = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:100",
        actor_id="user:1001",
        timestamp=time.time(),
        payload={"raw_text": "我超喜欢蜀九香火锅"}
    )
    await event_store.append_event(real_event)

    # 3. Commit valid proposal backed by real evidence
    valid_prop1 = MemoryProposal(
        subject="user:1001",
        kind="preference",
        key="hotpot",
        value="shujiaxiang",
        certainty=MemoryCertainty.STRONG,
        scope="group:100",
        evidence=[real_event.id],
        human_readable_assertion="User 1001 prefers Shujiaxiang hotpot"
    )
    res1 = await gate.commit_proposal(valid_prop1)
    assert res1.success is True
    mem1_id = res1.memory_item.id

    active_mems = await mem_store.query_memories(allowed_scopes=["group:100"], subject="user:1001")
    assert len(active_mems) == 1
    assert active_mems[0].value == "shujiaxiang"
    assert active_mems[0].status == MemoryStatus.ACTIVE

    # 4. Later, User 1001 changes mind: "我不吃蜀九香了，改吃海底捞"
    real_event2 = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:100",
        actor_id="user:1001",
        timestamp=time.time() + 100,
        payload={"raw_text": "我不吃蜀九香了，改吃海底捞"}
    )
    await event_store.append_event(real_event2)

    # 5. Conflicting Proposal for same semantic slot (user:1001, preference, hotpot, group:100)
    conflict_prop = MemoryProposal(
        subject="user:1001",
        kind="preference",
        key="hotpot",
        value="haidilao",
        certainty=MemoryCertainty.EXPLICIT,
        scope="group:100",
        evidence=[real_event2.id],
        human_readable_assertion="User 1001 now prefers Haidilao hotpot"
    )
    res2 = await gate.commit_proposal(conflict_prop)
    assert res2.success is True

    # 6. Verify Old memory is SUPERSEDED, new is ACTIVE! (ADR-0011)
    cursor = await event_store._db.execute("SELECT id, value, status FROM memories WHERE subject = 'user:1001';")
    rows = await cursor.fetchall()
    assert len(rows) == 2  # Both records exist! History is not erased!

    row_map = {r[0]: (r[1], r[2]) for r in rows}
    assert row_map[mem1_id] == ("shujiaxiang", "superseded")
    assert row_map[res2.memory_item.id] == ("haidilao", "active")

    # query_memories only returns ACTIVE items
    query_res = await mem_store.query_memories(allowed_scopes=["group:100"], subject="user:1001")
    assert len(query_res) == 1
    assert query_res[0].value == "haidilao"

    await event_store.close()

@pytest.mark.asyncio
async def test_v1d_micro_reflection(tmp_path):
    """
    Tests Micro-Reflection L1 generation.
    """
    db_file = str(tmp_path / "v1d_ref.db")
    event_store = EventStore(db_file)
    await event_store.initialize()

    mem_store = MemoryStore(event_store._db)
    await mem_store.initialize()
    gate = MemoryGate(mem_store, event_store)

    reflection = ReflectionEngine(mem_store, gate)

    # Plant 3 events
    now = time.time()
    e1 = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:200", actor_id="user:A", timestamp=now, payload={"raw_text": "今晚直播几点"})
    e2 = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:200", actor_id="user:B", timestamp=now+1, payload={"raw_text": "八点开始"})
    await event_store.append_event(e1)
    await event_store.append_event(e2)

    # Run Micro Reflection
    ep_rec = await reflection.run_micro_reflection("group:200", [e1, e2])
    assert ep_rec is not None
    assert "直播" in ep_rec.tags
    assert len(ep_rec.source_event_ids) == 2

    # Verify L1 Episode is stored
    loaded_ep = await mem_store.get_episode(ep_rec.id)
    assert loaded_ep is not None
    assert loaded_ep.title == "直播话题讨论"

    await event_store.close()
