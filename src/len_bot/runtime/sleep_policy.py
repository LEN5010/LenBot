"""Per-scene sleep and wake: store messages, stop ordinary replies.

Sleep does not stop event storage or history maintenance.  It stops ordinary
social replies, new heartbeat publication and autonomous sends. A real address
opens a confirmation for this scene only.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


WAKE_SECONDS = 30 * 60
CONFIRM_SECONDS = 5 * 60
WAKE_REASONS = frozenset({'mention', 'address_name', 'private_message'})
DIRECT_REASONS = WAKE_REASONS | {'reply_to_bot', 'continuing_interaction', 'awaiting_response'}


class DeliveryDeferred(Exception):
    """The action is kept; it is not sent and not a send failure."""

    def __init__(self, due_at: float, detail: str):
        super().__init__(detail)
        self.due_at = due_at
        self.detail = detail


def _zone(time_settings):
    if time_settings is None:
        return None
    try:
        return ZoneInfo(time_settings.timezone)
    except Exception:
        return None


def _minutes(value: str) -> int:
    hour, minute = value.split(':')
    return int(hour) * 60 + int(minute)


def in_sleep_window(time_settings, now: float) -> bool:
    """Whether the global schedule says this instant is sleep.

    Unconfigured start/end means there is no sleep schedule.
    """
    if time_settings is None or time_settings.sleep_start is None or time_settings.sleep_end is None:
        return False
    zone = _zone(time_settings)
    if zone is None:
        return False
    local = datetime.fromtimestamp(now, zone)
    current = local.hour * 60 + local.minute
    start = _minutes(time_settings.sleep_start)
    end = _minutes(time_settings.sleep_end)
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def next_wake_at(time_settings, now: float) -> float:
    if time_settings is None or time_settings.sleep_end is None:
        return now
    zone = _zone(time_settings)
    if zone is None:
        return now
    local = datetime.fromtimestamp(now, zone)
    hour, minute = time_settings.sleep_end.split(':')
    candidate = local.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    if candidate.timestamp() <= now:
        from datetime import timedelta
        candidate = candidate + timedelta(days=1)
    return candidate.timestamp()


def is_asleep(session, time_settings, now: float) -> bool:
    if session.awake_until is not None and session.awake_until > now:
        return False
    return in_sleep_window(time_settings, now)


def note_human(session, event, now: float, reasons: list[str], chat_allowed: bool, time_settings=None) -> bool:
    """Only direct eligible interaction renews an already confirmed awake period."""
    if not event.actor_id.startswith('user:') or not chat_allowed:
        return False
    pending = session.wake_confirmation
    if pending and pending.expires_at <= now:
        session.wake_confirmation = pending = None
    if not is_asleep(session, time_settings, now) and DIRECT_REASONS.intersection(reasons):
        session.last_direct_human_at = now
        session.awake_until = now + WAKE_SECONDS
        return True
    if is_asleep(session, time_settings, now):
        if pending and pending.actor_id == event.actor_id and event.timestamp > pending.created_at:
            reasons.append('wake_confirmation_reply')
        elif pending is None and WAKE_REASONS.intersection(reasons):
            from len_bot.scenes.models import WakeConfirmationRequest
            session.wake_confirmation = WakeConfirmationRequest(request_event_id=event.id,
                actor_id=event.actor_id, created_at=event.timestamp, expires_at=now + CONFIRM_SECONDS)
    return False


def should_ingest_social(session, time_settings, now: float, reasons: list[str]) -> bool:
    if not is_asleep(session, time_settings, now):
        return True
    return bool((WAKE_REASONS | {'wake_confirmation_reply'}).intersection(reasons))


def should_defer_send(action, session, time_settings, now: float, *, deterministic_service=False) -> DeliveryDeferred | None:
    if deterministic_service:
        return None
    pending = session.wake_confirmation
    if (action.wake_confirmation_request_id and pending
            and action.wake_confirmation_request_id == pending.request_event_id
            and pending.expires_at > now):
        return None
    if not is_asleep(session, time_settings, now):
        return None
    return DeliveryDeferred(next_wake_at(time_settings, now),
                            '本群处于睡眠窗口，普通发送改为延期交付')
