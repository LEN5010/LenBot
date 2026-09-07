"""Production Actor/Gate transactions against an isolated event database."""

import asyncio
from dataclasses import dataclass, field

import pytest
import pytest_asyncio

from len_bot.cognition.jobs import JobProposal
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal, TaskProposal
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.memory.history import HistoryConflictError, history_source_text
from len_bot.memory.models import MemoryProposal
from len_bot.memory.store import MemoryStore
from len_bot.runtime.gate import RuntimeGate
from len_bot.runtime.attention import AttentionPolicy
from len_bot.config import RuntimeConfig
from len_bot.cognition.agent_loop import FreshInputConflict
from len_bot.scenes.actor import SceneActor, SceneCommitConflict


SCENE = "group:transaction-test"
BOT = "user:999"


@dataclass
class RecordingQueue:
    """Capture authorized actions without creating transport success receipts."""

    actions: list = field(default_factory=list)

    def enqueue(self, action):
        self.actions.append(action)


@pytest_asyncio.fixture
async def scene(tmp_path):
    store = EventStore(str(tmp_path / "scene.db"), clock=lambda: 100.0)
    await store.initialize()
    memory = MemoryStore(store._db, store._write_lock, clock=store.clock)
    await memory.initialize()
    actor = SceneActor(SCENE, BOT, store, attention_policy=AttentionPolicy(RuntimeConfig(bot_qq=999, attention_sample_probability=0), store.clock))
    await actor.start()
    queue = RecordingQueue()
    gate = RuntimeGate(store, queue, bot_actor_id=BOT, origin_mode_provider=lambda: "live")
    try:
        yield actor, store, memory, queue, gate
    finally:
        await actor.stop()
        await store.close()


async def post(actor, event):
    actor.post_event(event)
    await asyncio.wait_for(actor._queue.join(), timeout=2)
    return event


def human(event_id="input:1", actor_id="user:A", **sender):
    return Event(id=event_id, event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=SCENE,
                 actor_id=actor_id, timestamp=100.0, payload={"raw_text": "请认真回答", "at_bot": True, "sender": sender})


def lease(actor, episode_id="episode:1"):
    mailbox = EpisodeMailbox(episode_id, SCENE, actor.session.version)
    assert actor.acquire_episode_lease(episode_id, mailbox)
    return mailbox


def reply(**kwargs):
    return EpisodeOutcome(disposition=FinalDisposition.ACTION, decision_reason="回应当前原话",
                          message_proposals=[MessageProposal(content="知道了", **kwargs)])


def belief(event_id="input:1"):
    return MemoryProposal(subject="user:A", kind="preference", statement="A明确希望认真回应",
                          basis="reported", evidence=[event_id])


def open_loop(loop_id="loop:one"):
    return {"id": loop_id, "scene_id": SCENE, "target_actor_id": "user:A", "intent": "核对想法",
            "status": "active", "created_at": 100.0, "expires_at": 300.0, "source_event_id": ""}


@pytest.mark.asyncio
async def test_identity_facts_are_incremental_and_only_confirmed_bot_speech_counts(scene):
    actor, store, _memory, _queue, _gate = scene
    await post(actor, human(nickname="旧昵称", card="群名片"))
    await post(actor, human("input:2", nickname="新昵称"))
    await post(actor, human("input:3", actor_id="user:B", nickname="新昵称"))
    assert actor.session.participants["user:A"].card == "群名片"
    assert len(actor.session.participants) == 2
    await post(actor, human("input:4", card=""))
    assert actor.session.participants["user:A"].display_name == "新昵称"
    for event_type, status in [(EventType.MESSAGE_SEND_FAILED, "unknown"), (EventType.ACTION_SHADOWED, "not_sent")]:
        await post(actor, Event(event_type=event_type, scene_id=SCENE, actor_id=BOT, timestamp=101,
                               payload={"content": "没有确认说出", "delivery_status": status},
                               metadata={"associated_open_loop": open_loop()}))
    assert actor.session.last_bot_message_event_id is None
    assert actor.session.consecutive_bot_messages == 0
    assert not await store.get_active_open_loops(SCENE)
    await post(actor, Event(id="delivery:other", event_type=EventType.MESSAGE_SENT, scene_id=SCENE,
                           actor_id="user:another-bot", timestamp=102, payload={"content": "其他账号"}))
    assert actor.session.last_bot_message_event_id is None
    sent = await post(actor, Event(id="delivery:one", event_type=EventType.MESSAGE_SENT, scene_id=SCENE,
                                  actor_id=BOT, timestamp=103, payload={"content": "已确认发送", "delivery_status": "sent"},
                                  metadata={"associated_open_loop": open_loop()}))
    assert actor.session.last_bot_message_event_id == sent.id
    assert actor.session.last_bot_message_at == 103 and actor.session.consecutive_bot_messages == 1
    loops = await store.get_active_open_loops(SCENE)
    assert len(loops) == 1 and loops[0]["source_event_id"] == sent.id
    persisted = await store.load_scene_session(SCENE)
    assert persisted == actor.session.model_dump()


@pytest.mark.asyncio
async def test_chat_commits_its_actual_read_cutoff_without_consuming_new_human_input(scene):
    actor, store, _memory, queue, gate = scene
    first = await post(actor, human())
    mailbox = lease(actor)
    read_cutoff = first.metadata["_rowid"]
    second = await post(actor, human("input:2"))
    decision = await actor.commit_turn(reply(), read_cutoff, [first.id], 0, mailbox, gate)
    assert decision.accepted and len(queue.actions) == 1
    assert [wake.event_id for wake in actor.session.pending_wakes] == [second.id]
    assert actor.session.last_observed_event_rowid == second.metadata["_rowid"]
    assert mailbox.has_unseen_interim()
    remaining = await store.get_events_since(SCENE, read_cutoff, event_types=[EventType.GROUP_MESSAGE_RECEIVED])
    assert [event.id for event in remaining] == [second.id]
    assert (await store.load_scene_session(SCENE)) == actor.session.model_dump()
    await store.recover_social_work()
    pending = await store.pending_runtime_events()
    assert pending == []
    assert (await store.load_scene_session(SCENE))["pending_wakes"][0]["event_id"] == second.id


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["task", "job", "fulfilment", "open_loop"])
async def test_control_and_fulfilment_never_commit_with_unread_input(scene, control):
    actor, store, memory, queue, gate = scene
    first = await post(actor, human())
    mailbox = lease(actor)
    await post(actor, human("input:2"))
    outcome = reply()
    if control == "task":
        outcome.task_proposals = [TaskProposal(proposal_id="remind", description="提醒", due_at=200, source_event_ids=[first.id])]
    elif control == "job":
        outcome.job_proposals = [JobProposal(proposal_id="work", goal="查询资料", source_event_ids=[first.id])]
    elif control == "fulfilment":
        outcome.message_proposals[0].fulfils_task_id = "task:pending"
    else:
        outcome.message_proposals[0].expect_reply = True
        outcome.message_proposals[0].reply_target = "user:A"
    before = actor.session.model_dump()
    with pytest.raises(FreshInputConflict, match="unread scene input"):
        await actor.commit_turn(outcome, first.metadata["_rowid"], [first.id], 0, mailbox, gate)
    assert actor.session.model_dump() == before
    assert not queue.actions and not await store.scene_tasks(SCENE)
    assert not await memory.query_memories([SCENE])
    assert not await store.event_exists("turn:" + mailbox.episode_id, SCENE)


@pytest.mark.asyncio
async def test_job_memory_loop_and_confirmation_rollback_together_on_storage_failure(scene, monkeypatch):
    actor, store, memory, queue, gate = scene
    await post(actor, Event(id="delivery:previous", event_type=EventType.MESSAGE_SENT, scene_id=SCENE,
                           actor_id=BOT, payload={"content": "等你回复"}, metadata={"associated_open_loop": open_loop()}))
    source = await post(actor, human())
    mailbox = lease(actor)
    before = actor.session.model_dump()
    original_write = store._write_scene_event

    async def fail_commit(event, *args, **kwargs):
        if event.event_type == EventType.CONVERSATION_COMMITTED:
            raise RuntimeError("isolated injected write failure")
        return await original_write(event, *args, **kwargs)

    monkeypatch.setattr(store, "_write_scene_event", fail_commit)
    outcome = reply(task_ref="work")
    outcome.job_proposals = [JobProposal(proposal_id="work", goal="查询资料", source_event_ids=[source.id])]
    outcome.task_proposals = [TaskProposal(proposal_id="remind", description="稍后提醒", due_at=200, source_event_ids=[source.id])]
    outcome.memory_proposals = [belief(source.id)]
    outcome.resolve_open_loop_ids = ["loop:one"]
    decision = await actor.commit_turn(outcome, source.metadata["_rowid"], [source.id], 0, mailbox, gate)
    assert not decision.accepted and "rollback" in decision.reason
    assert not queue.actions and not await store.scene_tasks(SCENE)
    assert not await memory.query_memories([SCENE])
    assert await (await store._db.execute("SELECT COUNT(*) FROM agent_jobs")).fetchone() == (0,)
    assert [item["id"] for item in await store.get_active_open_loops(SCENE)] == ["loop:one"]
    assert actor.session.model_dump() == before
    assert await store.load_scene_session(SCENE) == before
    assert not await store.event_exists("turn:" + mailbox.episode_id, SCENE)


@pytest.mark.asyncio
async def test_duplicate_episode_cannot_repeat_knowledge_or_enqueue_another_send(scene):
    actor, store, memory, queue, gate = scene
    source = await post(actor, human())
    mailbox = lease(actor)
    outcome = reply()
    outcome.memory_proposals = [belief(source.id)]
    first = await actor.commit_turn(outcome, source.metadata["_rowid"], [source.id], 0, mailbox, gate)
    second = await actor.commit_turn(outcome, source.metadata["_rowid"], [source.id], 0, mailbox, gate)
    assert first.accepted and second.accepted and "already committed" in second.reason
    assert len(queue.actions) == len(await memory.query_memories([SCENE])) == 1
    assert actor.session.knowledge_revision == 1
    assert await (await store._db.execute("SELECT COUNT(*) FROM events WHERE event_type='CONVERSATION_COMMITTED'")).fetchone() == (1,)


@pytest.mark.asyncio
async def test_lost_lease_rejects_the_old_turn(scene):
    actor, store, _memory, queue, gate = scene
    source = await post(actor, human())
    old = lease(actor, "episode:old")
    actor.release_episode_lease(old.episode_id)
    lease(actor, "episode:new")
    with pytest.raises(SceneCommitConflict, match="lease changed"):
        await actor.commit_turn(reply(), source.metadata["_rowid"], [source.id], 0, old, gate)
    assert not queue.actions
    assert not await store.event_exists("turn:" + old.episode_id, SCENE)


@pytest.mark.asyncio
async def test_history_revision_conflict_does_not_write_beliefs_receipt_or_coverage(scene):
    actor, store, memory, queue, gate = scene
    source = await post(actor, human())
    batch = await store.begin_history_batch(SCENE, min_tokens=1)
    mailbox = lease(actor)
    outcome = EpisodeOutcome(decision_reason="保存明确反馈", memory_proposals=[belief(source.id)])
    await actor.commit_turn(outcome, source.metadata["_rowid"], [source.id], 0, mailbox, gate)
    before = actor.session.model_dump()
    review = Event(id="history:stale", event_type=EventType.REFLECTION_RECORDED,
                   scene_id=SCENE, actor_id="system:maintenance", payload={"review_items": []})
    with pytest.raises(HistoryConflictError, match="Knowledge changed"):
        await actor.commit_history(batch_id=batch.id, proposals=[belief(source.id)], summary="A希望认真回应",
                                   key_event_ids=[source.id], review_event=review, expected_revision=0)
    assert not queue.actions and len(await memory.query_memories([SCENE])) == 1
    assert not await store.list_history_batches(SCENE, status="completed")
    assert not await store.event_exists(review.id, SCENE)
    assert actor.session.model_dump() == before


@pytest.mark.asyncio
async def test_history_is_atomic_and_covers_only_its_original_batch(scene):
    actor, store, memory, _queue, _gate = scene
    source = await post(actor, human())
    batch = await store.begin_history_batch(SCENE, min_tokens=1)
    later = await post(actor, human("input:2"))
    review = Event(id="history:one", event_type=EventType.REFLECTION_RECORDED,
                   scene_id=SCENE, actor_id="system:maintenance", payload={"review_items": []})
    committed = await actor.commit_history(batch_id=batch.id, proposals=[belief(source.id)],
        summary="A希望认真回应", key_event_ids=[source.id], review_event=review, expected_revision=0)
    assert len(committed) == 1 and actor.session.knowledge_revision == 1
    assert actor.session.last_observed_event_rowid == later.metadata["_rowid"]
    summaries = await store.list_history_batches(SCENE, status="completed")
    assert len(summaries) == 1 and summaries[0]["source_event_ids"] == [source.id]
    assert await store.load_scene_session(SCENE) == actor.session.model_dump()
    duplicate = review.model_copy(update={"id": "history:duplicate"}, deep=True)
    with pytest.raises(HistoryConflictError, match="completed"):
        await actor.commit_history(batch_id=batch.id, proposals=[], summary="重复结果", key_event_ids=[],
                                   review_event=duplicate, expected_revision=1)
    assert not await store.event_exists(duplicate.id, SCENE)
    next_batch = await store.begin_history_batch(SCENE, min_tokens=1)
    assert next_batch.source_event_ids == [later.id]
    assert len(await memory.query_memories([SCENE])) == 1


@pytest.mark.asyncio
async def test_history_rolls_back_summary_and_earlier_belief_when_later_source_is_invalid(scene):
    actor, store, memory, _queue, _gate = scene
    source = await post(actor, human())
    batch = await store.begin_history_batch(SCENE, min_tokens=1)
    before = actor.session.model_dump()
    review = Event(id="history:invalid", event_type=EventType.REFLECTION_RECORDED,
                   scene_id=SCENE, actor_id="system:maintenance", payload={"review_items": []})
    impossible = MemoryProposal(subject=BOT, kind="fact", statement="Bot能登录MC服务器",
                                basis="reported", evidence=[source.id])
    with pytest.raises(ValueError, match="Bot capabilities"):
        await actor.commit_history(batch_id=batch.id, proposals=[belief(source.id), impossible],
            summary="A希望认真回应", key_event_ids=[source.id], review_event=review, expected_revision=0)
    assert not await memory.query_memories([SCENE])
    assert not await store.list_history_batches(SCENE, status="completed")
    assert not await store.event_exists(review.id, SCENE)
    assert actor.session.model_dump() == before
    assert await store.load_scene_session(SCENE) == before
    await store.fail_history_batch(batch.id, "ValueError")
    await post(actor, human("input:2"))
    assert await store.begin_history_batch(SCENE, min_tokens=1) is None
    retried = await store.retry_history_batch(batch.id)
    assert retried.model_dump() == batch.model_dump()


@pytest.mark.asyncio
async def test_large_history_event_is_segmented_without_false_complete_evidence_or_replay(scene):
    actor, store, memory, _queue, _gate = scene
    source = human()
    source.payload["raw_text"] = "活动条件：仅周末开放，尚未确认具体日期。" * 30
    await post(actor, source)
    original = history_source_text(source)
    text = []
    previous_end = 0
    while batch := await store.begin_history_batch(SCENE, target_tokens=400, min_tokens=1):
        assert batch.start_offset == previous_end
        assert batch.source_event_ids == [source.id] and batch.complete_event_ids == []
        assert (await store.load_history_batch(batch.id)).segments == batch.segments
        review = Event(event_type=EventType.REFLECTION_RECORDED, scene_id=SCENE,
                       actor_id="system:maintenance", payload={"review_items": []})
        with pytest.raises(ValueError, match="completely read"):
            await actor.commit_history(batch_id=batch.id, proposals=[belief(source.id)], summary="片段摘要",
                key_event_ids=[source.id], review_event=review, expected_revision=0)
        await actor.commit_history(batch_id=batch.id, proposals=[], summary="此片段提到周末开放，具体日期未确认。",
            key_event_ids=[source.id], review_event=review, expected_revision=0)
        text.append(batch.segments[0]["text"])
        previous_end = batch.end_offset
    assert "".join(text) == original and previous_end == len(original)
    assert not await memory.query_memories([SCENE])
    assert await store.begin_history_batch(SCENE, target_tokens=400, min_tokens=1) is None


@pytest.mark.asyncio
async def test_actor_rejects_an_event_misdirected_from_another_scene(scene):
    actor, store, _memory, _queue, _gate = scene
    await post(actor, human())
    before = actor.session.model_dump()
    foreign = human("foreign:input", actor_id="user:other", nickname="其他场景人物")
    foreign.scene_id = "group:foreign"
    await post(actor, foreign)
    assert actor.session.model_dump() == before
    assert not await store.event_exists(foreign.id, foreign.scene_id)
    assert await store.load_scene_session(foreign.scene_id) is None
