"""Per-scene jargon counts, decisions, and actual learner calls."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterator, Sequence
from typing import Literal, TYPE_CHECKING

from .store import LearningStore
from ..platform.messages import ChatMessage, plain_text
from ..storage.codec import encode, decode_message

if TYPE_CHECKING:
    from ..storage.store import Store


THRESHOLDS = (4, 8, 25, 100)
SUMMARY_COLUMNS = (
    "id,scene,purpose,after_seq,through_seq,term_id,inference_count,started,ended,"
    "status,model_started,usage,tokens,error"
)


SCHEMA = """
CREATE TABLE jargon_state (
    scene TEXT PRIMARY KEY, start_seq INTEGER NOT NULL, after_seq INTEGER NOT NULL
);
CREATE TABLE jargon (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL, term TEXT NOT NULL,
    count INTEGER NOT NULL, sample_seqs TEXT NOT NULL,
    latest_meaning TEXT, confidence REAL, meaning TEXT,
    last_inference_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('pending','adopted','rejected')),
    updated REAL NOT NULL, UNIQUE(scene,term)
);
CREATE INDEX jargon_scene_status ON jargon(scene,status,id);
CREATE TABLE jargon_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK(purpose IN ('discovery','meaning')),
    after_seq INTEGER, through_seq INTEGER, term_id INTEGER, inference_count INTEGER,
    started REAL NOT NULL, ended REAL,
    status TEXT NOT NULL CHECK(status IN ('running','complete','failed','interrupted')),
    model_started REAL, request TEXT NOT NULL,
    response TEXT, usage TEXT, tokens TEXT, error TEXT
);
CREATE INDEX jargon_calls_scene ON jargon_calls(scene,id);
CREATE INDEX jargon_calls_usage ON jargon_calls(model_started,scene);
"""


class JargonStore:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db
        self.scanner = LearningStore(store)

    def initialize(self, scene: str, after_seq: int) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO jargon_state(scene,start_seq,after_seq) VALUES (?,?,?)",
                (scene, after_seq, after_seq),
            )

    def recover(self, scene: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE jargon_calls SET status='interrupted',ended=?,error=? "
                "WHERE scene=? AND status='running'",
                (self.store.now(), "Previous jargon call was interrupted; not replayed", scene),
            )

    def state(self, scene: str) -> dict | None:
        row = self.db.execute("SELECT * FROM jargon_state WHERE scene=?", (scene,)).fetchone()
        return None if row is None else dict(row)

    @staticmethod
    def _call(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        for field in ("request", "response", "usage", "tokens"):
            if field in result and result[field] is not None:
                result[field] = json.loads(result[field])
        return result

    def latest(self, scene: str) -> dict | None:
        return self._call(self.db.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM jargon_calls WHERE scene=? ORDER BY id DESC LIMIT 1", (scene,),
        ).fetchone())

    def calls(self, scene: str, limit: int, offset: int) -> dict:
        rows = self.db.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM jargon_calls WHERE scene=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (scene, limit, offset),
        )
        total = self.db.execute("SELECT COUNT(*) FROM jargon_calls WHERE scene=?", (scene,)).fetchone()[0]
        return {"items": [self._call(row) for row in rows], "total": total,
                "limit": limit, "offset": offset}

    def call(self, scene: str, id: int) -> dict | None:
        return self._call(self.db.execute(
            "SELECT * FROM jargon_calls WHERE scene=? AND id=?", (scene, id),
        ).fetchone())

    def begin_discovery(self, scene: str, after: int, through: int, request: dict) -> int:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO jargon_calls(scene,purpose,after_seq,through_seq,started,status,request) "
                "VALUES (?,'discovery',?,?,?,'running',?)",
                (scene, after, through, self.store.now(), encode(request)),
            )
        return cursor.lastrowid

    def begin_meaning(self, scene: str, term_id: int, count: int, request: dict) -> int:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO jargon_calls(scene,purpose,term_id,inference_count,started,status,request) "
                "SELECT scene,'meaning',id,?,?,'running',? FROM jargon WHERE scene=? AND id=?",
                (count, self.store.now(), encode(request), scene, term_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Jargon term {term_id} is not in {scene}")
        return cursor.lastrowid

    def mark_model_started(self, id: int) -> None:
        with self.db:
            self.db.execute("UPDATE jargon_calls SET model_started=? WHERE id=?",
                            (self.store.now(), id))

    def response(self, id: int, response: object | None,
                 usage: dict | None, tokens: dict | None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE jargon_calls SET response=?,usage=?,tokens=? WHERE id=?",
                (None if response is None else encode(response),
                 None if usage is None else encode(usage),
                 None if tokens is None else encode(tokens), id),
            )

    def fail(self, id: int, status: Literal["failed", "interrupted"], error: str) -> None:
        with self.db:
            self.db.execute("UPDATE jargon_calls SET status=?,ended=?,error=? WHERE id=?",
                            (status, self.store.now(), error, id))

    def _scan(self, scene: str, after: int, through: int,
              exclude_uids: Sequence[str]) -> Iterator[tuple[int, str]]:
        cursor = after
        while cursor < through:
            scanned, rows = self.scanner.scan(
                scene, cursor, limit=100, through=through, exclude_uids=exclude_uids,
            )
            if scanned == cursor:
                break
            yield from ((seq, plain_text(message)) for seq, message, _ in rows)
            cursor = scanned

    def complete_discovery(self, id: int, proposals: list[tuple[str, int]],
                           exclude_uids: Sequence[str]) -> None:
        """Scan outside the writer transaction, then publish counts and the cursor together."""
        call = self.db.execute(
            "SELECT scene,after_seq,through_seq FROM jargon_calls "
            "WHERE id=? AND purpose='discovery' AND status='running'", (id,),
        ).fetchone()
        if call is None:
            raise ValueError(f"No running jargon discovery call {id}")
        scene, after, through = call
        state = self.db.execute('SELECT start_seq,after_seq FROM jargon_state WHERE scene=?', (scene,)).fetchone()
        if state is None or state['after_seq'] != after:
            raise ValueError(f"Jargon discovery position changed for {scene}")
        known = {row['term']: row for row in self.db.execute(
            'SELECT id,term,count,sample_seqs FROM jargon WHERE scene=?', (scene,))}
        recent = list(self._scan(scene, after, through, exclude_uids))
        hits = {term: [seq for seq, text in recent if term in text] for term in known}
        new = {term: {'count': 0, 'samples': deque(maxlen=20)} for term, _ in proposals if term not in known}
        for seq, text in self._scan(scene, state['start_seq'], through, exclude_uids) if new else ():
            for term, values in new.items():
                if term in text:
                    values['count'] += 1
                    values['samples'].append(seq)
        now = self.store.now()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current = self.db.execute('SELECT start_seq,after_seq FROM jargon_state WHERE scene=?', (scene,)).fetchone()
            if current is None or tuple(current) != tuple(state):
                raise ValueError(f"Jargon discovery position changed for {scene}")
            for term, row in known.items():
                if hits[term]:
                    samples = (json.loads(row['sample_seqs']) + hits[term])[-20:]
                    self.db.execute('UPDATE jargon SET count=?,sample_seqs=?,updated=? WHERE id=?',
                                    (row['count'] + len(hits[term]), encode(samples), now, row['id']))
            for term, values in new.items():
                self.db.execute("INSERT INTO jargon(scene,term,count,sample_seqs,status,updated) "
                                "VALUES (?,?,?,?,'pending',?)", (scene, term, values['count'], encode(list(values['samples'])), now))
            changed = self.db.execute('UPDATE jargon_state SET after_seq=? WHERE scene=? AND after_seq=?',
                                      (through, scene, after))
            if changed.rowcount != 1:
                raise ValueError(f"Jargon discovery position changed for {scene}")
            completed = self.db.execute("UPDATE jargon_calls SET status='complete',ended=? WHERE id=? AND status='running'",
                                       (now, id))
            if completed.rowcount != 1:
                raise ValueError(f"No running jargon discovery call {id}")

    def complete_meaning(self, id: int, meaning: str, confidence: float) -> None:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            call = self.db.execute(
                "SELECT scene,term_id,inference_count FROM jargon_calls "
                "WHERE id=? AND purpose='meaning' AND status='running'", (id,),
            ).fetchone()
            if call is None:
                raise ValueError(f"No running jargon meaning call {id}")
            now = self.store.now()
            # Deletion or rejection while the model ran never recreates or changes the decision.
            self.db.execute(
                "UPDATE jargon SET latest_meaning=?,confidence=?,last_inference_count=?,updated=? "
                "WHERE scene=? AND id=?",
                (meaning, confidence, call["inference_count"], now,
                 call["scene"], call["term_id"]),
            )
            self.db.execute("UPDATE jargon_calls SET status='complete',ended=? WHERE id=?",
                            (now, id))

    def pending(self, scene: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM jargon WHERE scene=? AND status!='rejected' ORDER BY id", (scene,),
        )
        return [item for row in rows if (item := self._item(row)) is not None and
                any(item["last_inference_count"] < threshold <= item["count"]
                    for threshold in THRESHOLDS)]

    def advance_empty(self, scene: str, after: int, through: int) -> None:
        with self.db:
            changed = self.db.execute(
                "UPDATE jargon_state SET after_seq=? WHERE scene=? AND after_seq=?",
                (through, scene, after),
            )
            if changed.rowcount != 1:
                raise ValueError(f"Jargon discovery position changed for {scene}")

    @staticmethod
    def _item(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result["sample_seqs"] = json.loads(result["sample_seqs"])
        return result

    def items(self, scene: str, status: str | None, limit: int, offset: int) -> dict:
        clause = "scene=?" if status is None else "scene=? AND status=?"
        values = (scene,) if clause == "scene=?" else (scene, status)
        rows = self.db.execute(
            "SELECT * FROM jargon WHERE " + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
            (*values, limit, offset),
        )
        total = self.db.execute("SELECT COUNT(*) FROM jargon WHERE " + clause, values).fetchone()[0]
        return {"items": [self._item(row) for row in rows], "total": total,
                "limit": limit, "offset": offset}

    def item(self, scene: str, id: int) -> dict | None:
        return self._item(self.db.execute(
            "SELECT * FROM jargon WHERE scene=? AND id=?", (scene, id),
        ).fetchone())

    def update(self, scene: str, id: int, meaning: str | None, status: str) -> dict | None:
        if status == "pending":
            meaning = None
        if status == "adopted" and (meaning is None or not meaning.strip()):
            raise ValueError("Adopted jargon requires a nonblank effective meaning")
        if meaning is not None and not meaning.strip():
            raise ValueError("Jargon effective meaning must be nonblank or null")
        with self.db:
            changed = self.db.execute(
                "UPDATE jargon SET meaning=?,status=?,updated=? WHERE scene=? AND id=?",
                (meaning, status, self.store.now(), scene, id),
            )
        return None if changed.rowcount == 0 else self.item(scene, id)

    def delete(self, scene: str, id: int) -> bool:
        with self.db:
            changed = self.db.execute("DELETE FROM jargon WHERE scene=? AND id=?", (scene, id))
        return changed.rowcount == 1

    def matches(self, scene: str, texts: list[str], limit: int = 10) -> list[dict]:
        rows = self.db.execute(
            "SELECT term,CASE WHEN status='adopted' THEN meaning ELSE latest_meaning END AS meaning,"
            "CASE WHEN status='adopted' THEN '人工修订' ELSE '自动推断' END AS source "
            "FROM jargon WHERE scene=? AND status!='rejected'", (scene,),
        )
        found = [dict(row) for row in rows if row["meaning"]
                 and any(row["term"] in text for text in texts)]
        return sorted(found, key=lambda item: (-len(item["term"]), item["term"]))[:limit]

    def context(self, scene: str, sample_seq: int,
                exclude_uids: Sequence[str]) -> list[tuple[int, ChatMessage, float]]:
        """A matching human message with up to three qualifying neighbors on either side."""
        excluded = set(exclude_uids)

        def side(*, before: bool, needed: int) -> list[tuple[int, ChatMessage, float]]:
            cursor = sample_seq + (1 if before else 0)
            selected: list[tuple[int, ChatMessage, float]] = []
            while len(selected) < needed:
                operator, order = ("<", "DESC") if before else (">", "ASC")
                rows = self.db.execute(
                    "SELECT seq,body,received_at FROM messages WHERE scene=? "
                    f"AND seq{operator}? AND raw IS NOT NULL ORDER BY seq {order} LIMIT 32",
                    (scene, cursor),
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    if row["received_at"] is None:
                        continue
                    message = decode_message(row["body"])
                    if (message.send_status == "received" and not message.is_self
                            and message.sender.uid not in excluded and plain_text(message).strip()):
                        selected.append((row["seq"], message, row["received_at"]))
                        if len(selected) == needed:
                            break
                cursor = rows[-1]["seq"]
            return selected

        previous = side(before=True, needed=4)
        following = side(before=False, needed=3)
        return sorted([*previous, *following], key=lambda item: item[0])
