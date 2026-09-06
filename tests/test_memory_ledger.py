"""Deterministic checks for source authority, revision history and local preferences."""

import json

import aiosqlite
import pytest
import pytest_asyncio
from pydantic import ValidationError

from len_bot.memory.models import MemoryProposal, MemoryStatus
from len_bot.memory.store import MemoryStore
from len_bot.memory.writes import commit_memory_proposal_core, validate_memory_proposal


SCENE = "group:one"
BOT = "user:bot"


@pytest_asyncio.fixture
async def ledger():
    async with aiosqlite.connect(":memory:") as db:
        await db.execute("CREATE TABLE events(id TEXT PRIMARY KEY,event_type TEXT,scene_id TEXT,actor_id TEXT,payload TEXT)")
        rows = [
            ("a", "GROUP_MESSAGE_RECEIVED", SCENE, "user:A", {}),
            ("b", "GROUP_MESSAGE_RECEIVED", SCENE, "user:B", {}),
            ("foreign", "GROUP_MESSAGE_RECEIVED", "group:two", "user:A", {}),
            ("bot", "MESSAGE_SENT", SCENE, BOT, {}),
            ("trace", "SOCIAL_COGNITION_RECORDED", SCENE, "system:core", {}),
            ("derived", "TOOL_OBSERVATION_RECORDED", SCENE, "system:tool", {"independent_evidence": False}),
            ("web", "TOOL_OBSERVATION_RECORDED", SCENE, "system:tool", {"independent_evidence": True}),
            ("future", "GROUP_MESSAGE_RECEIVED", SCENE, "user:A", {}),
        ]
        await db.executemany("INSERT INTO events VALUES(?,?,?,?,?)", [(*row[:4], json.dumps(row[4])) for row in rows])
        await db.commit()
        store = MemoryStore(db, clock=lambda: 100.0)
        await store.initialize()
        yield db, store


def change(**updates):
    return MemoryProposal(**{
        "subject": "user:A", "kind": "preference", "statement": "A希望少开玩笑",
        "basis": "reported", "evidence": ["a"], **updates,
    })


async def commit(db, proposal, *, cutoff=8, now=100.0, event_id="cognition:1"):
    await validate_memory_proposal(db, proposal, SCENE, cutoff, bot_actor_id=BOT)
    result = await commit_memory_proposal_core(db, proposal, SCENE, now, revision_event_id=event_id)
    await db.commit()
    return result


@pytest.mark.asyncio
async def test_scope_is_runtime_owned_and_explicit_feedback_is_immediately_projected(ledger):
    db, store = ledger
    saved = await commit(db, change(scope="global-safe"))
    assert saved.scope == SCENE
    assert [item.id for item in await store.interaction_preferences(SCENE, ["user:A"])] == [saved.id]
    assert not await store.interaction_preferences(SCENE, ["user:B"])
    assert not await store.query_memories(["group:two", "global-safe"])
    assert await store.get_memory_in_scopes(saved.id, ["group:two"]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("source,cutoff", [("foreign", 8), ("future", 7), ("trace", 8), ("derived", 8), ("bot", 8)])
async def test_invalid_provenance_cannot_become_a_belief(ledger, source, cutoff):
    db, store = ledger
    with pytest.raises(ValueError):
        await validate_memory_proposal(db, change(evidence=[source]), SCENE, cutoff, bot_actor_id=BOT)
    assert not await store.query_memories([SCENE])


@pytest.mark.asyncio
async def test_bot_utterance_does_not_prove_its_own_real_world_ability(ledger):
    db, _store = ledger
    with pytest.raises(ValueError, match="Bot capabilities"):
        await validate_memory_proposal(
            db, change(subject=BOT, kind="fact", statement="我已经注册MC", evidence=["a", "bot"]),
            SCENE, 8, bot_actor_id=BOT,
        )
    with pytest.raises(ValueError, match="observed participant"):
        await validate_memory_proposal(db, change(subject="user:unknown"), SCENE, 8, bot_actor_id=BOT)


@pytest.mark.asyncio
async def test_relationship_requires_human_speech_and_carries_inference_label(ledger):
    db, store = ledger
    with pytest.raises(ValueError, match="human original speech"):
        await validate_memory_proposal(
            db, change(kind="relationship", basis="inferred", evidence=["bot"]), SCENE, 8, bot_actor_id=BOT,
        )
    item = await commit(db, change(kind="relationship", basis="inferred", evidence=["a", "bot"], statement="A与Bot有玩笑互动"))
    assert item.basis == "inferred"
    assert not await store.interaction_preferences(SCENE, ["user:A"])


@pytest.mark.asyncio
async def test_personal_preference_needs_the_persons_statement(ledger):
    db, _store = ledger
    with pytest.raises(ValueError, match="that person's own statement"):
        await validate_memory_proposal(db, change(evidence=["b"]), SCENE, 8, bot_actor_id=BOT)


@pytest.mark.asyncio
async def test_revision_keeps_original_claim_and_evidence(ledger):
    db, store = ledger
    old = await commit(db, change())
    new = await commit(db, change(
        operation="supersede", target_memory_ids=[old.id], reason="A澄清只是今天",
        statement="A今天希望少开玩笑", evidence=["future"], expires_at=200,
    ), event_id="cognition:2")
    prior = await store.get_memory_in_scopes(old.id, [SCENE])
    assert prior.statement == "A希望少开玩笑" and prior.evidence == ["a"]
    assert prior.status == MemoryStatus.SUPERSEDED and prior.superseded_by == new.id
    assert prior.created_event_id == "cognition:1" and prior.revision_event_id == "cognition:2"
    assert new.supersedes_ids == [old.id]
    assert [item.id for item in await store.interaction_preferences(SCENE, ["user:A"])] == [new.id]
    assert not await store.interaction_preferences(SCENE, ["user:A"], now=200)
    with pytest.raises(ValueError, match="no longer active"):
        await commit(db, MemoryProposal(operation="refute", target_memory_ids=[old.id], reason="再次撤回", evidence=["future"]))
    refuted = await commit(db, MemoryProposal(
        operation="refute", target_memory_ids=[new.id], reason="A撤回偏好", evidence=["future"],
    ), event_id="cognition:3")
    assert refuted.statement == new.statement and refuted.evidence == ["future"]
    assert refuted.status == MemoryStatus.REFUTED
    assert len(await store.query_memories([SCENE], include_superseded=True)) == 2
    assert not await store.interaction_preferences(SCENE, ["user:A"])


@pytest.mark.asyncio
async def test_conflicting_revision_does_not_partially_retire_other_target(ledger):
    db, store = ledger
    first = await commit(db, change())
    second = await commit(db, change(statement="A希望直接回答"))
    await commit(db, MemoryProposal(operation="refute", target_memory_ids=[second.id], reason="A撤回", evidence=["a"]))
    with pytest.raises(ValueError, match="no longer active"):
        await commit(db, change(operation="supersede", target_memory_ids=[first.id, second.id], reason="合并"))
    assert (await store.get_memory_in_scopes(first.id, [SCENE])).status == MemoryStatus.ACTIVE


@pytest.mark.asyncio
async def test_local_preference_projection_excludes_inferences_and_respects_group_and_expiry(ledger):
    db, store = ledger
    group = await commit(db, change(subject=SCENE, kind="group_norm", statement="群友明确希望先说结论"))
    await commit(db, change(basis="inferred", statement="A可能不喜欢玩笑"))
    await commit(db, change(statement="A现在想安静", expires_at=110))
    assert [item.id for item in await store.interaction_preferences(SCENE, ["user:B"])] == [group.id]
    assert len(await store.interaction_preferences(SCENE, ["user:A"], now=105)) == 2
    assert len(await store.interaction_preferences(SCENE, ["user:A"], now=110)) == 1
    changes_before = db.total_changes
    await store.query_memories([SCENE], query="A")
    assert db.total_changes == changes_before


def test_old_wire_format_is_rejected():
    with pytest.raises(ValidationError):
        MemoryProposal(subject="user:A", key="style", value="温柔", evidence=["a"])


@pytest.mark.asyncio
async def test_initialization_does_not_silently_migrate_or_delete_old_knowledge():
    async with aiosqlite.connect(":memory:") as db:
        await db.execute("CREATE TABLE memories(id TEXT,key TEXT,value TEXT)")
        await db.execute("INSERT INTO memories VALUES('old','style','温柔')")
        await db.commit()
        with pytest.raises(RuntimeError, match="approved conversation reset"):
            await MemoryStore(db).initialize()
        assert await (await db.execute("SELECT value FROM memories")).fetchone() == ("温柔",)


@pytest.mark.asyncio
async def test_independent_observation_can_refute_a_report_without_reclassifying_it(ledger):
    db, store = ledger
    old = await commit(db, change(kind="fact", statement="A说活动在今天"))
    refuted = await commit(db, MemoryProposal(
        operation="refute", target_memory_ids=[old.id], reason="已读取原始公告更正时间", evidence=["web"],
    ))
    assert refuted.basis == "reported" and refuted.evidence == ["a"]
    assert refuted.status == "refuted" and refuted.revision_evidence == ["web"]
    assert not await store.query_memories([SCENE])


@pytest.mark.asyncio
async def test_reflection_has_no_execution_tool_and_only_stages_changes(ledger, monkeypatch):
    from len_bot.cognition.agent_loop import TerminalArgumentError, ToolArgumentError
    from len_bot.events.models import Event, EventType
    from len_bot.memory import reflector
    from len_bot.memory.reflection import ReflectionEngine

    db, store = ledger
    existing = await commit(db, change())

    class TestLoop:
        def __init__(self, gateway):
            pass

        async def run(self, **kwargs):
            assert [tool["function"]["name"] for tool in kwargs["tool_definitions"]()] == ["query_memory"]
            assert kwargs["terminal"]["function"]["name"] == "finish_reflection"
            with pytest.raises(ToolArgumentError):
                await kwargs["execute_tool"]("create_task", {})
            rows = await kwargs["execute_tool"]("query_memory", {"subject": "user:A"})
            assert [row["id"] for row in rows] == [existing.id]
            with pytest.raises(TerminalArgumentError):
                await kwargs["finish"]({"memory_proposals": [change(evidence=["foreign"]).model_dump(exclude={"scope"})]})
            return await kwargs["finish"]({
                "memory_proposals": [change(statement="A明确希望认真回应").model_dump(exclude={"scope"})],
                "review_items": [{"summary": "核对偏好是否只适用本次", "source_event_ids": ["a"]}],
            })

    monkeypatch.setattr(reflector, "ModelGateway", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(reflector, "AgentLoop", TestLoop)
    engine = ReflectionEngine(store, llm_reflector=reflector.LLMReflector(lambda: object(), memory_store=store))
    event = Event(id="a", event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=SCENE, actor_id="user:A", payload={"raw_text": "认真回答就好"})
    before = db.total_changes
    result = await engine.reflect_on_events(SCENE, [event])
    assert len(result.memory_proposals) == len(result.review_items) == 1
    assert result.memory_proposals[0].scope == SCENE
    assert db.total_changes == before
    assert len(await store.query_memories([SCENE])) == 1


@pytest.mark.asyncio
async def test_reflection_does_not_generate_a_fake_success_when_unconfigured(ledger):
    from len_bot.events.models import Event, EventType
    from len_bot.memory.reflection import ReflectionEngine

    _db, store = ledger
    event = Event(id="a", event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=SCENE, actor_id="user:A")
    with pytest.raises(RuntimeError, match="configured work-profile"):
        await ReflectionEngine(store).reflect_on_events(SCENE, [event])
