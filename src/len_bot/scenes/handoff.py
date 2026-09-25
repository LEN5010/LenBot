"""Unfinished items a conversation segment carries when its content leaves.

A segment loses content only when the Actor saves a smaller window or no
longer carries older native groups. What left may have pointed at work still
running, a question still waiting or a send whose result is unknown; those
facts stay in their own stores. This module picks them by fixed rules and
keeps only their identities. It never closes, cancels or spends anything.
"""
from __future__ import annotations

from len_bot.scenes.models import SegmentHandoffItem

# The list stays bounded; the oldest identities leave first.
HANDOFF_LIMIT = 40
# Approvals scanned for sends made by dropped native groups.
OUTBOUND_SCAN = 50
FINISHED_WORK = {'cancelled', 'completed', 'shadow_observed'}
SETTLED_SENDS = {'sent', 'simulated_sent'}


async def handoff_states(store, session, items, *, bot_actor_id, now, cutoff):
    """Current state of each item that is still unfinished, by item key."""
    states = {}
    if not items:
        return states
    kinds = {item.kind for item in items}
    wakes = {wake.event_id: wake for wake in session.pending_wakes}
    jobs = {job['id']: job for job in await store.list_jobs(session.scene_id)} if 'work' in kinds else {}
    loops = ({loop['id']: loop for loop in await store.get_active_open_loops(session.scene_id)}
             if 'open_loop' in kinds else {})
    sends = ({f"{fact['approval_event_id']}#{fact['batch_index']}": fact for fact in await store.outbound_message_facts(
        session.scene_id, cutoff, bot_actor_id=bot_actor_id, limit=OUTBOUND_SCAN)} if 'outbound' in kinds else {})
    for item in items:
        if item.kind == 'pending_source' and item.id in wakes:
            states[(item.kind, item.id)] = wakes[item.id]
        elif item.kind == 'work' and item.id in jobs and jobs[item.id]['status'] not in FINISHED_WORK:
            states[(item.kind, item.id)] = jobs[item.id]
        elif item.kind == 'open_loop' and item.id in loops and loops[item.id]['expires_at'] > now:
            states[(item.kind, item.id)] = loops[item.id]
        elif item.kind == 'outbound' and item.id in sends and sends[item.id]['status'] not in SETTLED_SENDS:
            states[(item.kind, item.id)] = sends[item.id]
    return states


async def collect_handoff(store, session, previous, event_ids, exchanges, *, bot_actor_id, now, cutoff):
    """The handoff a saved segment keeps, or the previous one when nothing left."""
    if previous is None:
        return []
    kept_calls = {ident for exchange in exchanges for ident in exchange.call_ids}
    dropped_events = set(previous.event_ids) - set(event_ids)
    dropped_groups = [exchange for exchange in previous.ordered_items if not set(exchange.call_ids) & kept_calls]
    if not dropped_events and not dropped_groups:
        return list(previous.handoff)
    candidates = list(previous.handoff)
    candidates += [SegmentHandoffItem(kind='pending_source', id=wake.event_id)
                   for wake in sorted(session.pending_wakes, key=lambda wake: wake.rowid)
                   if wake.event_id in dropped_events]
    if dropped_events:
        for job in await store.list_jobs(session.scene_id):
            if job['request_source_event_id'] in dropped_events or set(job['source_event_ids']) & dropped_events:
                candidates.append(SegmentHandoffItem(kind='work', id=job['id']))
        for loop in await store.get_active_open_loops(session.scene_id):
            if loop['source_event_id'] in dropped_events:
                candidates.append(SegmentHandoffItem(kind='open_loop', id=loop['id']))
    # A committed terminal in a group that left may have sends still unsettled.
    episodes = {exchange.episode_id for exchange in dropped_groups
                if any(reply.kind == 'receipt' and reply.committed for reply in exchange.replies)}
    if episodes:
        for fact in await store.outbound_message_facts(session.scene_id, cutoff, bot_actor_id=bot_actor_id,
                                                       limit=OUTBOUND_SCAN):
            if fact['batch_id'] in episodes:
                candidates.append(SegmentHandoffItem(kind='outbound',
                                                     id=f"{fact['approval_event_id']}#{fact['batch_index']}"))
    unique = list(dict.fromkeys(candidates))
    states = await handoff_states(store, session, unique, bot_actor_id=bot_actor_id, now=now, cutoff=cutoff)
    return [item for item in unique if (item.kind, item.id) in states][-HANDOFF_LIMIT:]
