"""Actual expression candidates, successful input positions and model batches."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal, TYPE_CHECKING

from ..platform.messages import ChatMessage, plain_text
from ..storage.codec import encode, decode_message

if TYPE_CHECKING:
    from ..storage.store import Store


StoredVector = tuple[bytes, str, int]
EXPRESSION_COLUMNS = "id,scene,situation,style,sources,status,updated,vector IS NOT NULL AS indexed"


BATCH_SUMMARY_COLUMNS = (
    "id,scene,after_seq,through_seq,started,ended,status,model_started,usage,cost,error"
)


SCHEMA = """
CREATE TABLE learning_state (
    scene TEXT PRIMARY KEY, after_seq INTEGER NOT NULL
);
CREATE TABLE learning_batches (
    id INTEGER PRIMARY KEY, scene TEXT NOT NULL,
    after_seq INTEGER NOT NULL, through_seq INTEGER NOT NULL,
    started REAL NOT NULL, ended REAL, model_started REAL,
    status TEXT NOT NULL CHECK(status IN ('running','complete','failed','interrupted')),
    request TEXT NOT NULL, response TEXT, usage TEXT, cost TEXT, error TEXT
);
CREATE INDEX learning_batches_scene ON learning_batches(scene,id);
CREATE TABLE expressions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL, situation TEXT NOT NULL,
    style TEXT NOT NULL, sources TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','adopted','rejected')),
    updated REAL NOT NULL, vector BLOB, vector_binding TEXT,
    vector_dimensions INTEGER, UNIQUE(scene,situation,style)
);
CREATE INDEX expressions_scene_status ON expressions(scene,status,id);
CREATE TABLE expression_embedding_calls (
    id INTEGER PRIMARY KEY, scene TEXT NOT NULL, turn_id TEXT,
    purpose TEXT NOT NULL CHECK(purpose IN ('query','index','reindex')),
    started REAL NOT NULL, ended REAL, request TEXT NOT NULL,
    response TEXT, usage TEXT, cost TEXT, error TEXT
);
CREATE INDEX expression_embedding_scene ON expression_embedding_calls(scene,id);
CREATE INDEX learning_batches_usage ON learning_batches(model_started,scene);
CREATE INDEX expression_embedding_calls_usage ON expression_embedding_calls(started,scene);
"""


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
            message = decode_message(row["body"])
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
            if plain_text(decode_message(body)).strip():
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

    def complete(self, batch_id: int, candidates: list[tuple[str, str, list[int]]], *, auto_adopt: bool,
                 vectors: dict[tuple[str, str], StoredVector] | None = None) -> None:
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
                    vector = None if vectors is None else vectors.get((situation, style))
                    self.db.execute(
                        "INSERT INTO expressions(scene,situation,style,sources,status,updated,"
                        "vector,vector_binding,vector_dimensions) VALUES (?,?,?,?,?,?,?,?,?)",
                        (batch["scene"], situation, style, encode(sorted(sources)),
                         "adopted" if auto_adopt else "pending", self.store.now(),
                         *((None, None, None) if vector is None else vector)),
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
        result["indexed"] = bool(result["indexed"])
        result["sources"] = json.loads(result["sources"])
        result["count"] = len(result["sources"])
        return result

    def expressions(self, scene: str, *, status: str | None, limit: int, offset: int) -> dict:
        clause = "scene=?" if status is None else "scene=? AND status=?"
        values = (scene,) if status is None else (scene, status)
        rows = self.db.execute(f"SELECT {EXPRESSION_COLUMNS} FROM expressions WHERE " + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
                               (*values, limit, offset))
        total = self.db.execute("SELECT COUNT(*) FROM expressions WHERE " + clause, values).fetchone()[0]
        return {"items": [self._expression(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    def expression(self, scene: str, id: int) -> dict | None:
        return self._expression(self.db.execute(f"SELECT {EXPRESSION_COLUMNS} FROM expressions WHERE scene=? AND id=?",
                                                (scene, id)).fetchone())

    def update_expression(self, scene: str, id: int, *, situation: str, style: str, status: str,
                          vector: StoredVector | None = None) -> dict | None:
        with self.db:
            self.db.execute(
                "UPDATE expressions SET situation=?,style=?,status=?,updated=?,vector=?,"
                "vector_binding=?,vector_dimensions=? WHERE scene=? AND id=?",
                (situation, style, status, self.store.now(),
                 *((None, None, None) if vector is None else vector), scene, id),
            )
        return self.expression(scene, id)

    def delete_expression(self, scene: str, id: int) -> bool:
        with self.db:
            result = self.db.execute("DELETE FROM expressions WHERE scene=? AND id=?", (scene, id))
        return result.rowcount == 1

    def find_expression(self, scene: str, situation: str, style: str) -> dict | None:
        return self._expression(self.db.execute(
            f"SELECT {EXPRESSION_COLUMNS} FROM expressions WHERE scene=? AND situation=? AND style=?",
            (scene, situation, style),
        ).fetchone())

    def adopted(self, scene: str, *, exclude_uids: Sequence[str] = ()) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT id,situation,style,vector,vector_binding,vector_dimensions FROM expressions "
            "WHERE scene=? AND status='adopted' AND NOT EXISTS ("
            "SELECT 1 FROM json_each(expressions.sources) source JOIN messages m ON m.seq=source.value "
            "WHERE json_extract(m.body,'$.sender.uid') IN (SELECT value FROM json_each(?))) ORDER BY id",
            (scene, encode(exclude_uids)),
        )]

    def vector(self, scene: str, id: int) -> StoredVector | None:
        row = self.db.execute("SELECT vector,vector_binding,vector_dimensions FROM expressions WHERE scene=? AND id=?",
                              (scene, id)).fetchone()
        return None if row is None or row[0] is None else (row[0], row[1], row[2])

    def replace_vectors(self, scene: str, snapshot: list[tuple[int, str]],
                        vectors: dict[int, StoredVector]) -> None:
        with self.db:
            current = [tuple(row) for row in self.db.execute(
                "SELECT id,situation FROM expressions WHERE scene=? AND status='adopted' ORDER BY id", (scene,),
            )]
            if current != snapshot:
                raise ValueError(f"Adopted expressions changed during reindex in {scene}; old vectors unchanged")
            self.db.executemany("UPDATE expressions SET vector=?,vector_binding=?,vector_dimensions=? WHERE scene=? AND id=?",
                                [(*vectors[id], scene, id) for id, _ in snapshot])

    def recent_expression_ids(self, scene: str, limit: int = 3) -> set[int]:
        """IDs provided as reference; this does not record use or drive selection."""
        rows = self.db.execute(
            "SELECT json_extract(model_calls.request,'$.expression_ids') FROM model_calls "
            "JOIN turns ON turns.id=model_calls.turn_id "
            "WHERE turns.scene=? AND model_calls.role IN ('mind','voice') AND model_calls.ended IS NOT NULL "
            "AND model_calls.error IS NULL ORDER BY model_calls.id DESC LIMIT ?", (scene, limit),
        )
        return {id for row in rows if row[0] is not None for id in json.loads(row[0])}

    def start_embedding_call(self, scene: str, purpose: Literal["query", "index", "reindex"],
                             request: dict, *, turn_id: str | None = None) -> int:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO expression_embedding_calls(scene,turn_id,purpose,started,request) VALUES (?,?,?,?,?)",
                (scene, turn_id, purpose, self.store.now(), encode(request)),
            )
        return cursor.lastrowid

    def end_embedding_call(self, id: int, response: dict | None, usage: dict | None,
                           cost: dict | None, error: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE expression_embedding_calls SET ended=?,response=?,usage=?,cost=?,error=? WHERE id=?",
                (self.store.now(), None if response is None else encode(response),
                 None if usage is None else encode(usage), None if cost is None else encode(cost), error, id),
            )

    def embedding_calls(self, scene: str, *, limit: int, offset: int) -> dict:
        rows = self.db.execute(
            "SELECT id,scene,turn_id,purpose,started,ended,response,usage,cost,error "
            "FROM expression_embedding_calls WHERE scene=? ORDER BY id DESC LIMIT ? OFFSET ?", (scene, limit, offset),
        )
        total = self.db.execute("SELECT COUNT(*) FROM expression_embedding_calls WHERE scene=?", (scene,)).fetchone()[0]
        return {"items": [self._batch(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    def embedding_call(self, scene: str, id: int) -> dict | None:
        return self._batch(self.db.execute("SELECT * FROM expression_embedding_calls WHERE scene=? AND id=?",
                                           (scene, id)).fetchone())
