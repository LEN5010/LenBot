import pytest
import asyncio
import time
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.events.models import Event, EventType
from len_bot.memory.models import EpisodeRecord, MemoryProposal, MemoryKind, MemoryCertainty
from len_bot.cognition.providers import ProviderConfig, RoutingConfig, RouteTarget

@pytest.mark.asyncio
async def test_startup_wires_llm_reflector_when_provider_configured(tmp_path):
    """ADR-0028, §11: AgentRuntime.start() must wire LLMReflector when provider is configured."""
    db_file = str(tmp_path / "wiring_order.db")
    config = RuntimeConfig(
        bot_qq=12345678,
        db_path=db_file,
        openai_base_url="https://mock.provider.ai/v1",
        openai_api_key="sk-testkey123"
    )
    runtime = AgentRuntime(config)
    await runtime.start()

    # Provider registry has live provider from default config
    assert runtime.provider_registry.has_live_provider() is True
    # Reflection engine MUST have live llm_reflector wired!
    assert runtime.reflection_engine.llm_reflector is not None

    await runtime.stop()


@pytest.mark.asyncio
async def test_batch_cursor_no_event_skipping(tmp_path):
    """ADR-0028, §10.1: get_unreflected_events consumes batch limit 30 without skipping older events."""
    db_file = str(tmp_path / "batch_cursor.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:batch_test"
    # Insert 45 events
    for i in range(45):
        await runtime.event_store.append_event(Event(
            id=f"ev_{i}",
            event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id=scene_id,
            actor_id="user:1",
            timestamp=time.time() + i,
            payload={"raw_text": f"message {i}"}
        ))

    # Batch 1: should return exactly 30 events
    batch_1 = await runtime.event_store.get_unreflected_events(scene_id, after_rowid=0, limit=30)
    assert len(batch_1) == 30
    assert batch_1[0].id == "ev_0"
    assert batch_1[-1].id == "ev_29"
    max_rowid_1 = int(batch_1[-1].metadata["_rowid"])

    # Batch 2: should return remaining 15 events
    batch_2 = await runtime.event_store.get_unreflected_events(scene_id, after_rowid=max_rowid_1, limit=30)
    assert len(batch_2) == 15
    assert batch_2[0].id == "ev_30"
    assert batch_2[-1].id == "ev_44"

    await runtime.stop()


@pytest.mark.asyncio
async def test_atomic_reflection_batch_commit_rollback_on_invalid_proposal(tmp_path):
    """ADR-0028, §10.2: Any failure in proposal validation rolls back entire reflection batch."""
    db_file = str(tmp_path / "atomic_reflection.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:atomic_ref"
    ev = Event(
        id="ev_valid",
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id=scene_id,
        actor_id="user:1",
        timestamp=time.time(),
        payload={"raw_text": "real text"}
    )
    await runtime.event_store.append_event(ev)
    saved_evs = await runtime.event_store.get_unreflected_events(scene_id, after_rowid=0, limit=1)
    cursor_rowid = int(saved_evs[0].metadata["_rowid"])

    episode = EpisodeRecord(
        id="ep_test_rollback",
        scene_id=scene_id,
        title="Test Rollback",
        summary="summary",
        source_event_ids=["ev_valid"],
        participants=["user:1"],
        created_at=time.time()
    )

    # Proposal with fake evidence not in DB -> must fail validation!
    bad_proposal = MemoryProposal(
        subject="user:1",
        kind=MemoryKind.FACT,
        key="fake_key",
        value="fake_val",
        human_readable_assertion="assertion",
        certainty=MemoryCertainty.STRONG,
        evidence=["fake_nonexistent_event_id"]
    )

    with pytest.raises(ValueError) as excinfo:
        await runtime.event_store.commit_reflection_batch(
            scene_id=scene_id,
            episode_record=episode,
            proposals=[bad_proposal],
            new_cursor_rowid=cursor_rowid
        )

    assert "evidence" in str(excinfo.value).lower() or "integrity" in str(excinfo.value).lower()

    # Entire batch must be rolled back!
    # 1. Episode not saved
    ep_check = await runtime.memory_store.get_episode("ep_test_rollback")
    assert ep_check is None

    # 2. Reflection cursor still 0
    cur_check = await runtime.memory_store.get_reflection_cursor(scene_id)
    assert cur_check == 0

    await runtime.stop()
