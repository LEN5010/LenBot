"""Recorded confirmed expressions, their frozen follow-up windows and actual judge calls."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal, TYPE_CHECKING

from ..platform.messages import ChatMessage
from ..storage.codec import encode, decode_message

if TYPE_CHECKING:
    from ..storage.store import Store


OBSERVE_SECONDS = 180.0
OBSERVE_MESSAGES = 5
REACTIONS = ("agree", "continue", "correct", "negative", "unrelated", "uncertain")
# Derived states shown next to model reactions; they are never model output.
STATES = ("observing", "no_messages", "waiting", "failed")
CHANNELS = ("direct", "named", "focus", "ambient", "schedule", "task", "resume", "in_turn", "quiet_notice",
            "proactive", "plugin", "audio")
CALL_SUMMARY_COLUMNS = "id,scene,effect_ids,started,ended,status,model_started,usage,cost,error"
STATE_SQL = (
    "CASE WHEN e.reaction IS NOT NULL THEN e.reaction "
    "WHEN e.closed_at IS NULL THEN 'observing' "
    "WHEN e.observed_seqs='[]' THEN 'no_messages' "
    "WHEN c.status IN ('failed','interrupted') THEN 'failed' ELSE 'waiting' END"
)


SCHEMA = """
CREATE TABLE reply_effects (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,
    entry_seq INTEGER NOT NULL UNIQUE, turn_id TEXT, channels TEXT NOT NULL,
    message_seqs TEXT NOT NULL, planned_parts INTEGER NOT NULL,
    first_sent_at REAL NOT NULL, last_sent_at REAL NOT NULL, deadline REAL NOT NULL,
    observed_seqs TEXT, closed_at REAL, input_gap INTEGER, call_id INTEGER,
    reaction TEXT CHECK(reaction IS NULL OR reaction IN
        ('agree','continue','correct','negative','unrelated','uncertain')),
    reason TEXT
);
CREATE INDEX reply_effects_scene ON reply_effects(scene,id);
CREATE INDEX reply_effects_turn ON reply_effects(scene,turn_id);
CREATE INDEX reply_effects_open ON reply_effects(scene,id) WHERE closed_at IS NULL;
CREATE TABLE reply_effect_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,
    effect_ids TEXT NOT NULL, started REAL NOT NULL, ended REAL,
    status TEXT NOT NULL CHECK(status IN ('running','complete','failed','interrupted')),
    model_started REAL, request TEXT NOT NULL,
    response TEXT, usage TEXT, cost TEXT, error TEXT
);
CREATE INDEX reply_effect_calls_scene ON reply_effect_calls(scene,id);
CREATE INDEX reply_effect_calls_usage ON reply_effect_calls(model_started,scene);
"""


class ReplyEffectStore:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db

    def record(self, scene: str, entry_seq: int, *, turn_id: str | None, channels: Sequence[str],
               sent: Sequence[tuple[int, float]], planned_parts: int) -> int:
        """Save one expression's confirmed parts; unconfirmed parts never become a sample."""
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO reply_effects(scene,entry_seq,turn_id,channels,message_seqs,planned_parts,"
                "first_sent_at,last_sent_at,deadline) VALUES (?,?,?,?,?,?,?,?,?)",
                (scene, entry_seq, turn_id, encode(sorted(set(channels))), encode([seq for seq, _ in sent]),
                 planned_parts, sent[0][1], sent[-1][1], sent[-1][1] + OBSERVE_SECONDS),
            )
        return cursor.lastrowid

    def recover(self, scene: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE reply_effect_calls SET status='interrupted',ended=?,error=? "
                "WHERE scene=? AND status='running'",
                (self.store.now(), "Previous reply effect judgment was interrupted; not replayed", scene),
            )

    def open_effects(self, scene: str) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT id,first_sent_at,deadline FROM reply_effects WHERE scene=? AND closed_at IS NULL ORDER BY id",
            (scene,),
        )]

    def followups(self, scene: str, first_sent_at: float, deadline: float, *,
                  exclude_uids: Sequence[str], limit: int = OBSERVE_MESSAGES) -> list[int]:
        """Human arrivals in the window by actual host arrival time, images included."""
        return [row[0] for row in self.db.execute(
            "SELECT seq FROM messages WHERE scene=? AND raw IS NOT NULL AND received_at>? AND received_at<=? "
            "AND json_extract(body,'$.is_self')=0 AND json_extract(body,'$.send_status')='received' "
            "AND json_extract(body,'$.sender.uid') NOT IN (SELECT value FROM json_each(?)) "
            "ORDER BY received_at,seq LIMIT ?",
            (scene, first_sent_at, deadline, encode(list(exclude_uids)), limit),
        )]

    def close(self, id: int, observed: list[int], *, input_gap: bool) -> None:
        with self.db:
            changed = self.db.execute(
                "UPDATE reply_effects SET observed_seqs=?,closed_at=?,input_gap=? WHERE id=? AND closed_at IS NULL",
                (encode(observed), self.store.now(), int(input_gap), id),
            )
            if changed.rowcount != 1:
                raise ValueError(f"Reply effect {id} was already closed")

    def waiting(self, scene: str) -> list[dict]:
        """Closed samples with follow-ups that no call has taken yet, oldest first."""
        return [dict(row) for row in self.db.execute(
            "SELECT id,entry_seq,closed_at FROM reply_effects WHERE scene=? AND closed_at IS NOT NULL "
            "AND observed_seqs!='[]' AND reaction IS NULL AND call_id IS NULL ORDER BY id", (scene,),
        )]

    def effects(self, scene: str, ids: Sequence[int]) -> list[dict]:
        rows = [self._effect(row) for row in self.db.execute(
            "SELECT * FROM reply_effects WHERE scene=? AND id IN (SELECT value FROM json_each(?)) ORDER BY id",
            (scene, encode(list(ids))),
        )]
        if [row["id"] for row in rows] != sorted(ids):
            raise ValueError(f"Reply effect samples are missing in {scene}: {sorted(ids)}")
        return rows

    def begin_call(self, scene: str, ids: Sequence[int], request: dict, *, retry_of: int | None = None) -> int:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO reply_effect_calls(scene,effect_ids,started,status,request) VALUES (?,?,?,'running',?)",
                (scene, encode(list(ids)), self.store.now(), encode(request)),
            )
            changed = self.db.execute(
                "UPDATE reply_effects SET call_id=? WHERE scene=? AND reaction IS NULL AND "
                + ("call_id IS NULL" if retry_of is None else "call_id=?")
                + " AND id IN (SELECT value FROM json_each(?))",
                (cursor.lastrowid, scene, *(() if retry_of is None else (retry_of,)), encode(list(ids))),
            )
            if changed.rowcount != len(ids):
                raise ValueError(f"Reply effect samples changed before judgment in {scene}: {list(ids)}")
        return cursor.lastrowid

    def mark_model_started(self, call_id: int) -> None:
        with self.db:
            self.db.execute("UPDATE reply_effect_calls SET model_started=? WHERE id=?", (self.store.now(), call_id))

    def response(self, call_id: int, response: object, usage: dict | None, cost: dict | None) -> None:
        with self.db:
            self.db.execute("UPDATE reply_effect_calls SET response=?,usage=?,cost=? WHERE id=?",
                            (encode(response), None if usage is None else encode(usage),
                             None if cost is None else encode(cost), call_id))

    def complete(self, call_id: int, results: dict[int, tuple[str, str]]) -> None:
        """Store every entry's result together with the call's completion."""
        with self.db:
            for entry_seq, (reaction, reason) in results.items():
                changed = self.db.execute(
                    "UPDATE reply_effects SET reaction=?,reason=? WHERE call_id=? AND entry_seq=? AND reaction IS NULL",
                    (reaction, reason, call_id, entry_seq),
                )
                if changed.rowcount != 1:
                    raise ValueError(f"Reply effect for entry {entry_seq} is not waiting on call {call_id}")
            self.db.execute("UPDATE reply_effect_calls SET status='complete',ended=? WHERE id=?",
                            (self.store.now(), call_id))

    def fail(self, call_id: int, status: Literal["failed", "interrupted"], error: str) -> None:
        with self.db:
            self.db.execute("UPDATE reply_effect_calls SET status=?,ended=?,error=? WHERE id=?",
                            (status, self.store.now(), error, call_id))

    def retryable(self, scene: str, call_id: int) -> list[int]:
        call = self.call(scene, call_id, summary=True)
        if call is None:
            raise ValueError(f"当前场景没有回复效果判断 {call_id}")
        if call["status"] not in {"failed", "interrupted"}:
            raise ValueError(f"回复效果判断 {call_id} 状态为 {call['status']}，只有失败或中断的批次可以重做")
        ids = [row[0] for row in self.db.execute(
            "SELECT id FROM reply_effects WHERE scene=? AND call_id=? AND reaction IS NULL ORDER BY id",
            (scene, call_id),
        )]
        if not ids:
            raise ValueError(f"回复效果判断 {call_id} 没有仍待判断的样本")
        return ids

    @staticmethod
    def _json(row, fields: Sequence[str]) -> dict:
        result = dict(row)
        for field in fields:
            if result.get(field) is not None:
                result[field] = json.loads(result[field])
        return result

    def _effect(self, row) -> dict:
        result = self._json(row, ("channels", "message_seqs", "observed_seqs"))
        result["partial"] = len(result["message_seqs"]) < result["planned_parts"]
        if "input_gap" in result and result["input_gap"] is not None:
            result["input_gap"] = bool(result["input_gap"])
        return result

    def page(self, scene: str, *, state: str | None, channel: str | None, since: float | None,
             limit: int, offset: int) -> dict:
        conditions, values = ["e.scene=?"], [scene]
        if state == "attention":
            conditions.append("e.reaction IN ('correct','negative')")
        elif state is not None:
            conditions.append(STATE_SQL + "=?")
            values.append(state)
        if channel is not None:
            conditions.append("EXISTS (SELECT 1 FROM json_each(e.channels) WHERE value=?)")
            values.append(channel)
        if since is not None:
            conditions.append("e.first_sent_at>=?")
            values.append(since)
        where = " AND ".join(conditions)
        rows = self.db.execute(
            f"SELECT e.*,{STATE_SQL} AS state FROM reply_effects e LEFT JOIN reply_effect_calls c ON c.id=e.call_id "
            f"WHERE {where} ORDER BY e.id DESC LIMIT ? OFFSET ?", (*values, limit, offset),
        )
        total = self.db.execute(
            f"SELECT COUNT(*) FROM reply_effects e LEFT JOIN reply_effect_calls c ON c.id=e.call_id WHERE {where}",
            values,
        ).fetchone()[0]
        return {"items": [self._effect(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    def distribution(self, scene: str, since: float) -> dict:
        states = dict(self.db.execute(
            f"SELECT {STATE_SQL},COUNT(*) FROM reply_effects e LEFT JOIN reply_effect_calls c ON c.id=e.call_id "
            "WHERE e.scene=? AND e.first_sent_at>=? GROUP BY 1", (scene, since),
        ))
        facts = self.db.execute(
            "SELECT COUNT(*),SUM(json_array_length(message_seqs)<planned_parts),SUM(input_gap=1) "
            "FROM reply_effects WHERE scene=? AND first_sent_at>=?", (scene, since),
        ).fetchone()
        channels = dict(self.db.execute(
            "SELECT channels,COUNT(*) FROM reply_effects WHERE scene=? AND first_sent_at>=? GROUP BY channels",
            (scene, since),
        ))
        return {"since": since, "samples": facts[0], "partial": facts[1] or 0, "input_gap": facts[2] or 0,
                "states": {name: states.get(name, 0) for name in (*REACTIONS, *STATES)},
                "channel_sets": [{"channels": json.loads(key), "count": count}
                                 for key, count in sorted(channels.items(), key=lambda item: -item[1])]}

    def effect(self, scene: str, id: int) -> dict | None:
        row = self.db.execute(
            f"SELECT e.*,{STATE_SQL} AS state FROM reply_effects e LEFT JOIN reply_effect_calls c ON c.id=e.call_id "
            "WHERE e.scene=? AND e.id=?", (scene, id),
        ).fetchone()
        return None if row is None else self._effect(row)

    def before(self, scene: str, seq: int, limit: int = 6) -> list[tuple[int, ChatMessage]]:
        rows = self.db.execute(
            "SELECT seq,body FROM messages WHERE scene=? AND seq<? "
            "AND json_extract(body,'$.send_status') IN ('received','sent') ORDER BY seq DESC LIMIT ?",
            (scene, seq, limit),
        ).fetchall()
        return [(row[0], decode_message(row[1])) for row in reversed(rows)]

    def arrivals(self, scene: str, seqs: Sequence[int]) -> dict[int, float | None]:
        return {row[0]: row[1] for row in self.db.execute(
            "SELECT seq,received_at FROM messages WHERE scene=? AND seq IN (SELECT value FROM json_each(?))",
            (scene, encode(list(seqs))),
        )}

    def calls(self, scene: str, *, limit: int, offset: int) -> dict:
        rows = self.db.execute(
            f"SELECT {CALL_SUMMARY_COLUMNS} FROM reply_effect_calls WHERE scene=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (scene, limit, offset),
        )
        total = self.db.execute("SELECT COUNT(*) FROM reply_effect_calls WHERE scene=?", (scene,)).fetchone()[0]
        return {"items": [self._json(row, ("effect_ids", "usage", "cost")) for row in rows],
                "total": total, "limit": limit, "offset": offset}

    def call(self, scene: str, id: int, *, summary: bool = False) -> dict | None:
        columns = CALL_SUMMARY_COLUMNS if summary else "*"
        row = self.db.execute(f"SELECT {columns} FROM reply_effect_calls WHERE scene=? AND id=?",
                              (scene, id)).fetchone()
        return None if row is None else self._json(row, ("effect_ids", "request", "response", "usage", "cost"))

    def latest_call(self, scene: str) -> dict | None:
        row = self.db.execute(
            f"SELECT {CALL_SUMMARY_COLUMNS} FROM reply_effect_calls WHERE scene=? ORDER BY id DESC LIMIT 1", (scene,),
        ).fetchone()
        return None if row is None else self._json(row, ("effect_ids", "usage", "cost"))

    def for_turn(self, scene: str, turn_id: str) -> list[dict]:
        return [self._effect(row) for row in self.db.execute(
            f"SELECT e.*,{STATE_SQL} AS state FROM reply_effects e LEFT JOIN reply_effect_calls c ON c.id=e.call_id "
            "WHERE e.scene=? AND e.turn_id=? ORDER BY e.id", (scene, turn_id),
        )]
