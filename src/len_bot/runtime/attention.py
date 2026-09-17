"""Decide observation opportunities; only SocialCognitionCore decides expression.

The SceneActor applies these rules before its event/session transaction. Clock
and random source are injected so sampling is once per durable time window.
"""
from __future__ import annotations

import random
import re

from len_bot.cognition.projection import project_onebot_text
from len_bot.events.models import EventType
from len_bot.scenes.models import PendingWake

HUMAN_INPUTS = {EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED}
RUNTIME_INPUTS = {
    EventType.TASK_DUE, EventType.TASK_REVIEW, EventType.AGENT_JOB_FINISHED,
    EventType.AGENT_JOB_PROGRESS, EventType.MESSAGE_SEND_FAILED, EventType.FILE_UPLOAD_FAILED, EventType.FILE_UPLOADED,
    EventType.LIVE_STARTED, EventType.LIVE_ENDED, EventType.USER_JOINED,
    EventType.TOOL_COMPLETED, EventType.REFLECTION_RECORDED,
}


def is_real_send(event, bot_actor_id):
    return (event.event_type == EventType.MESSAGE_SENT and event.actor_id == bot_actor_id
            and not event.metadata.get('simulated') and event.payload.get('delivery_status') == 'sent'
            and not event.payload.get('delivery_unknown') and event.payload.get('origin_mode') == 'live')


class AttentionPolicy:
    def __init__(self, config, clock, random_source=random.random):
        self.config, self.clock, self.random_source = config, clock, random_source
        self.chat_allowed = None
        self.time_settings = None
        self.effective_attention = None

    def _attention(self, scene_id):
        if self.effective_attention is not None:
            return self.effective_attention(scene_id)
        from types import SimpleNamespace
        return SimpleNamespace(sample_probability=self.config.attention_sample_probability,
            sample_window_seconds=self.config.attention_sample_window_seconds,
            keyword_cooldown_seconds=self.config.attention_keyword_cooldown_seconds,
            focus_seconds=self.config.attention_focus_seconds, keywords=self.config.attention_keywords)

    def apply(self, state, event, bot_actor_id, *, in_flight=(), work_participants=(),
              awaiting_response=(), focus_renewal_actors=()):
        now = self.clock()
        attention = self._attention(event.scene_id)
        state.focused_participants = {actor: until for actor, until in state.focused_participants.items() if until > now}
        if event.metadata.get('conversation_excluded'):
            event.metadata['attention_reasons'] = []
            event.metadata['attention_certain'] = False
            return
        if is_real_send(event, bot_actor_id):
            renewed = []
            for actor in sorted(set(focus_renewal_actors)):
                if actor != bot_actor_id:
                    state.focused_participants[actor] = now + attention.focus_seconds
                    renewed.append(actor)
            event.metadata['focus_renewed_actor_ids'] = renewed

        reasons = []
        certain = False
        if event.event_type in HUMAN_INPUTS and event.actor_id != bot_actor_id:
            # A CQ reply identifies an old message; its quoted text is never a
            # new nickname/keyword match. Only the current message is scanned.
            text = project_onebot_text(re.sub(r'\[CQ:[^\]]*\]', '', event.raw_text)).casefold()
            names = [self.config.identity_name, *self.config.address_names]
            if event.event_type == EventType.PRIVATE_MESSAGE_RECEIVED:
                reasons.append('private_message')
            if event.is_mention_bot or any('user:' + actor == bot_actor_id for actor in
                    re.findall(r'\[CQ:at,qq=(\d+)(?:,[^\]]*)?\]', event.raw_text)):
                reasons.append('mention')
            if event.is_reply_bot:
                reasons.append('reply_to_bot')
            if any(name and name.casefold() in text for name in names):
                reasons.append('address_name')
            if event.actor_id in state.focused_participants:
                reasons.append('continuing_interaction')
            if event.actor_id in in_flight:
                reasons.append('in_flight_follow_up')
            if event.actor_id in work_participants:
                reasons.append('work_participant')
            if event.actor_id in awaiting_response:
                reasons.append('awaiting_response')
            certain = bool(reasons)
            if not certain:
                if (any(word and word.casefold() in text for word in attention.keywords)
                        and (state.attention_keyword_at is None
                             or now - state.attention_keyword_at >= attention.keyword_cooldown_seconds)):
                    reasons.append('keyword_opportunity')
                    state.attention_keyword_at = now
                if state.attention_sample_at is None:
                    # A window index from an earlier configuration cannot be compared
                    # once the window length changes, so the next opportunity becomes
                    # an absolute time.  A session that already sampled under the old
                    # coordinate waits one full window; a new session may sample now.
                    state.attention_sample_at = (now + attention.sample_window_seconds
                                                 if state.attention_sample_window >= 0 else now)
                # A shortened window takes effect from here instead of after the old
                # one would have ended.  This moves one pending deadline closer; it
                # never adds a second draw inside the same window.
                state.attention_sample_at = min(state.attention_sample_at,
                                                now + attention.sample_window_seconds)
                if now >= state.attention_sample_at:
                    state.attention_sample_at = now + attention.sample_window_seconds
                    state.attention_sample_window = int(now // attention.sample_window_seconds)
                    if self.random_source() < attention.sample_probability:
                        reasons.append('sample_opportunity')
        elif event.event_type in RUNTIME_INPUTS:
            stale = event.metadata.get('obsolete_task_wake') or event.metadata.get('obsolete_job_result')
            due_kind = event.payload.get('payload', {}).get('kind')
            bookkeeping = (event.event_type == EventType.TASK_DUE
                           and due_kind in {'agent_job', 'heartbeat', 'heartbeat_occupancy', 'interest_share', 'deferred_delivery'})
            review = event.event_type != EventType.REFLECTION_RECORDED or event.metadata.get('needs_review')
            if not stale and not bookkeeping and review:
                reasons.append('runtime:' + event.event_type.value.lower())
                certain = True
        event.metadata['attention_reasons'] = reasons
        event.metadata['attention_certain'] = certain
        fast = {'mention', 'reply_to_bot', 'address_name', 'awaiting_response', 'in_flight_follow_up',
                'wake_confirmation_reply', 'private_message'}
        event.metadata['attention_lane'] = 'fast' if set(reasons) & fast else 'slow' if reasons else 'none'
        if event.event_type in HUMAN_INPUTS and event.actor_id != bot_actor_id:
            from len_bot.runtime.sleep_policy import note_human
            uid = event.actor_id[5:] if event.actor_id.startswith('user:') else None
            allowed = True if self.chat_allowed is None else self.chat_allowed(event.scene_id, uid)
            note_human(state, event, now, reasons, allowed, self.time_settings() if self.time_settings else None)
            if 'wake_confirmation_reply' in reasons:
                certain = True
                event.metadata['attention_certain'] = True
                event.metadata['attention_lane'] = 'fast'
        if reasons:
            state.pending_wakes.append(PendingWake(event_id=event.id, actor_id=event.actor_id,
                                                  reasons=reasons, certain=certain))


def record_scanned_event(state, event_id, rowid):
    """Complete the event position after INSERT, inside the same transaction."""
    state.attention_scanned_event_rowid = rowid
    for wake in state.pending_wakes:
        if wake.event_id == event_id:
            wake.rowid = rowid
