"""Actual expression candidates, successful input positions and model batches."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

from .messages import ChatMessage, plain_text
from .store import Store, encode


BATCH_SUMMARY_COLUMNS = (
    "id,scene,after_seq,through_seq,started,ended,status,model_started,usage,cost,error"
)


class LearningStore:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db

    def initialize(self, scene: str, after_seq: int) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO learning_state(scene,after_seq) VALUES (?,?)",
                            (scene, after_seq))

    def recover(self, scene: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE learning_batches SET status='interrupted',ended=?,error=? "
                "WHERE scene=? AND status='running'",
                (self.store.now(), "Previous expression learning batch was interrupted; not replayed", scene),
            )

    def state(self, scene: str) -> dict | None:
        row = self.db.execute("SELECT * FROM learning_state WHERE scene=?", (scene,)).fetchone()
        return None if row is None else dict(row)

    def scan(self, scene: str, after_seq: int, *, limit: int,
             exclude_uids: Sequence[str], through: int | None = None
             ) -> tuple[int, list[tuple[int, ChatMessage, float]]]:
        clause = "" if through is None else " AND seq<=?"
        values = (scene, after_seq) if through is None else (scene, after_seq, through)
        rows = self.db.execute(
            "SELECT seq,body,raw IS NOT NULL AS is_received,received_at FROM messages WHERE scene=? AND seq>?" + clause
            + " ORDER BY seq LIMIT ?", (*values, limit),
        ).fetchall()
        selected = []
        for row in rows:
            if not row["is_received"] or row["received_at"] is None:
                continue
            message = Store._message(row["body"])
            if (message.send_status == "received" and not message.is_self
                    and message.sender.uid not in exclude_uids and plain_text(message).strip()):
                selected.append((row["seq"], message, row["received_at"]))
        return (rows[-1]["seq"] if rows else after_seq), selected

    def last_input_at(self, scene: str, after_seq: int, *, exclude_uids: Sequence[str]) -> float | None:
        rows = self.db.execute(
            "SELECT body,received_at FROM messages WHERE scene=? AND seq>? AND raw IS NOT NULL "
            "AND received_at IS NOT NULL AND json_extract(body,'$.send_status')='received' "
            "AND json_extract(body,'$.is_self')=0 "
            "AND json_extract(body,'$.sender.uid') NOT IN (SELECT value FROM json_each(?)) "
            "ORDER BY seq DESC", (scene, after_seq, encode(exclude_uids)),
        )
        for body, received_at in rows:
            if plain_text(Store._message(body)).strip():
                return received_at
        return None

    def _advance(self, scene: str, after_seq: int, through_seq: int) -> None:
        changed = self.db.execute(
            "UPDATE learning_state SET after_seq=? WHERE scene=? AND after_seq=?",
            (through_seq, scene, after_seq),
        )
        if changed.rowcount != 1:
            raise ValueError(f"Expression learning input position changed for {scene}")

    def advance_empty(self, scene: str, after_seq: int, through_seq: int) -> None:
        with self.db:
            self._advance(scene, after_seq, through_seq)

    def begin(self, scene: str, after_seq: int, through_seq: int, request: dict) -> int:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO learning_batches(scene,after_seq,through_seq,started,status,request) "
                "VALUES (?,?,?,?,'running',?)", (scene, after_seq, through_seq, self.store.now(), encode(request)),
            )
        return cursor.lastrowid

    def mark_model_started(self, batch_id: int) -> None:
        with self.db:
            self.db.execute("UPDATE learning_batches SET model_started=? WHERE id=?",
                            (self.store.now(), batch_id))

    def response(self, batch_id: int, response: object, usage: dict | None, cost: dict | None) -> None:
        with self.db:
            self.db.execute("UPDATE learning_batches SET response=?,usage=?,cost=? WHERE id=?",
                            (encode(response), None if usage is None else encode(usage),
                             None if cost is None else encode(cost), batch_id))

    def complete(self, batch_id: int, candidates: list[tuple[str, str, list[int]]], *, auto_adopt: bool) -> None:
        with self.db:
            batch = self.db.execute(
                "SELECT scene,after_seq,through_seq FROM learning_batches WHERE id=?", (batch_id,),
            ).fetchone()
            for situation, style, sources in candidates:
                old = self.db.execute(
                    "SELECT id,sources FROM expressions WHERE scene=? AND situation=? AND style=?",
                    (batch["scene"], situation, style),
                ).fetchone()
                if old is None:
                    self.db.execute(
                        "INSERT INTO expressions(scene,situation,style,sources,status,updated) VALUES (?,?,?,?,?,?)",
                        (batch["scene"], situation, style, encode(sorted(sources)),
                         "adopted" if auto_adopt else "pending", self.store.now()),
                    )
                else:
                    merged = sorted(set(json.loads(old["sources"])) | set(sources))
                    self.db.execute("UPDATE expressions SET sources=?,updated=? WHERE id=?",
                                    (encode(merged), self.store.now(), old["id"]))
            self._advance(batch["scene"], batch["after_seq"], batch["through_seq"])
            self.db.execute("UPDATE learning_batches SET status='complete',ended=? WHERE id=?",
                            (self.store.now(), batch_id))

    def fail(self, batch_id: int, status: Literal["failed", "interrupted"], error: str) -> None:
        with self.db:
            self.db.execute("UPDATE learning_batches SET status=?,ended=?,error=? WHERE id=?",
                            (status, self.store.now(), error, batch_id))

    @staticmethod
    def _batch(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        for field in ("request", "response", "usage", "cost"):
            if field in result and result[field] is not None:
                result[field] = json.loads(result[field])
        return result

    def latest(self, scene: str) -> dict | None:
        return self._batch(self.db.execute(
            f"SELECT {BATCH_SUMMARY_COLUMNS} FROM learning_batches WHERE scene=? ORDER BY id DESC LIMIT 1", (scene,),
        ).fetchone())

    def batch(self, scene: str, id: int) -> dict | None:
        return self._batch(self.db.execute("SELECT * FROM learning_batches WHERE scene=? AND id=?",
                                           (scene, id)).fetchone())

    def batches(self, scene: str, *, limit: int, offset: int) -> dict:
        rows = self.db.execute(
            f"SELECT {BATCH_SUMMARY_COLUMNS} FROM learning_batches "
            "WHERE scene=? ORDER BY id DESC LIMIT ? OFFSET ?", (scene, limit, offset),
        )
        total = self.db.execute("SELECT COUNT(*) FROM learning_batches WHERE scene=?", (scene,)).fetchone()[0]
        return {"items": [self._batch(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    def counts(self, scene: str) -> dict[str, int]:
        return {"pending": 0, "adopted": 0, "rejected": 0, **dict(self.db.execute(
            "SELECT status,COUNT(*) FROM expressions WHERE scene=? GROUP BY status", (scene,),
        ))}

    @staticmethod
    def _expression(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result["sources"] = json.loads(result["sources"])
        result["count"] = len(result["sources"])
        return result

    def expressions(self, scene: str, *, status: str | None, limit: int, offset: int) -> dict:
        clause = "scene=?" if status is None else "scene=? AND status=?"
        values = (scene,) if status is None else (scene, status)
        rows = self.db.execute("SELECT * FROM expressions WHERE " + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
                               (*values, limit, offset))
        total = self.db.execute("SELECT COUNT(*) FROM expressions WHERE " + clause, values).fetchone()[0]
        return {"items": [self._expression(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    def expression(self, scene: str, id: int) -> dict | None:
        return self._expression(self.db.execute("SELECT * FROM expressions WHERE scene=? AND id=?",
                                                (scene, id)).fetchone())

    def update_expression(self, scene: str, id: int, *, situation: str, style: str, status: str) -> dict | None:
        with self.db:
            self.db.execute("UPDATE expressions SET situation=?,style=?,status=?,updated=? WHERE scene=? AND id=?",
                            (situation, style, status, self.store.now(), scene, id))
        return self.expression(scene, id)

    def delete_expression(self, scene: str, id: int) -> bool:
        with self.db:
            result = self.db.execute("DELETE FROM expressions WHERE scene=? AND id=?", (scene, id))
        return result.rowcount == 1
