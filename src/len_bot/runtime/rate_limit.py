"""Hourly ceilings on what the Bot actually said in a group.

The window is the Bot's own delivered messages, never the messages it
received: a busy group is not itself a reason to go quiet.  Counting from the
event log rather than a counter means a restart neither forgets nor doubles an
allowance, and the operator can audit any refusal against the same rows.

Reaching a ceiling stops chat from entering the model at all.  Silence alone
would save nothing — the input tokens are already spent once a turn starts —
so the check belongs in front of the turn, not in front of the send.  Plugin
commands and background pushes never travel this path and are unaffected.
"""
from __future__ import annotations

WINDOW_SECONDS = 3600.0
# One SQL scan serves every message in a burst; the window is an hour, so a
# few seconds of staleness cannot carry a scene past its ceiling.
CACHE_SECONDS = 5.0
# A ceiling explains itself once; after that the silence speaks for itself.
NOTICE_INTERVAL_SECONDS = 600.0


class MessageRateLimiter:
    def __init__(self, event_store, config_provider, clock):
        self.event_store, self.config_provider, self.clock = event_store, config_provider, clock
        self._cache: dict[str, tuple[float, int, dict[str, int]]] = {}

    def _limits(self) -> tuple[int, int]:
        config = self.config_provider()
        return config.scene_hourly_message_limit, config.user_hourly_message_limit

    async def _counts(self, scene_id: str) -> tuple[int, dict[str, int]]:
        now = self.clock()
        cached = self._cache.get(scene_id)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1], cached[2]
        total, per_user = await self.event_store.sent_message_counts(scene_id, now - WINDOW_SECONDS)
        self._cache[scene_id] = (now, total, per_user)
        return total, per_user

    async def status(self, scene_id: str, requester_qq_uid=None) -> dict:
        """What this scene and requester have left; the single source both
        the admission checks and the operator panel read."""
        scene_limit, user_limit = self._limits()
        # Ceilings are a group-room concern; a one-to-one conversation going
        # silent mid-exchange would read as a fault, not as restraint.
        if not scene_id.startswith('group:') or (not scene_limit and not user_limit):
            return {'scene_exhausted': False, 'user_exhausted': False, 'scene_used': 0, 'user_used': 0,
                    'scene_limit': scene_limit, 'user_limit': user_limit}
        total, per_user = await self._counts(scene_id)
        used = per_user.get(str(requester_qq_uid), 0) if requester_qq_uid is not None else 0
        return {
            'scene_exhausted': bool(scene_limit and total >= scene_limit),
            'user_exhausted': bool(user_limit and requester_qq_uid is not None and used >= user_limit),
            'scene_used': total, 'user_used': used,
            'scene_limit': scene_limit, 'user_limit': user_limit,
        }

    async def exhausted(self, scene_id: str, requester_qq_uid=None) -> bool:
        state = await self.status(scene_id, requester_qq_uid)
        return state['scene_exhausted'] or state['user_exhausted']

    def note_send(self, scene_id: str) -> None:
        """Drop the cached window so a send inside it is visible immediately."""
        self._cache.pop(scene_id, None)


def limit_notice(state: dict) -> str:
    """The one sentence a mention gets while a ceiling holds."""
    if state['scene_exhausted']:
        return (f"本群这一小时我已经发了 {state['scene_used']} 条（上限 {state['scene_limit']}），"
                "先安静一会儿。日程命令和直播推送不受影响。")
    return (f"这一小时我回你已经 {state['user_used']} 条了（上限 {state['user_limit']}），"
            "让我缓缓，过会儿再找我。日程命令还能用。")
