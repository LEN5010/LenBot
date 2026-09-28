"""Wake a quiet group at most once per local day and record whether anyone answered."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Proactive, QuietHours
from .quiet import local_period, next_local_start, quiet_period
from .reply_effect_store import ReplyEffectStore
from .store import Store


PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "next_proactive.md"
PAUSE_SECONDS = 7 * 86400.0
OUTCOMES = ("silent", "answered", "ignored", "unobserved")
# Enough steps to pass a week-long pause, skipped days and quiet intervals.
_SEARCH_STEPS = 64


def idle_text(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分钟"
    return f"{hours} 小时" if hours else f"{minutes} 分钟"


class ProactiveStore:
    """Queries over proactive_wakes; the row itself is written with its turn in Store.start_turn."""

    def __init__(self, store: Store):
        self.store = store
        self.db = store.db

    def last_activity(self, scene: str, exclude_uids: tuple[str, ...]) -> float | None:
        """Host time of the latest human arrival or confirmed own send, excluding other bots."""
        placeholders = ",".join("?" for _ in exclude_uids)
        excluded = f" AND json_extract(body,'$.sender.uid') NOT IN ({placeholders})" if exclude_uids else ""
        human = self.db.execute(
            "SELECT MAX(received_at) FROM messages WHERE scene=? AND raw IS NOT NULL "
            "AND received_at IS NOT NULL AND json_extract(body,'$.is_self')=0" + excluded,
            (scene, *exclude_uids),
        ).fetchone()[0]
        own = self.store.last_self_time(scene)
        times = [value for value in (human, own) if value is not None]
        return max(times) if times else None

    def woke_on(self, scene: str, local_date: str) -> bool:
        return self.db.execute("SELECT 1 FROM proactive_wakes WHERE scene=? AND local_date=?",
                               (scene, local_date)).fetchone() is not None

    def pause(self, scene: str) -> dict | None:
        """The latest two observed spoken openings both ignored: pause from the later window end."""
        rows = self.db.execute(
            "SELECT p.id,p.outcome,(SELECT MAX(e.deadline) FROM reply_effects e "
            "WHERE e.scene=p.scene AND e.turn_id=p.turn_id) FROM proactive_wakes p "
            "WHERE p.scene=? AND p.assessment='reply_effects' AND p.outcome IN ('answered','ignored') "
            "ORDER BY p.id DESC LIMIT 2",
            (scene,),
        ).fetchall()
        if len(rows) < 2 or any(row[1] != "ignored" for row in rows):
            return None
        return {"until": rows[0][2] + PAUSE_SECONDS, "wakes": [rows[1][0], rows[0][0]]}

    def _wake(self, row) -> dict:
        item = dict(row)
        spoke = item.pop("first_expression_at")
        delivery = item.pop("first_expression_delivery")
        item["spoke_at"] = spoke if delivery == "sent" else None
        return item

    def _select(self, where: str, parameters: tuple) -> list[dict]:
        rows = self.db.execute(
            "SELECT p.id,p.scene,p.turn_id,p.woke_at,p.local_date,p.idle_since,p.outcome,p.closed_at,p.assessment,"
            "(SELECT MAX(e.deadline) FROM reply_effects e WHERE e.scene=p.scene AND e.turn_id=p.turn_id) AS observe_until,"
            "t.status AS turn_status,t.ended AS turn_ended,t.first_expression_at,t.first_expression_delivery "
            "FROM proactive_wakes p JOIN turns t ON t.id=p.turn_id WHERE " + where, parameters,
        ).fetchall()
        return [self._wake(row) for row in rows]

    def open_wakes(self, scene: str) -> list[dict]:
        return self._select("p.scene=? AND p.outcome IS NULL AND p.assessment='reply_effects' ORDER BY p.id", (scene,))

    def page(self, scene: str, *, limit: int, offset: int) -> list[dict]:
        return self._select("p.scene=? ORDER BY p.id DESC LIMIT ? OFFSET ?", (scene, limit, offset))

    def close(self, id: int, outcome: str) -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"Unknown proactive outcome {outcome!r}")
        with self.db:
            updated = self.db.execute(
                "UPDATE proactive_wakes SET outcome=?,closed_at=? WHERE id=? AND outcome IS NULL",
                (outcome, self.store.now(), id),
            )
            if updated.rowcount != 1:
                raise ValueError(f"Proactive wake {id} is already closed")

    def settle(self, scene: str, now: float) -> float | None:
        """Use the existing reply judgments, never infer a response from a human arrival."""
        pending = None
        effects = ReplyEffectStore(self.store)
        for wake in self.open_wakes(scene):
            if wake["turn_ended"] is None:
                continue
            spoke = wake["spoke_at"]
            if spoke is None:
                self.close(wake["id"], "silent")
                continue
            samples = effects.for_turn(scene, wake["turn_id"])
            if not samples:
                self.close(wake["id"], "unobserved")
            elif any(item["reaction"] in {"agree", "continue", "correct", "negative"} for item in samples):
                self.close(wake["id"], "answered")
            elif any(item["state"] in {"observing", "waiting", "failed"} for item in samples):
                # Failed judgments stay retryable in M12; they are not "ignored".
                pending = now + 30.0
            elif any(item["input_gap"] or item["reaction"] == "uncertain" for item in samples):
                self.close(wake["id"], "unobserved")
            else:
                self.close(wake["id"], "ignored")
        return pending

    def next_at(self, scene: str, settings: Proactive, timezone: str, quiet: QuietHours | None,
                now: float, exclude_uids: tuple[str, ...]) -> tuple[float | None, str]:
        """Return the earliest allowed wake time at or after ``now`` and why it is that time."""
        idle_since = self.last_activity(scene, exclude_uids)
        if idle_since is None:
            return None, "本群还没有可计算安静时长的消息"
        at, reason = max(now, idle_since + settings.idle_seconds), "安静时长达到设定值"
        pause = self.pause(scene)
        if pause is not None and pause["until"] > at:
            at, reason = pause["until"], "连续两次主动开话题都没人回应，暂停一周"
        zone = ZoneInfo(timezone)
        for _ in range(_SEARCH_STEPS):
            if local_period(settings.start, settings.end, timezone, at) is None:
                at, reason = next_local_start(settings.start, settings.end, timezone, at), "等到活跃时段开始"
                continue
            if self.woke_on(scene, datetime.fromtimestamp(at, zone).date().isoformat()):
                at, reason = next_local_start(settings.start, settings.end, timezone, at), "本地今天已主动叫醒过"
                continue
            period = quiet_period(quiet, timezone, at)
            if period is not None:
                at, reason = period[1], "处在安静时段"
                continue
            return at, reason
        raise ValueError(f"No proactive wake time found for {scene} within {_SEARCH_STEPS} search steps")
