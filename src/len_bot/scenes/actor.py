"""One writer for scene facts, conversation commits and reflection receipts."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from len_bot.cognition.models import EpisodeOutcome, FinalDisposition
from len_bot.events.models import Event, EventType
from len_bot.events.store import ReflectionConflictError
from len_bot.runtime.gate import GateDecision
from len_bot.scenes.models import SceneSession
from len_bot.scenes.reducer import SceneReducer

logger = logging.getLogger(__name__)


class SceneCommitConflict(RuntimeError):
    pass


@dataclass
class CommitCommand:
    outcome: EpisodeOutcome
    through_rowid: int
    source_event_ids: list[str]
    knowledge_revision: int
    mailbox: Any
    gate: Any
    future: asyncio.Future
    operator: bool = False


@dataclass
class ReflectionCommand:
    kwargs: dict
    future: asyncio.Future


class SceneActor:
    def __init__(self, scene_id, bot_actor_id, event_store, on_state_updated=None):
        self.scene_id = scene_id
        self.bot_actor_id = bot_actor_id
        self.event_store = event_store
        self.on_state_updated = on_state_updated
        self.session: SceneSession | None = None
        self._queue = asyncio.Queue()
        self._worker_task = None
        self._active_mailbox = None

    async def start(self):
        if self._worker_task:
            return
        saved = await self.event_store.load_scene_session(self.scene_id)
        self.session = SceneSession.model_validate(saved) if saved else SceneSession(scene_id=self.scene_id)
        self._worker_task = asyncio.create_task(self._process_loop())

    async def stop(self):
        if self._active_mailbox:
            self._active_mailbox.cancel('Runtime stopped')
        if self._worker_task:
            self._worker_task.cancel()
            await asyncio.gather(self._worker_task, return_exceptions=True)
            self._worker_task = None
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if hasattr(item, 'future') and not item.future.done():
                item.future.cancel()
            self._queue.task_done()

    def post_event(self, event):
        self._queue.put_nowait(event)

    def acquire_episode_lease(self, episode_id, mailbox):
        if self._active_mailbox is not None:
            return False
        self._active_mailbox = mailbox
        return True

    def release_episode_lease(self, episode_id):
        if self._active_mailbox and self._active_mailbox.episode_id == episode_id:
            self._active_mailbox = None

    def has_active_episode(self):
        return self._active_mailbox is not None

    async def commit_turn(self, outcome, through_rowid, source_event_ids, knowledge_revision, mailbox, gate, *, operator=False):
        future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(CommitCommand(outcome, through_rowid, source_event_ids, knowledge_revision, mailbox, gate, future, operator))
        return await future

    async def commit_reflection(self, **kwargs):
        future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(ReflectionCommand(kwargs, future))
        return await future

    async def _process_loop(self):
        while True:
            item = await self._queue.get()
            try:
                if isinstance(item, CommitCommand):
                    result = await self._commit_turn(item)
                    if not item.future.done(): item.future.set_result(result)
                elif isinstance(item, ReflectionCommand):
                    result = await self._commit_reflection(item.kwargs)
                    if not item.future.done(): item.future.set_result(result)
                else:
                    await self._commit_event(item)
            except asyncio.CancelledError:
                if hasattr(item, 'future') and not item.future.done(): item.future.cancel()
                raise
            except Exception as error:
                if hasattr(item, 'future') and not item.future.done():
                    item.future.set_exception(error)
                else:
                    logger.exception('Event commit failed in %s', self.scene_id)
            finally:
                self._queue.task_done()

    async def _commit_event(self, event):
        if event.scene_id != self.scene_id:
            raise ValueError('Event belongs to another scene')
        if await self.event_store.event_exists(event.id, self.scene_id):
            return
        if event.event_type == EventType.TASK_DUE and event.payload.get('task_id'):
            event.metadata['obsolete_task_wake'] = not await self.event_store.task_due_is_current(event)
        if event.event_type in {EventType.AGENT_JOB_FINISHED, EventType.AGENT_JOB_PROGRESS}:
            job = await self.event_store.get_job(event.payload.get('job_id'), self.scene_id)
            allowed = {'result_ready'} if event.event_type == EventType.AGENT_JOB_FINISHED else {'processing', 'result_ready'}
            event.metadata['obsolete_job_result'] = not job or job['revision'] != event.payload.get('job_revision') or job['status'] not in allowed
        bookkeeping = event.event_type in {EventType.MEDIA_UPDATED, EventType.AGENT_JOB_CHECKPOINT, EventType.AGENT_JOB_CONTROL} or (
            event.event_type == EventType.TOOL_OBSERVATION_RECORDED) or (
            event.event_type == EventType.TASK_DUE and event.payload.get('payload', {}).get('kind') == 'agent_job')
        candidate = SceneReducer.reduce(self.session, event, self.bot_actor_id)
        rowid = await self.event_store.commit_scene_event(
            event, candidate.model_dump(),
            task_id_to_trigger=event.payload.get('task_id') if event.event_type == EventType.TASK_DUE else None,
            associated_open_loop=event.metadata.get('associated_open_loop') if event.event_type == EventType.MESSAGE_SENT else None,
            advance_session_observation=not bookkeeping)
        if not bookkeeping: candidate.last_observed_event_rowid = rowid
        self.session = candidate
        event.metadata['_rowid'] = rowid
        if self._active_mailbox: self._active_mailbox.post(event)
        if self.on_state_updated: await self.on_state_updated(candidate, event)

    async def _commit_turn(self, item):
        if not item.operator and self._active_mailbox is not item.mailbox:
            raise SceneCommitConflict('Episode lease changed')
        if item.mailbox.is_cancelled():
            raise SceneCommitConflict(item.mailbox.cancellation_reason())
        event_id = 'turn:' + item.mailbox.episode_id
        if await self.event_store.event_exists(event_id, self.scene_id):
            return GateDecision(FinalDisposition.SILENCE, 'Turn already committed', accepted=True)
        state = self.session
        if state.knowledge_revision != item.knowledge_revision:
            raise SceneCommitConflict('Knowledge revision changed')
        if not state.last_cognized_event_rowid <= item.through_rowid <= state.last_observed_event_rowid:
            raise SceneCommitConflict('Read cutoff is outside the unconsumed range')
        bounded = not item.outcome.requires_fresh_input()
        if not bounded and item.through_rowid != state.last_observed_event_rowid:
            raise SceneCommitConflict('Control or fulfilment proposal has unread scene input')
        refs = set(item.source_event_ids)
        for proposal in item.outcome.task_proposals + item.outcome.job_proposals:
            refs.update(proposal.source_event_ids)
        for proposal in item.outcome.memory_proposals:
            refs.update(proposal.evidence)
        if not await self.event_store.references_belong_to_scene(refs, self.scene_id, item.through_rowid):
            raise SceneCommitConflict('Evidence is outside the scene or read cutoff')
        event = Event(id=event_id, event_type=EventType.CONVERSATION_COMMITTED, scene_id=self.scene_id,
                      actor_id='system:conversation', timestamp=self.event_store.clock(),
                      payload={'source_event_ids': item.source_event_ids, 'outcome': item.outcome.model_dump(mode='json')},
                      metadata={'through_event_rowid': item.through_rowid, 'mode': item.mailbox.origin_mode})
        candidate = SceneReducer.reduce(state, event, self.bot_actor_id)
        candidate.last_cognized_event_rowid = state.last_cognized_event_rowid if item.operator else item.through_rowid
        if item.outcome.memory_proposals: candidate.knowledge_revision += 1
        decision = await item.gate.evaluate_and_commit(item.outcome, item.mailbox, state,
            scene_commit={'event': event, 'scene_state_data': candidate.model_dump(), 'advance_session_observation': False},
            bounded_chat=bounded)
        if decision.accepted:
            self.session = candidate
        return decision

    async def _commit_reflection(self, kwargs):
        if self.session.knowledge_revision != kwargs['expected_revision']:
            raise ReflectionConflictError('Knowledge changed before reflection commit')
        event = kwargs['review_event']
        candidate = SceneReducer.reduce(self.session, event, self.bot_actor_id)
        if kwargs['proposals']: candidate.knowledge_revision += 1
        result, rowid = await self.event_store.commit_reflection_batch(self.scene_id, **kwargs,
            scene_state_data=candidate.model_dump(), bot_actor_id=self.bot_actor_id)
        if event.payload.get('review_items'):
            candidate.last_observed_event_rowid = rowid
        self.session = candidate
        event.metadata['_rowid'] = rowid
        if self.on_state_updated: await self.on_state_updated(candidate, event)
        return result
