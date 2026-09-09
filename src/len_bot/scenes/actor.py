"""One writer for scene facts, conversation commits and history-maintenance receipts."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from len_bot.cognition.models import EpisodeOutcome, FinalDisposition
from len_bot.cognition.agent_loop import FreshInputConflict
from len_bot.events.models import Event, EventType
from len_bot.memory.history import HistoryConflictError
from len_bot.runtime.gate import CommittedProposal, GateDecision, PublicationRecord
from len_bot.scenes.models import SceneSession
from len_bot.scenes.reducer import SceneReducer
from len_bot.runtime.attention import HUMAN_INPUTS, is_real_send, record_scanned_event

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
class HistoryCommand:
    kwargs: dict
    future: asyncio.Future


class SceneActor:
    def __init__(self, scene_id, bot_actor_id, event_store, on_state_updated=None, *, attention_policy=None, classify_event=None):
        self.scene_id = scene_id
        self.bot_actor_id = bot_actor_id
        self.event_store = event_store
        self.on_state_updated = on_state_updated
        self.attention_policy = attention_policy
        self.classify_event = classify_event
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
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # The Actor may already be committing in its independent worker.
            # Recover that one decision; never repeat the transaction or send.
            if not mailbox.is_cancelled():
                mailbox.cancel('Caller cancelled while awaiting commit')
            decision = await asyncio.shield(future)
            if not decision.accepted:
                raise
            decision.publication.status = 'interrupted'
            decision.publication.phase = 'commit_acknowledgement'
            decision.publication.error = 'Caller cancelled while awaiting the committed turn'
            decision.publication.error_type = 'CancelledError'
            # The caller first receives the durable decision. Publication then
            # propagates cancellation without preparing or enqueueing actions.
            return decision

    async def commit_history(self, **kwargs):
        future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(HistoryCommand(kwargs, future))
        return await future

    async def _process_loop(self):
        while True:
            item = await self._queue.get()
            try:
                if isinstance(item, CommitCommand):
                    result = await self._commit_turn(item)
                    if not item.future.done(): item.future.set_result(result)
                elif isinstance(item, HistoryCommand):
                    result = await self._commit_history(item.kwargs)
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
        if self.classify_event:
            await self.classify_event(event, self.session.last_observed_event_rowid)
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
        if self.attention_policy:
            participants = set()
            waiting = set()
            focus_renewals = set()
            in_flight = set(self._active_mailbox.interaction_actors) if self._active_mailbox else set()
            if event.event_type in HUMAN_INPUTS:
                in_flight.update(await self.event_store.pending_response_actors(self.scene_id, self.bot_actor_id,
                    self.event_store.clock() - self.attention_policy.config.attention_focus_seconds))
                jobs = await self.event_store.list_jobs(self.scene_id)
                participants = {'user:' + job['requester_qq_uid'] for job in jobs if job['requester_qq_uid'] and job['status'] in {
                    'pending', 'claimed', 'processing', 'review_required', 'result_ready', 'awaiting_delivery',
                }}
                active_loops = [loop for loop in await self.event_store.get_active_open_loops(self.scene_id)
                                if loop['expires_at'] > self.event_store.clock()]
                loop_sources = await self.event_store.events_by_ids(self.scene_id,
                    {loop['source_event_id'] for loop in active_loops}, self.session.last_observed_event_rowid)
                delivered = {source.id for source in loop_sources if is_real_send(source, self.bot_actor_id)}
                waiting = {loop['target_actor_id'] for loop in active_loops if loop['source_event_id'] in delivered}
                if not event.is_reply_bot:
                    projected = await self.event_store.project_reply_context(
                        self.scene_id, [event], through_rowid=self.session.last_observed_event_rowid)
                    quote = projected[0].metadata.get('quote_context') or {}
                    if not quote.get('missing') and quote.get('actor_id') == self.bot_actor_id:
                        event.payload['reply_bot'] = True
            elif event.event_type == EventType.MESSAGE_SENT:
                focus_renewals = await self._focus_renewal_actors(event)
            self.attention_policy.apply(candidate, event, self.bot_actor_id,
                in_flight=in_flight,
                work_participants=participants, awaiting_response=waiting,
                focus_renewal_actors=focus_renewals)
        rowid = await self.event_store.commit_scene_event(
            event, candidate.model_dump(),
            task_id_to_trigger=event.payload.get('task_id') if event.event_type == EventType.TASK_DUE else None,
            associated_open_loop=event.metadata.get('associated_open_loop') if event.event_type == EventType.MESSAGE_SENT else None,
            advance_session_observation=not bookkeeping)
        if not bookkeeping: candidate.last_observed_event_rowid = rowid
        record_scanned_event(candidate, event.id, rowid)
        self.session = candidate
        event.metadata['_rowid'] = rowid
        if self.on_state_updated: await self.on_state_updated(candidate, event)

    async def _focus_renewal_actors(self, event):
        """Renew from this delivered message's real request or work relation."""
        if event.metadata.get('conversation_excluded') or not is_real_send(event, self.bot_actor_id):
            return set()
        responders = set(event.payload.get('response_actor_ids', []))
        if event.payload.get('requester_qq_uid'):
            responders.add('user:'+event.payload['requester_qq_uid'])
        related = set()
        source_id = event.payload.get('origin_event_id')
        if source_id:
            sources = await self.event_store.events_by_ids(self.scene_id, [source_id], self.session.last_observed_event_rowid)
            for source in sources:
                if source.event_type not in HUMAN_INPUTS:
                    continue
                reasons = set(source.metadata.get('attention_reasons', []))
                if (source.event_type == EventType.PRIVATE_MESSAGE_RECEIVED or source.is_mention_bot or source.is_reply_bot
                        or reasons.intersection({'mention', 'reply_to_bot', 'private_message', 'awaiting_response'})):
                    related.add(source.actor_id)
                    related.update(event.payload.get('response_actor_ids', []))
        task_ids = {event.payload[key] for key in ('job_id', 'fulfils_task_id', 'acknowledges_task_id')
                    if event.payload.get(key)}
        if task_ids:
            for task in await self.event_store.scene_tasks(self.scene_id):
                if task['id'] not in task_ids:
                    continue
                payload = task['payload']
                if payload.get('requester_qq_uid'):
                    related.add('user:' + payload['requester_qq_uid'])
                if payload.get('target_actor_id'):
                    related.add(payload['target_actor_id'])
        return responders.intersection(related)-set(event.payload.get('release_focus_actor_ids', []))

    async def _commit_turn(self, item):
        native_output = item.mailbox.output_kind in {'command', 'announcement'}
        if native_output:
            if item.operator or item.outcome.task_proposals or item.outcome.job_proposals or item.outcome.memory_proposals or item.outcome.resolve_open_loop_ids or item.outcome.release_focus_actor_ids:
                raise SceneCommitConflict('A command or announcement may only submit its own expression')
            await item.gate.validate_native_origin(item.mailbox, self.scene_id)
        if not item.operator and not native_output and self._active_mailbox is not item.mailbox:
            raise SceneCommitConflict('Episode lease changed')
        if item.mailbox.is_cancelled():
            raise SceneCommitConflict(item.mailbox.cancellation_reason())
        event_id = 'turn:' + item.mailbox.episode_id
        if await self.event_store.event_exists(event_id, self.scene_id):
            saved = (await self.event_store.read_context(event_id, 0, 0, [self.scene_id]))[0]
            outcome = EpisodeOutcome.model_validate(saved['payload']['outcome'])
            decision = GateDecision(outcome.disposition, 'Turn already committed; publication is not repeated',
                committed_proposal=CommittedProposal(episode_id=item.mailbox.episode_id,
                    scene_id=self.scene_id, outcome=outcome))
            decision.commit_event_id = event_id
            decision.action_ids = saved['payload'].get('action_ids', [])
            decision.publication = PublicationRecord(status='not_repeated', phase='previous_commit')
            return decision
        state = self.session
        if not native_output and state.knowledge_revision != item.knowledge_revision:
            raise SceneCommitConflict('Knowledge revision changed')
        if not 0 <= item.through_rowid <= state.last_observed_event_rowid:
            raise SceneCommitConflict('Read cutoff is outside the observed range')
        read = set(item.source_event_ids)
        handled = set(item.outcome.handled_source_event_ids)
        if handled and (item.operator or native_output):
            raise SceneCommitConflict('Operator and native outputs cannot handle conversation wake sources')
        if not handled.issubset(read):
            raise SceneCommitConflict('Handled wake sources were not actually read in this turn')
        pending_ids = {wake.event_id for wake in state.pending_wakes}
        if not handled.issubset(pending_ids):
            raise SceneCommitConflict('Handled sources are not currently pending in this scene')
        if set(item.outcome.release_focus_actor_ids)-await self.event_store.event_actors(self.scene_id,handled):
            raise SceneCommitConflict('Ending an interaction requires this participant\'s handled original')
        refs = set()
        for proposal in item.outcome.task_proposals + item.outcome.job_proposals:
            refs.update(proposal.source_event_ids)
            if proposal.request_source_event_id:
                refs.add(proposal.request_source_event_id)
        for message in item.outcome.message_proposals:
            if message.source_event_id:
                refs.add(message.source_event_id)
        for proposal in item.outcome.memory_proposals:
            refs.update(proposal.evidence)
        if not refs.issubset(read):
            raise SceneCommitConflict('Proposal evidence was located but not read in this turn')
        if not await self.event_store.references_belong_to_scene(read, self.scene_id, item.through_rowid):
            raise SceneCommitConflict('Evidence is outside the scene or read cutoff')
        if not item.operator and not native_output and item.outcome.requires_fresh_input():
            related = await self._related_unread_wakes(item.outcome, read, state, item.gate.scene_policy)
            if related:
                raise FreshInputConflict('Control or fulfilment has unread related input: ' + ', '.join(related))
        event = Event(id=event_id, event_type=EventType.CONVERSATION_COMMITTED, scene_id=self.scene_id,
                      actor_id='system:conversation', timestamp=self.event_store.clock(),
                      payload={'source_event_ids': item.source_event_ids,
                               'handled_source_event_ids': item.outcome.handled_source_event_ids,
                               'output_kind': item.mailbox.output_kind,
                               'origin_event_id': item.mailbox.origin_stimulus_id,
                               'outcome': item.outcome.model_dump(mode='json')},
                      metadata={'through_event_rowid': item.through_rowid, 'mode': item.mailbox.origin_mode,
                                'operator_control': item.operator,
                                'conversation_excluded': native_output or item.operator})
        candidate = SceneReducer.reduce(state, event, self.bot_actor_id)
        if item.outcome.memory_proposals: candidate.knowledge_revision += 1
        decision = await item.gate.evaluate_and_commit(item.outcome, item.mailbox, state,
            scene_commit={'event': event, 'scene_state_data': candidate.model_dump(), 'advance_session_observation': False},
            operator_control=item.operator)
        if decision.accepted:
            # No Scheduler or Action publication runs inside this commit. The
            # caller receives this durable decision after the Actor adopts it.
            self.session = candidate
        return decision

    async def _related_unread_wakes(self, outcome, read, state, scene_policy):
        """Require related input, using stored request and reply links only."""
        unread_ids = [wake.event_id for wake in state.pending_wakes if wake.certain and wake.event_id not in read]
        if not unread_ids:
            return []
        source_ids = set()
        request_ids = set()
        task_ids = set()
        requesters = set()
        message_ids = set()

        def requester(qq_uid):
            if qq_uid is not None:
                requesters.add(f'user:{qq_uid}')

        def request_source(event_id):
            if event_id:
                request_ids.add(event_id)
                source_ids.add(event_id)

        for proposal in outcome.job_proposals:
            source_ids.update(proposal.source_event_ids)
            request_source(proposal.request_source_event_id)
            requester(proposal.requester_qq_uid)
            if proposal.job_id:
                task_ids.add(proposal.job_id)
        for proposal in outcome.task_proposals:
            source_ids.update(proposal.source_event_ids)
            request_source(proposal.request_source_event_id)
            if proposal.requester_id:
                requesters.add(proposal.requester_id)
            if proposal.task_id:
                task_ids.add(proposal.task_id)
        for proposal in outcome.memory_proposals:
            source_ids.update(proposal.evidence)
            request_ids.update(proposal.evidence)
            if proposal.subject.startswith('user:'):
                requesters.add(proposal.subject)
            requesters.update(subject for subject in await self.event_store.memory_subjects(
                self.scene_id,proposal.target_memory_ids) if subject.startswith('user:'))
        requesters.update(outcome.release_focus_actor_ids)
        for message in outcome.message_proposals:
            request_source(message.source_event_id)
            requester(message.requester_qq_uid)
            requesters.update(message.addressed_to)
            requesters.update('user:'+part.qq_uid for part in message.segments if part.type=='at')
            if message.reply_to:
                message_ids.add(str(message.reply_to))
            if message.expect_reply and message.reply_target:
                requesters.add(message.reply_target)
            task_ids.update(ident for ident in (message.job_id, message.fulfils_task_id) if ident)
        if task_ids:
            for task in await self.event_store.scene_tasks(self.scene_id):
                if task['id'] not in task_ids:
                    continue
                payload = task['payload']
                requester(payload.get('requester_qq_uid'))
                request_source(payload.get('request_source_event_id'))
                source_ids.update(payload.get('source_event_ids', []))
        if outcome.resolve_open_loop_ids:
            for loop in await self.event_store.get_active_open_loops(self.scene_id):
                if loop['id'] not in outcome.resolve_open_loop_ids:
                    continue
                requesters.add(loop['target_actor_id'])
                source_ids.add(loop['source_event_id'])
                if loop['source_stimulus_id']:
                    source_ids.add(loop['source_stimulus_id'])

        sources = await self.event_store.events_by_ids(self.scene_id, source_ids, state.last_observed_event_rowid)
        for source in sources:
            if source.id in request_ids and source.event_type in HUMAN_INPUTS:
                requesters.add(source.actor_id)
            if source.payload.get('message_id') is not None:
                message_ids.add(str(source.payload['message_id']))
        pending = await self.event_store.events_by_ids(self.scene_id, unread_ids, state.last_observed_event_rowid)
        if scene_policy:
            pending = [event for event in pending if event.metadata.get('interaction') == 'chat'
                       and scene_policy.chat_allowed(self.scene_id,event.metadata.get('requester_qq_uid'))]
        projected = await self.event_store.project_reply_context(self.scene_id, pending,
            through_rowid=state.last_observed_event_rowid)
        quoted_ids = {event.metadata['quote_context']['event_id'] for event in projected
                      if event.metadata.get('quote_context') and not event.metadata['quote_context'].get('missing')}
        quoted = {event.id: event for event in await self.event_store.events_by_ids(
            self.scene_id, quoted_ids, state.last_observed_event_rowid)}

        def linked_payload(payload):
            return (any(payload.get(key) in task_ids for key in ('job_id', 'task_id', 'fulfils_task_id'))
                    or any(payload.get(key) in source_ids for key in ('source_event_id', 'origin_event_id', 'request_source_event_id'))
                    or payload.get('reply_to') is not None and str(payload['reply_to']) in message_ids)

        related = []
        for event in projected:
            quote_id = (event.metadata.get('quote_context') or {}).get('event_id')
            original = quoted.get(quote_id)
            reply_to = event.payload.get('reply_to_message_id')
            if (event.id in source_ids
                    or event.event_type in HUMAN_INPUTS and event.actor_id in requesters
                    or linked_payload(event.payload)
                    or reply_to is not None and str(reply_to) in message_ids
                    or quote_id in source_ids
                    or original is not None and linked_payload(original.payload)):
                related.append(event.id)
        return related

    async def _commit_history(self, kwargs):
        if self.session.knowledge_revision != kwargs['expected_revision']:
            raise HistoryConflictError('Knowledge changed before reflection commit')
        event = kwargs['review_event']
        candidate = SceneReducer.reduce(self.session, event, self.bot_actor_id)
        if kwargs['proposals']: candidate.knowledge_revision += 1
        if self.attention_policy:
            self.attention_policy.apply(candidate, event, self.bot_actor_id)
        result, rowid = await self.event_store.commit_history_batch(self.scene_id, **kwargs,
            scene_state_data=candidate.model_dump(), bot_actor_id=self.bot_actor_id)
        record_scanned_event(candidate, event.id, rowid)
        if event.payload.get('review_items'):
            candidate.last_observed_event_rowid = rowid
        self.session = candidate
        event.metadata['_rowid'] = rowid
        if self.on_state_updated: await self.on_state_updated(candidate, event)
        return result
