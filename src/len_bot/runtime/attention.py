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
    EventType.AGENT_JOB_PROGRESS, EventType.MESSAGE_SEND_FAILED,
    EventType.LIVE_STARTED, EventType.LIVE_ENDED, EventType.USER_JOINED,
    EventType.TOOL_COMPLETED, EventType.REFLECTION_RECORDED,
}


class AttentionPolicy:
    def __init__(self, config, clock, random_source=random.random):
        self.config, self.clock, self.random_source = config, clock, random_source

    def apply(self, state, event, bot_actor_id, *, in_flight=(), work_participants=()):
        now = self.clock()
        state.focused_participants = {actor: until for actor, until in state.focused_participants.items() if until > now}
        if (event.event_type == EventType.MESSAGE_SENT and event.actor_id == bot_actor_id
                and not event.metadata.get('simulated') and event.payload.get('delivery_status', 'sent') == 'sent'):
            for actor in event.payload.get('response_actor_ids', []):
                if actor != bot_actor_id:
                    state.focused_participants[actor] = now + self.config.attention_focus_seconds

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
                state.focused_participants[event.actor_id] = now + self.config.attention_focus_seconds
            if event.actor_id in in_flight:
                reasons.append('in_flight_follow_up')
            if event.actor_id in work_participants:
                reasons.append('work_participant')
            certain = bool(reasons)
            if not certain:
                if (any(word and word.casefold() in text for word in self.config.attention_keywords)
                        and (state.attention_keyword_at is None
                             or now - state.attention_keyword_at >= self.config.attention_keyword_cooldown_seconds)):
                    reasons.append('keyword_opportunity')
                    state.attention_keyword_at = now
                window = int(now // self.config.attention_sample_window_seconds)
                if window > state.attention_sample_window:
                    state.attention_sample_window = window
                    if self.random_source() < self.config.attention_sample_probability:
                        reasons.append('sample_opportunity')
        elif event.event_type in RUNTIME_INPUTS:
            stale = event.metadata.get('obsolete_task_wake') or event.metadata.get('obsolete_job_result')
            bookkeeping = (event.event_type == EventType.TASK_DUE
                           and event.payload.get('payload', {}).get('kind') == 'agent_job')
            review = event.event_type != EventType.REFLECTION_RECORDED or event.metadata.get('needs_review')
            if not stale and not bookkeeping and review:
                reasons.append('runtime:' + event.event_type.value.lower())
                certain = True
        event.metadata['attention_reasons'] = reasons
        event.metadata['attention_certain'] = certain
        if reasons:
            state.pending_wakes.append(PendingWake(event_id=event.id, actor_id=event.actor_id,
                                                  reasons=reasons, certain=certain))


def record_scanned_event(state, event_id, rowid):
    """Complete the event position after INSERT, inside the same transaction."""
    state.attention_scanned_event_rowid = rowid
    for wake in state.pending_wakes:
        if wake.event_id == event_id:
            wake.rowid = rowid
