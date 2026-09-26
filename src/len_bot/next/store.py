"""Scene history and actual model exchanges in a separate SQLite database."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from .messages import ChatMessage, Segment, Sender


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        try:
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if tables:
                application_id = self.db.execute("PRAGMA application_id").fetchone()[0]
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                if application_id == 0x4C424E31 and version in (1, 2):
                    raise ValueError(
                        f"Next-core database format {version} requires offline migration while stopped: {path}; "
                        "run python -m len_bot.next.migrate from the isolated instance directory"
                    )
                if application_id != 0x4C424E31 or version != 3:
                    raise ValueError(f"Not a supported next-core database: {path}")
            else:
                self.db.executescript("""
                    BEGIN;
                    PRAGMA application_id = 1279413809;
                    PRAGMA user_version = 3;
                    CREATE TABLE messages (
                        seq INTEGER PRIMARY KEY, scene TEXT NOT NULL,
                        platform_id TEXT, body TEXT NOT NULL, raw TEXT,
                        received_at REAL
                    );
                    CREATE UNIQUE INDEX platform_messages ON messages(scene, platform_id)
                        WHERE platform_id IS NOT NULL;
                    CREATE TABLE mind_entries (
                        seq INTEGER PRIMARY KEY, scene TEXT NOT NULL,
                        message TEXT NOT NULL, created REAL NOT NULL
                    );
                    CREATE INDEX scene_entries ON mind_entries(scene, seq);
                    CREATE TABLE mind_sessions (
                        scene TEXT PRIMARY KEY,
                        compact_through INTEGER NOT NULL DEFAULT 0,
                        recap TEXT,
                        last_message_seq INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE turns (
                        id TEXT PRIMARY KEY, scene TEXT NOT NULL, started REAL NOT NULL,
                        ended REAL, status TEXT NOT NULL, error TEXT
                    );
                    CREATE TABLE model_calls (
                        id INTEGER PRIMARY KEY, turn_id TEXT NOT NULL, role TEXT NOT NULL,
                        started REAL NOT NULL, ended REAL, request TEXT NOT NULL,
                        response TEXT, usage TEXT, error TEXT
                    );
                    COMMIT;
                """)
        except BaseException:
            self.db.close()
            raise

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: object) -> None:
        self.db.close()

    def active_history(self, scene: str) -> tuple[str | None, list[tuple[int, dict]]]:
        session = self.db.execute(
            "SELECT compact_through,recap FROM mind_sessions WHERE scene=?", (scene,)
        ).fetchone()
        through = 0 if session is None else session[0]
        history = [(row[0], json.loads(row[1])) for row in self.db.execute(
            "SELECT seq,message FROM mind_entries WHERE scene=? AND seq>? ORDER BY seq",
            (scene, through),
        )]
        return (None if session is None else session[1]), history

    def entries(self, scene: str) -> list[dict]:
        recap, history = self.active_history(scene)
        return ([{"role": "user", "content": recap}] if recap is not None else []) + [
            message for _, message in history
        ]

    def _append(self, scene: str, message: dict) -> None:
        self.db.execute(
            "INSERT INTO mind_entries(scene,message,created) VALUES (?,?,?)",
            (scene, encode(message), time.time()),
        )

    def append(self, scene: str, message: dict) -> None:
        with self.db:
            self._append(scene, message)

    def _save_message(self, message: ChatMessage, raw: dict | None,
                      received_at: float | None = None) -> int:
        cursor = self.db.execute(
            "INSERT INTO messages(scene,platform_id,body,raw,received_at) VALUES (?,?,?,?,?)",
            (message.scene, message.platform_message_id, encode(asdict(message)),
             None if raw is None else encode(raw), received_at),
        )
        return cursor.lastrowid

    def enqueue(self, message: ChatMessage, raw: dict, received_at: float) -> int:
        """Store one received platform message without adding it to the mind yet."""
        with self.db:
            return self._save_message(message, raw, received_at)

    def pending_messages(self, scene: str) -> list[tuple[int, ChatMessage, float]]:
        rows = self.db.execute(
            "SELECT seq,body,received_at FROM messages WHERE scene=? AND raw IS NOT NULL "
            "AND seq>COALESCE((SELECT last_message_seq FROM mind_sessions WHERE scene=?),0) "
            "ORDER BY seq",
            (scene, scene),
        )
        return [(row[0], self._message(row[1]), row[2]) for row in rows]

    def _append_batch(self, scene: str, through: int, content: str) -> None:
        self._append(scene, {"role": "user", "content": content})
        self.db.execute(
            "INSERT INTO mind_sessions(scene,last_message_seq) VALUES (?,?) "
            "ON CONFLICT(scene) DO UPDATE SET last_message_seq=excluded.last_message_seq",
            (scene, through),
        )

    def append_batch(self, scene: str, through: int, content: str, *, turn_id: str) -> None:
        """Attach an arriving batch to an existing turn before its next request."""
        with self.db:
            updated = self.db.execute(
                "UPDATE turns SET status='queued' WHERE id=? AND scene=? AND ended IS NULL",
                (turn_id, scene),
            )
            if updated.rowcount != 1:
                raise ValueError(f"No active turn {turn_id} in scene {scene}")
            self._append_batch(scene, through, content)

    def complete_tool(self, scene: str, call_id: str, content: str,
                      expression: ChatMessage | None = None) -> None:
        with self.db:
            if expression is not None:
                self._save_message(expression, None)
            self._append(scene, {"role": "tool", "tool_call_id": call_id, "content": content})

    def recent(self, scene: str, limit: int = 20) -> list[ChatMessage]:
        rows = self.db.execute(
            "SELECT body FROM messages WHERE scene=? ORDER BY seq DESC LIMIT ?", (scene, limit)
        ).fetchall()
        return [self._message(row[0]) for row in reversed(rows)]

    def recent_context_messages(self, scene: str, limit: int = 20) -> list[ChatMessage]:
        """Only inbound messages already batched for the mind, plus saved outbound."""
        rows = self.db.execute(
            "SELECT body FROM messages WHERE scene=? AND (raw IS NULL OR seq<="
            "COALESCE((SELECT last_message_seq FROM mind_sessions WHERE scene=?),0)) "
            "ORDER BY seq DESC LIMIT ?",
            (scene, scene, limit),
        ).fetchall()
        return [self._message(row[0]) for row in reversed(rows)]

    @staticmethod
    def _message(body: str) -> ChatMessage:
        value = json.loads(body)
        value["sender"] = Sender(**value["sender"])
        value["segments"] = [Segment(**segment) for segment in value["segments"]]
        return ChatMessage(**value)

    def find_message(self, scene: str, platform_id: str) -> ChatMessage | None:
        row = self.db.execute(
            "SELECT body FROM messages WHERE scene=? AND platform_id=?", (scene, platform_id)
        ).fetchone()
        return None if row is None else self._message(row[0])

    def own_ids(self, scene: str) -> set[str]:
        return {row[0] for row in self.db.execute(
            "SELECT platform_id FROM messages WHERE scene=? AND platform_id IS NOT NULL "
            "AND json_extract(body,'$.is_self')=1", (scene,)
        )}

    def start_turn(self, scene: str, *, batch: tuple[int, str] | None = None) -> str:
        turn_id = str(uuid4())
        with self.db:
            self.db.execute(
                "UPDATE turns SET ended=?,status='interrupted',error=COALESCE(error,?) "
                "WHERE scene=? AND ended IS NULL",
                (time.time(), "Previous turn handed off to a resumed turn", scene),
            )
            self.db.execute("INSERT INTO turns VALUES (?,?,?,NULL,'queued',NULL)",
                            (turn_id, scene, time.time()))
            if batch is not None:
                self._append_batch(scene, batch[0], batch[1])
        return turn_id

    def end_turn(self, turn_id: str, status: str, error: str | None = None) -> bool:
        with self.db:
            if status in {"timeout", "cancelled"}:
                queued = self.db.execute(
                    "UPDATE turns SET error=? WHERE id=? AND status='queued' AND ended IS NULL",
                    (error, turn_id),
                )
                if queued.rowcount == 1:
                    return True
            self.db.execute("UPDATE turns SET ended=?,status=?,error=? WHERE id=?",
                            (time.time(), status, error, turn_id))
        return False

    def start_call(self, turn_id: str, role: str, request: dict) -> int:
        with self.db:
            updated = self.db.execute(
                "UPDATE turns SET status='running' WHERE id=? AND ended IS NULL", (turn_id,)
            )
            if updated.rowcount != 1:
                raise ValueError(f"No active turn {turn_id}")
            cursor = self.db.execute(
                "INSERT INTO model_calls(turn_id,role,started,request) VALUES (?,?,?,?)",
                (turn_id, role, time.time(), encode(request)),
            )
        return cursor.lastrowid

    def end_call(self, call_id: int, response: dict | None, usage: dict | None,
                 error: str | None = None, *, append_to_scene: str | None = None,
                 recap_for: tuple[str, int] | None = None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE model_calls SET ended=?,response=?,usage=?,error=? WHERE id=?",
                (time.time(), None if response is None else encode(response),
                 None if usage is None else encode(usage), error, call_id),
            )
            if append_to_scene is not None:
                self._append(append_to_scene, response["message"])
                if error is None and not response["message"].get("tool_calls"):
                    self.db.execute(
                        "UPDATE turns SET status='settling' WHERE id="
                        "(SELECT turn_id FROM model_calls WHERE id=?) AND ended IS NULL",
                        (call_id,),
                    )
            if recap_for is not None:
                scene, through = recap_for
                self.db.execute(
                    "INSERT INTO mind_sessions(scene,compact_through,recap) VALUES (?,?,?) "
                    "ON CONFLICT(scene) DO UPDATE SET compact_through=excluded.compact_through, "
                    "recap=excluded.recap",
                    (scene, through, response["message"]["content"]),
                )

    def last_mind_request(self, scene: str) -> dict | None:
        row = self.db.execute(
            "SELECT request FROM model_calls JOIN turns ON turns.id=model_calls.turn_id "
            "WHERE turns.scene=? AND model_calls.role='mind' ORDER BY model_calls.id DESC LIMIT 1",
            (scene,),
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def finish_pending_tools(self, scene: str, reason: str) -> None:
        # The only persisted pending work is the provider's native call/result group.
        pending: dict[str, str] = {}
        for entry in self.entries(scene):
            if entry["role"] == "assistant":
                calls = entry.get("tool_calls")
                if calls is not None:
                    for call in calls:
                        pending[call["id"]] = call["function"]["name"]
            elif entry["role"] == "tool":
                del pending[entry["tool_call_id"]]
        with self.db:
            for call_id, name in pending.items():
                self._append(scene, {"role": "tool", "tool_call_id": call_id,
                                     "content": f"{name} 中断：{reason}。未重放此调用。"})

    def recover(self, scene: str) -> bool:
        needs_resume = self.db.execute(
            "SELECT 1 FROM turns WHERE scene=? AND status IN ('queued','running') "
            "AND ended IS NULL LIMIT 1",
            (scene,),
        ).fetchone() is not None
        self.finish_pending_tools(scene, "隔离进程上次退出时没有保存执行结果")
        with self.db:
            self.db.execute(
                "UPDATE model_calls SET ended=?,error=? WHERE ended IS NULL AND turn_id IN "
                "(SELECT id FROM turns WHERE scene=?)",
                (time.time(), "Interrupted: previous process exited without a response", scene),
            )
            self.db.execute(
                "UPDATE turns SET ended=?,status='settled' WHERE scene=? AND status='settling' "
                "AND ended IS NULL",
                (time.time(), scene),
            )
        return needs_resume
