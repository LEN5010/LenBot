"""Scene history and actual model exchanges in a separate SQLite database."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal
from uuid import uuid4

from .messages import ChatMessage, Segment, Sender, plain_text
from .persona_stickers import PersonaSticker
from .sticker_assets import CollectedSticker
from .pricing import cost_summary
from .schedule_time import CronTimeError, next_cron, parse_cron


FORMAT_VERSION = 25


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def turn_record(row: sqlite3.Row) -> dict:
    turn = dict(row)
    first, wake = turn["first_expression_at"], turn["wake_received_at"]
    turn["turn_to_first_expression_seconds"] = None if first is None else first - turn["started"]
    turn["wake_to_first_expression_seconds"] = None if first is None or wake is None else first - wake
    return turn


@dataclass(frozen=True)
class Schedule:
    id: int
    scene: str
    created: float
    due_at: float
    timezone: str
    note: str
    target: str
    requester: str | None
    status: str
    delivered_at: float | None
    reason: str | None
    interval_seconds: int | None = None
    cron: str | None = None


@dataclass(frozen=True)
class WebPage:
    url: str
    final_url: str
    fetched_at: float
    media_type: str
    content: str
    notice: str


@dataclass(frozen=True)
class ImageAsset:
    jpeg: bytes
    width: int
    height: int
    animated: bool
    fetched_at: float
    description: str | None = None
    description_model: str | None = None
    described_at: float | None = None


class Store:
    def __init__(self, path: Path, *, now: Callable[[], float] = time.time):
        self.now = now
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        try:
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if tables:
                application_id = self.db.execute("PRAGMA application_id").fetchone()[0]
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                if application_id == 0x4C424E31 and version in range(1, FORMAT_VERSION):
                    raise ValueError(
                        f"Next-core database format {version} requires offline migration while stopped: {path}; "
                        "run python -m len_bot.next.migrate from the isolated instance directory"
                    )
                if application_id != 0x4C424E31 or version != FORMAT_VERSION:
                    raise ValueError(f"Not a supported next-core database: {path}")
            else:
                self.db.executescript(f"""
                    BEGIN;
                    PRAGMA application_id = 1279413809;
                    PRAGMA user_version = {FORMAT_VERSION};
                    CREATE TABLE messages (
                        seq INTEGER PRIMARY KEY, scene TEXT NOT NULL,
                        platform_id TEXT, body TEXT NOT NULL, raw TEXT,
                        received_at REAL
                    );
                    CREATE UNIQUE INDEX platform_messages ON messages(scene, platform_id)
                        WHERE platform_id IS NOT NULL;
                    CREATE VIRTUAL TABLE message_search USING fts5(
                        search_text, tokenize='trigram case_sensitive 1'
                    );
                    CREATE TABLE mind_entries (
                        seq INTEGER PRIMARY KEY, scene TEXT NOT NULL,
                        message TEXT NOT NULL, created REAL NOT NULL
                    );
                    CREATE INDEX scene_entries ON mind_entries(scene, seq);
                    CREATE TABLE mind_sessions (
                        scene TEXT PRIMARY KEY,
                        compact_through INTEGER NOT NULL DEFAULT 0,
                        recap TEXT,
                        last_message_seq INTEGER NOT NULL DEFAULT 0,
                        attention_state TEXT,
                        discovered_tools TEXT NOT NULL DEFAULT '[]'
                    );
                    CREATE TABLE turns (
                        id TEXT PRIMARY KEY, scene TEXT NOT NULL, started REAL NOT NULL,
                        ended REAL, status TEXT NOT NULL, error TEXT,
                        wake_received_at REAL, first_expression_at REAL, first_expression_delivery TEXT
                    );
                    CREATE TABLE model_calls (
                        id INTEGER PRIMARY KEY, turn_id TEXT NOT NULL, role TEXT NOT NULL,
                        started REAL NOT NULL, ended REAL, request TEXT NOT NULL,
                        response TEXT, usage TEXT, error TEXT, mind_entry_seq INTEGER, cost TEXT
                    );
                    CREATE INDEX turn_calls ON model_calls(turn_id, id);
                    CREATE TABLE schedules (
                        id INTEGER PRIMARY KEY, scene TEXT NOT NULL,
                        created REAL NOT NULL, due_at REAL NOT NULL,
                        timezone TEXT NOT NULL, note TEXT NOT NULL,
                        target TEXT NOT NULL, requester TEXT,
                        status TEXT NOT NULL, delivered_at REAL, reason TEXT,
                        interval_seconds INTEGER CHECK(interval_seconds BETWEEN 60 AND 31536000),
                        cron TEXT CHECK(cron IS NULL OR interval_seconds IS NULL)
                    );
                    CREATE INDEX schedules_status_due ON schedules(scene,status,due_at,id);
                    CREATE TABLE tasks (
                        id INTEGER PRIMARY KEY, scene TEXT NOT NULL, requester TEXT NOT NULL,
                        goal TEXT NOT NULL, deliverable TEXT NOT NULL, context TEXT NOT NULL,
                        input TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN
                            ('queued','running','waiting_input','done','failed','cancelled')),
                        created REAL NOT NULL, started REAL, ended REAL,
                        container TEXT, question TEXT, summary TEXT, error TEXT
                    );
                    CREATE INDEX tasks_scene_status ON tasks(scene,status,id);
                    CREATE INDEX tasks_status ON tasks(status,id);
                    CREATE INDEX tasks_containers ON tasks(id) WHERE container IS NOT NULL;
                    CREATE INDEX tasks_requester_created ON tasks(scene,requester,created);
                    CREATE TABLE task_events (
                        id INTEGER PRIMARY KEY, scene TEXT NOT NULL, task_id INTEGER NOT NULL,
                        kind TEXT NOT NULL, body TEXT NOT NULL, notice TEXT,
                        created REAL NOT NULL, delivered_at REAL
                    );
                    CREATE INDEX task_events_task ON task_events(scene,task_id,id);
                    CREATE INDEX task_events_pending_notice ON task_events(scene,id)
                        WHERE notice IS NOT NULL AND delivered_at IS NULL;
                    CREATE TABLE task_files (
                        id INTEGER PRIMARY KEY, scene TEXT NOT NULL, task_id INTEGER NOT NULL,
                        name TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL,
                        note TEXT, created REAL NOT NULL
                    );
                    CREATE INDEX task_files_task ON task_files(scene,task_id,id);
                    CREATE TABLE web_documents (
                        id INTEGER PRIMARY KEY, scene TEXT NOT NULL, body TEXT NOT NULL
                    );
                    CREATE TABLE image_cache (
                        scene TEXT NOT NULL, platform_id TEXT NOT NULL,
                        image_index INTEGER NOT NULL, jpeg BLOB NOT NULL,
                        width INTEGER NOT NULL, height INTEGER NOT NULL,
                        animated INTEGER NOT NULL, fetched_at REAL NOT NULL,
                        description TEXT, description_model TEXT, described_at REAL,
                        PRIMARY KEY(scene, platform_id, image_index)
                    );
                    CREATE TABLE media (
                        id INTEGER PRIMARY KEY, persona_id TEXT, file TEXT,
                        source_message_seq INTEGER, source_image_index INTEGER,
                        mime_type TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
                        animated INTEGER NOT NULL, data BLOB NOT NULL,
                        CHECK ((persona_id IS NOT NULL AND file IS NOT NULL AND
                                source_message_seq IS NULL AND source_image_index IS NULL) OR
                               (persona_id IS NULL AND file IS NULL AND
                                source_message_seq IS NOT NULL AND source_image_index IS NOT NULL)),
                        UNIQUE(source_message_seq,source_image_index)
                    );
                    CREATE INDEX media_persona_file ON media(persona_id,file,id);
                    CREATE TABLE message_media (
                        message_seq INTEGER NOT NULL, image_index INTEGER NOT NULL,
                        media_id INTEGER NOT NULL, description TEXT NOT NULL,
                        emotions TEXT NOT NULL, tags TEXT NOT NULL,
                        PRIMARY KEY(message_seq,image_index)
                    );
                    CREATE INDEX message_media_media ON message_media(media_id,message_seq);
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
                        response TEXT, usage TEXT, cost TEXT, error TEXT
                    );
                    CREATE INDEX jargon_calls_scene ON jargon_calls(scene,id);
                    CREATE TABLE sticker_candidates (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,
                        source_message_seq INTEGER NOT NULL, image_index INTEGER NOT NULL,
                        media_id INTEGER, status TEXT NOT NULL CHECK(status IN
                            ('queued','running','complete','failed','interrupted')),
                        review TEXT NOT NULL CHECK(review IN ('pending','adopted','rejected')),
                        description TEXT, text TEXT, emotions TEXT NOT NULL DEFAULT '[]',
                        tags TEXT NOT NULL DEFAULT '[]', is_sticker INTEGER,
                        created REAL NOT NULL, updated REAL NOT NULL, error TEXT,
                        UNIQUE(scene,source_message_seq,image_index)
                    );
                    CREATE INDEX sticker_candidates_status ON sticker_candidates(scene,status,review,id);
                    CREATE INDEX sticker_candidates_review ON sticker_candidates(scene,review,id);
                    CREATE TABLE sticker_calls (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,
                        candidate_id INTEGER NOT NULL, source_message_seq INTEGER NOT NULL,
                        image_index INTEGER NOT NULL, started REAL NOT NULL, ended REAL,
                        status TEXT NOT NULL CHECK(status IN ('running','complete','failed','interrupted')),
                        model_started REAL, request TEXT, response TEXT, usage TEXT, cost TEXT, error TEXT
                    );
                    CREATE INDEX sticker_calls_scene ON sticker_calls(scene,id);
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
                    CREATE INDEX reply_effects_open ON reply_effects(scene,id) WHERE closed_at IS NULL;
                    CREATE TABLE reply_effect_calls (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,
                        effect_ids TEXT NOT NULL, started REAL NOT NULL, ended REAL,
                        status TEXT NOT NULL CHECK(status IN ('running','complete','failed','interrupted')),
                        model_started REAL, request TEXT NOT NULL,
                        response TEXT, usage TEXT, cost TEXT, error TEXT
                    );
                    CREATE INDEX reply_effect_calls_scene ON reply_effect_calls(scene,id);
                    CREATE TABLE proactive_wakes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,
                        turn_id TEXT NOT NULL UNIQUE, woke_at REAL NOT NULL,
                        local_date TEXT NOT NULL, idle_since REAL NOT NULL,
                        outcome TEXT CHECK(outcome IS NULL OR outcome IN ('silent','answered','ignored','unobserved')),
                        closed_at REAL, UNIQUE(scene,local_date)
                    );
                    CREATE TABLE plugin_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL, plugin TEXT NOT NULL,
                        kind TEXT NOT NULL CHECK(kind IN ('event','reply')),
                        content TEXT NOT NULL, created REAL NOT NULL, delivered_at REAL
                    );
                    CREATE INDEX plugin_events_pending ON plugin_events(scene,id) WHERE delivered_at IS NULL;
                    COMMIT;
                """)
        except BaseException:
            self.db.close()
            raise

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: object) -> None:
        self.db.close()

    def save_web_page(self, scene: str, page: WebPage) -> int:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO web_documents(scene,body) VALUES (?,?)",
                (scene, encode(asdict(page))),
            )
        return cursor.lastrowid

    def web_page(self, scene: str, document: int) -> WebPage | None:
        row = self.db.execute(
            "SELECT body FROM web_documents WHERE scene=? AND id=?", (scene, document)
        ).fetchone()
        return None if row is None else WebPage(**json.loads(row[0]))

    def message_media(self, scene: str, message_seq: int) -> list[dict]:
        rows = self.db.execute(
            "SELECT mm.image_index,m.file,m.source_message_seq,m.source_image_index,"
            "mm.description,m.mime_type,m.width,m.height,"
            "m.animated,length(m.data) AS bytes FROM message_media mm "
            "JOIN messages msg ON msg.seq=mm.message_seq JOIN media m ON m.id=mm.media_id "
            "WHERE msg.scene=? AND msg.seq=? ORDER BY mm.image_index", (scene, message_seq),
        )
        return [{**dict(row), "animated": bool(row["animated"])} for row in rows]

    def original_image(self, scene: str, message_seq: int, image_index: int) -> tuple[str, bytes] | None:
        row = self.db.execute(
            "SELECT m.mime_type,m.data FROM message_media mm "
            "JOIN messages msg ON msg.seq=mm.message_seq JOIN media m ON m.id=mm.media_id "
            "WHERE msg.scene=? AND msg.seq=? AND mm.image_index=?",
            (scene, message_seq, image_index),
        ).fetchone()
        return None if row is None else (row[0], row[1])

    def platform_image(self, scene: str, platform_id: str, image_index: int) -> tuple[str, bytes] | None:
        row = self.db.execute(
            "SELECT seq FROM messages WHERE scene=? AND platform_id=?", (scene, platform_id),
        ).fetchone()
        return None if row is None else self.original_image(scene, row[0], image_index)

    def sticker_usage(self, scene: str, persona_id: str) -> dict[str, int]:
        return {row[0]: row[1] for row in self.db.execute(
            "SELECT m.file,count(*) FROM message_media mm "
            "JOIN messages msg ON msg.seq=mm.message_seq JOIN media m ON m.id=mm.media_id "
            "WHERE msg.scene=? AND m.persona_id=? AND json_extract(msg.body,'$.send_status')='sent' "
            "GROUP BY m.file", (scene, persona_id),
        )}

    def _save_sticker(self, message_seq: int, persona_id: str, sticker: PersonaSticker) -> None:
        previous = self.db.execute(
            "SELECT id,data FROM media WHERE persona_id=? AND file=? ORDER BY id DESC LIMIT 1",
            (persona_id, sticker.file),
        ).fetchone()
        if previous is not None and previous[1] == sticker.data:
            media_id = previous[0]
        else:
            media_id = self.db.execute(
                "INSERT INTO media(persona_id,file,mime_type,width,height,animated,data) VALUES (?,?,?,?,?,?,?)",
                (persona_id, sticker.file, sticker.mime_type, sticker.width, sticker.height,
                 int(sticker.animated), sticker.data),
            ).lastrowid
        self.db.execute(
            "INSERT INTO message_media(message_seq,image_index,media_id,description,emotions,tags) "
            "VALUES (?,1,?,?,?,?)",
            (message_seq, media_id, sticker.description, encode(sticker.emotions), encode(sticker.tags)),
        )

    def _save_collected_sticker(self, message_seq: int, sticker: CollectedSticker) -> None:
        self.db.execute(
            "INSERT INTO message_media(message_seq,image_index,media_id,description,emotions,tags) "
            "VALUES (?,1,?,?,?,?)",
            (message_seq, sticker.media_id, sticker.description,
             encode(sticker.emotions), encode(sticker.tags)),
        )

    def image(self, scene: str, platform_id: str, image_index: int) -> ImageAsset | None:
        row = self.db.execute(
            "SELECT jpeg,width,height,animated,fetched_at,description,description_model,described_at "
            "FROM image_cache WHERE scene=? AND platform_id=? AND image_index=?",
            (scene, platform_id, image_index),
        ).fetchone()
        if row is None:
            return None
        return ImageAsset(row[0], row[1], row[2], bool(row[3]), row[4], row[5], row[6], row[7])

    def save_image(self, scene: str, platform_id: str, image_index: int, asset: ImageAsset) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO image_cache(scene,platform_id,image_index,jpeg,width,height,animated,"
                "fetched_at,description,description_model,described_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (scene, platform_id, image_index, asset.jpeg, asset.width, asset.height,
                 int(asset.animated), asset.fetched_at, asset.description,
                 asset.description_model, asset.described_at),
            )

    def save_image_description(self, scene: str, platform_id: str, image_index: int,
                               description: str, model: str, described_at: float) -> None:
        with self.db:
            updated = self.db.execute(
                "UPDATE image_cache SET description=?,description_model=?,described_at=? "
                "WHERE scene=? AND platform_id=? AND image_index=?",
                (description, model, described_at, scene, platform_id, image_index),
            )
            if updated.rowcount != 1:
                raise ValueError(f"No cached image {image_index} for platform message {platform_id} in {scene}")

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

    def install_portable_recaps(self, updates: dict[str, tuple[int, int, str]]) -> None:
        """Set checked scene cutpoints together, preserving every original entry.

        Each value is (previous compact_through, final entry seq, portable text).
        """
        if not updates:
            return
        self.db.execute("BEGIN EXCLUSIVE")
        try:
            for scene, (expected_through, through, recap) in updates.items():
                session = self.db.execute(
                    "SELECT compact_through FROM mind_sessions WHERE scene=?", (scene,)
                ).fetchone()
                current_through = 0 if session is None else session[0]
                latest = self.db.execute(
                    "SELECT COALESCE(MAX(seq),0) FROM mind_entries WHERE scene=?", (scene,)
                ).fetchone()[0]
                if current_through != expected_through or latest != through:
                    raise ValueError(f"Portable history changed before commit in {scene}")
                active = self.db.execute(
                    "SELECT id,status FROM turns WHERE scene=? AND ended IS NULL "
                    "AND status IN ('running','settling') LIMIT 1", (scene,),
                ).fetchone()
                if active is not None:
                    raise ValueError(f"Scene {scene} still has turn {active['id']} in {active['status']}")
                call = self.db.execute(
                    "SELECT model_calls.id FROM model_calls JOIN turns ON turns.id=model_calls.turn_id "
                    "WHERE turns.scene=? AND model_calls.ended IS NULL LIMIT 1", (scene,),
                ).fetchone()
                if call is not None:
                    raise ValueError(f"Scene {scene} still has unfinished model call {call['id']}")
                self.db.execute(
                    "INSERT INTO mind_sessions(scene,compact_through,recap,discovered_tools) "
                    "VALUES (?,?,?,'[]') ON CONFLICT(scene) DO UPDATE SET "
                    "compact_through=excluded.compact_through,recap=excluded.recap,"
                    "discovered_tools=excluded.discovered_tools",
                    (scene, through, recap),
                )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def entries(self, scene: str) -> list[dict]:
        recap, history = self.active_history(scene)
        return ([{"role": "user", "content": recap}] if recap is not None else []) + [
            message for _, message in history
        ]

    def mind_history_page(self, scene: str, *, before: int | None, limit: int,
                          active_only: bool) -> dict:
        session = self.db.execute(
            "SELECT compact_through,recap,last_message_seq FROM mind_sessions WHERE scene=?", (scene,),
        ).fetchone()
        through = 0 if session is None else session[0]
        conditions, values = ["scene=?"], [scene]
        if active_only:
            conditions.append("seq>?")
            values.append(through)
        if before is not None:
            conditions.append("seq<?")
            values.append(before)
        rows = self.db.execute(
            "SELECT seq,message,created FROM mind_entries WHERE " + " AND ".join(conditions)
            + " ORDER BY seq DESC LIMIT ?", (*values, limit + 1),
        ).fetchall()
        selected = rows[:limit]
        return {
            "recap": None if session is None else session[1], "compact_through": through,
            "last_message_seq": 0 if session is None else session[2],
            "entries": [{"seq": row[0], "message": json.loads(row[1]), "created": row[2],
                         "active": row[0] > through} for row in reversed(selected)],
            "next_before": selected[-1][0] if len(rows) > limit else None,
        }

    def daily_overview(self, scenes: Sequence[str], since: float, until: float) -> dict:
        placeholders = ",".join("?" for _ in scenes)
        messages = dict(self.db.execute(
            "SELECT json_extract(body,'$.send_status'),COUNT(*) FROM messages "
            f"WHERE scene IN ({placeholders}) "
            "AND CASE WHEN raw IS NULL THEN json_extract(body,'$.time') ELSE received_at END>=? "
            "AND CASE WHEN raw IS NULL THEN json_extract(body,'$.time') ELSE received_at END<? "
            "GROUP BY json_extract(body,'$.send_status')", (*scenes, since, until),
        ))
        turns = dict(self.db.execute(
            f"SELECT status,COUNT(*) FROM turns WHERE scene IN ({placeholders}) "
            "AND started>=? AND started<? GROUP BY status", (*scenes, since, until),
        ))
        calls = self.db.execute(
            "SELECT model_calls.ended,model_calls.cost FROM model_calls "
            "JOIN turns ON turns.id=model_calls.turn_id "
            f"WHERE turns.scene IN ({placeholders}) AND model_calls.started>=? AND model_calls.started<?",
            (*scenes, since, until),
        ).fetchall()
        calls.extend(self.db.execute(
            "SELECT ended,cost FROM learning_batches "
            f"WHERE scene IN ({placeholders}) AND model_started>=? AND model_started<?",
            (*scenes, since, until),
        ).fetchall())
        calls.extend(self.db.execute(
            "SELECT ended,cost FROM expression_embedding_calls "
            f"WHERE scene IN ({placeholders}) AND started>=? AND started<?",
            (*scenes, since, until),
        ).fetchall())
        calls.extend(self.db.execute(
            "SELECT ended,cost FROM jargon_calls "
            f"WHERE scene IN ({placeholders}) AND model_started>=? AND model_started<?",
            (*scenes, since, until),
        ).fetchall())
        calls.extend(self.db.execute(
            "SELECT ended,cost FROM sticker_calls "
            f"WHERE scene IN ({placeholders}) AND model_started>=? AND model_started<?",
            (*scenes, since, until),
        ).fetchall())
        calls.extend(self.db.execute(
            "SELECT ended,cost FROM reply_effect_calls "
            f"WHERE scene IN ({placeholders}) AND model_started>=? AND model_started<?",
            (*scenes, since, until),
        ).fetchall())
        reactions = dict(self.db.execute(
            "SELECT COALESCE(reaction,CASE WHEN observed_seqs='[]' THEN 'no_messages' ELSE 'waiting' END),"
            f"COUNT(*) FROM reply_effects WHERE scene IN ({placeholders}) AND closed_at IS NOT NULL "
            "AND first_sent_at>=? AND first_sent_at<? GROUP BY 1", (*scenes, since, until),
        ))
        costs = cost_summary([None if raw is None else json.loads(raw) for _, raw in calls])
        unfinished = sum(ended is None for ended, _ in calls)
        pending = self.db.execute(
            f"SELECT COUNT(*) FROM schedules WHERE scene IN ({placeholders}) AND status='pending'", scenes,
        ).fetchone()[0]
        errors = [dict(row) for row in self.db.execute(
            f"SELECT id,scene,started,status,error FROM turns WHERE scene IN ({placeholders}) "
            "AND error IS NOT NULL ORDER BY started DESC LIMIT 10", scenes,
        )]
        return {"messages": messages, "turns": turns, "model_calls": len(calls),
                "unfinished_calls": unfinished, "unknown_cost_calls": costs["unknown_calls"],
                "estimated_costs": costs["known_amounts"],
                "pending_schedules": pending, "recent_errors": errors,
                "reply_effects": reactions}

    def _append(self, scene: str, message: dict) -> int:
        cursor = self.db.execute(
            "INSERT INTO mind_entries(scene,message,created) VALUES (?,?,?)",
            (scene, encode(message), self.now()),
        )
        return cursor.lastrowid

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
        seq = cursor.lastrowid
        self.db.execute(
            "INSERT INTO message_search(rowid,search_text) VALUES (?,?)",
            (seq, plain_text(message).casefold()),
        )
        return seq

    def _save_attention(self, scene: str, state: dict) -> None:
        self.db.execute(
            "INSERT INTO mind_sessions(scene,attention_state) VALUES (?,?) "
            "ON CONFLICT(scene) DO UPDATE SET attention_state=excluded.attention_state",
            (scene, encode(state)),
        )

    def load_attention(self, scene: str) -> dict | None:
        row = self.db.execute(
            "SELECT attention_state FROM mind_sessions WHERE scene=?", (scene,)
        ).fetchone()
        return None if row is None or row[0] is None else json.loads(row[0])

    def save_attention(self, scene: str, state: dict) -> None:
        with self.db:
            self._save_attention(scene, state)

    def load_discovered_tools(self, scene: str) -> list[str]:
        row = self.db.execute(
            "SELECT discovered_tools FROM mind_sessions WHERE scene=?", (scene,)
        ).fetchone()
        return [] if row is None else json.loads(row[0])

    def save_discovered_tools(self, scene: str, names: list[str]) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO mind_sessions(scene,discovered_tools) VALUES (?,?) "
                "ON CONFLICT(scene) DO UPDATE SET discovered_tools=excluded.discovered_tools",
                (scene, encode(names)),
            )

    def enqueue(self, message: ChatMessage, raw: dict, received_at: float,
                *, attention_state: dict | None = None,
                collect_stickers: bool = False) -> int:
        """Store one received platform message without adding it to the mind yet."""
        with self.db:
            seq = self._save_message(message, raw, received_at)
            if collect_stickers:
                image_index = 0
                for segment in message.segments:
                    if segment.type == "image":
                        image_index += 1
                        self.db.execute(
                            "INSERT INTO sticker_candidates(scene,source_message_seq,image_index,"
                            "status,review,created,updated) VALUES (?,?,?,'queued','pending',?,?)",
                            (message.scene, seq, image_index, received_at, received_at),
                        )
            if attention_state is not None:
                self._save_attention(message.scene, attention_state)
            return seq

    def pending_messages(self, scene: str) -> list[tuple[int, ChatMessage, float]]:
        rows = self.db.execute(
            "SELECT seq,body,received_at FROM messages WHERE scene=? AND raw IS NOT NULL "
            "AND NOT (json_extract(body,'$.is_self')=1 AND json_extract(body,'$.send_status')='sent') "
            "AND seq>COALESCE((SELECT last_message_seq FROM mind_sessions WHERE scene=?),0) "
            "ORDER BY seq",
            (scene, scene),
        )
        return [(row[0], self._message(row[1]), row[2]) for row in rows]

    def pending_attention_sample(self, scene: str, exclude_uids: Sequence[str],
                                 limit: int = 20) -> list[tuple[ChatMessage, float]]:
        """Recent eligible non-self input with known host arrival, oldest first."""
        excluded = tuple(exclude_uids)
        exclude_clause = (
            " AND json_extract(body,'$.sender.uid') NOT IN (" + ",".join("?" for _ in excluded) + ")"
            if excluded else ""
        )
        rows = self.db.execute(
            "SELECT body,received_at FROM messages WHERE scene=? AND raw IS NOT NULL "
            "AND received_at IS NOT NULL "
            "AND seq>COALESCE((SELECT last_message_seq FROM mind_sessions WHERE scene=?),0) "
            "AND json_extract(body,'$.is_self')=0" + exclude_clause + " ORDER BY seq DESC LIMIT ?",
            (scene, scene, *excluded, limit),
        ).fetchall()
        return [(self._message(body), received_at) for body, received_at in reversed(rows)]

    def last_pending_arrival(self, scene: str, exclude_uids: Sequence[str]) -> float | None:
        """Latest unbatched arrival eligible for merging into the next input."""
        excluded = tuple(exclude_uids)
        exclude_clause = (
            " AND (json_extract(body,'$.mentions_bot')=1 OR scene LIKE 'private:%' "
            "OR json_extract(body,'$.sender.uid') NOT IN (" + ",".join("?" for _ in excluded) + "))"
            if excluded else ""
        )
        row = self.db.execute(
            "SELECT received_at FROM messages WHERE scene=? AND raw IS NOT NULL "
            "AND received_at IS NOT NULL "
            "AND seq>COALESCE((SELECT last_message_seq FROM mind_sessions WHERE scene=?),0) "
            "AND json_extract(body,'$.is_self')=0" + exclude_clause + " ORDER BY seq DESC LIMIT 1",
            (scene, scene, *excluded),
        ).fetchone()
        return None if row is None else float(row[0])

    def _append_batch(self, scene: str, through: int, contents: list[str]) -> None:
        for content in contents:
            self._append(scene, {"role": "user", "content": content})
        self.db.execute(
            "INSERT INTO mind_sessions(scene,last_message_seq) VALUES (?,?) "
            "ON CONFLICT(scene) DO UPDATE SET last_message_seq=excluded.last_message_seq",
            (scene, through),
        )

    def append_batch(self, scene: str, through: int, contents: list[str], *, turn_id: str,
                     attention_state: dict | None = None) -> None:
        """Attach an arriving batch to an existing turn before its next request."""
        with self.db:
            updated = self.db.execute(
                "UPDATE turns SET status='queued' WHERE id=? AND scene=? AND ended IS NULL",
                (turn_id, scene),
            )
            if updated.rowcount != 1:
                raise ValueError(f"No active turn {turn_id} in scene {scene}")
            self._append_batch(scene, through, contents)
            if attention_state is not None:
                self._save_attention(scene, attention_state)

    def append_quiet(self, scene: str, batch: tuple[int, list[str]], *,
                     attention_state: dict, note: str | None) -> int | None:
        """Persist the batch, once-per-period attempt and actual notice together."""
        entry_seq = None
        with self.db:
            self._append_batch(scene, batch[0], batch[1])
            if note is not None:
                entry_seq = self._append(scene, {"role": "user", "content": note})
            self._save_attention(scene, attention_state)
        return entry_seq

    def complete_tool(self, scene: str, call_id: str, content: str, *,
                      discovered_tools: list[str] | None = None) -> None:
        with self.db:
            self._append(scene, {"role": "tool", "tool_call_id": call_id, "content": content})
            if discovered_tools is not None:
                self.db.execute(
                    "INSERT INTO mind_sessions(scene,discovered_tools) VALUES (?,?) "
                    "ON CONFLICT(scene) DO UPDATE SET discovered_tools=excluded.discovered_tools",
                    (scene, encode(discovered_tools)),
                )

    def prepare_expression(self, scene: str, call_id: str, content: str) -> int:
        """Save the complete actual words before starting any part."""
        with self.db:
            return self._append(scene, {
                "role": "tool", "tool_call_id": call_id, "content": content,
            })

    def start_expression_part(self, entry_seq: int, expression: ChatMessage, content: str,
                              *, turn_id: str | None = None,
                              sticker: tuple[str, PersonaSticker] | CollectedSticker | None = None) -> int:
        """Only attempted parts become chat messages; the remainder stays in the result."""
        with self.db:
            message_seq = self._save_message(expression, None)
            if sticker is not None:
                if isinstance(sticker, CollectedSticker):
                    self._save_collected_sticker(message_seq, sticker)
                else:
                    self._save_sticker(message_seq, *sticker)
            self.db.execute("UPDATE mind_entries SET message=json_set(message,'$.content',?) WHERE seq=?",
                            (content, entry_seq))
            if turn_id is not None and expression.send_status == "simulated":
                self._first_expression(turn_id, "simulated")
        return message_seq

    def _first_expression(self, turn_id: str, delivery: Literal["simulated", "sent"]) -> None:
        # Part of the same message/receipt transaction, not an independent event.
        self.db.execute(
            "UPDATE turns SET first_expression_at=?,first_expression_delivery=? "
            "WHERE id=? AND first_expression_at IS NULL", (self.now(), delivery, turn_id),
        )

    def expression_error(self, entry_seq: int, error: str) -> str:
        with self.db:
            self.db.execute(
                "UPDATE mind_entries SET message=json_set(message,'$.content',"
                "json_extract(message,'$.content')||char(10)||?) WHERE seq=?", (error, entry_seq),
            )
            content = self.db.execute("SELECT json_extract(message,'$.content') FROM mind_entries WHERE seq=?",
                                      (entry_seq,)).fetchone()[0]
        return content

    def finish_expression(self, positions: tuple[int, int | None], expression: ChatMessage, content: str,
                          *, turn_id: str | None = None) -> int:
        """Finish this live call; return the message position kept after an early echo merge.

        ``positions`` is (message, mind entry); a host-originated part has no mind entry.
        """
        message_seq, entry_seq = positions
        kept = message_seq
        with self.db:
            echo = (None if expression.platform_message_id is None else self.db.execute(
                "SELECT seq,body FROM messages WHERE scene=? AND platform_id=?",
                (expression.scene, expression.platform_message_id),
            ).fetchone())
            if echo is not None:
                received = self._message(echo[1])
                if (not received.is_self or received.sender.uid != expression.sender.uid
                        or echo[0] <= message_seq or received.send_status != "received"):
                    raise ValueError(f"Send receipt conflicts with an existing message: {expression.platform_message_id}")
                self.db.execute("UPDATE messages SET body=? WHERE seq=?",
                                (encode(asdict(replace(received, send_status="sent"))), echo[0]))
                self.db.execute("UPDATE message_media SET message_seq=? WHERE message_seq=?",
                                (echo[0], message_seq))
                self.db.execute("DELETE FROM message_search WHERE rowid=?", (message_seq,))
                self.db.execute("DELETE FROM messages WHERE seq=?", (message_seq,))
                kept = echo[0]
            else:
                self.db.execute("UPDATE messages SET platform_id=?,body=? WHERE seq=?",
                                (expression.platform_message_id, encode(asdict(expression)), message_seq))
            if entry_seq is not None:
                self.db.execute(
                    "UPDATE mind_entries SET message=json_set(message,'$.content',?) WHERE seq=?",
                    (content, entry_seq),
                )
            if turn_id is not None and expression.send_status == "sent":
                self._first_expression(turn_id, "sent")
        return kept

    def attach_echo(self, message: ChatMessage, raw: dict, received_at: float) -> None:
        """Attach a later platform event to an already confirmed own expression."""
        with self.db:
            row = self.db.execute("SELECT seq,body,raw FROM messages WHERE scene=? AND platform_id=?",
                                  (message.scene, message.platform_message_id)).fetchone()
            saved = self._message(row[1])
            if not message.is_self or message.sender.uid != saved.sender.uid:
                raise ValueError(f"Own message echo conflicts with another sender: {message.platform_message_id}")
            if row[2] is not None:
                return
            self.db.execute("UPDATE messages SET body=?,raw=?,received_at=? WHERE seq=?",
                            (encode(asdict(replace(message, id=saved.id, send_status="sent"))),
                             encode(raw), received_at, row[0]))
            self.db.execute("UPDATE message_search SET search_text=? WHERE rowid=?",
                            (plain_text(message).casefold(), row[0]))

    def recent(self, scene: str, limit: int = 20) -> list[ChatMessage]:
        rows = self.db.execute(
            "SELECT body FROM messages WHERE scene=? ORDER BY seq DESC LIMIT ?", (scene, limit)
        ).fetchall()
        return [self._message(row[0]) for row in reversed(rows)]

    def recent_records(self, scene: str, limit: int = 50, *, snapshot: int | None = None,
                       offset: int = 0) -> list[tuple[int, ChatMessage]]:
        conditions = "scene=?" if snapshot is None else "scene=? AND seq<=?"
        values = (scene,) if snapshot is None else (scene, snapshot)
        rows = self.db.execute(
            f"SELECT seq,body FROM messages WHERE {conditions} ORDER BY seq DESC LIMIT ? OFFSET ?",
            (*values, limit, offset),
        ).fetchall()
        return [(row[0], self._message(row[1])) for row in reversed(rows)]

    def recent_turns(self, scene: str, limit: int = 20) -> list[dict]:
        return [turn_record(row) for row in self.db.execute(
            "SELECT * FROM turns WHERE scene=? ORDER BY started DESC,id DESC LIMIT ?", (scene, limit))]

    def turn_detail(self, scene: str, turn_id: str) -> dict | None:
        turn = self.db.execute("SELECT * FROM turns WHERE scene=? AND id=?", (scene, turn_id)).fetchone()
        if turn is None:
            return None
        calls = []
        for row in self.db.execute("SELECT * FROM model_calls WHERE turn_id=? ORDER BY id", (turn_id,)):
            call = dict(row)
            for key in ("request", "response", "usage", "cost"):
                call[key] = None if call[key] is None else json.loads(call[key])
            entry_seq = call.pop("mind_entry_seq")
            call["tool_results"] = None if entry_seq is None else []
            if entry_seq is not None and call["response"]["message"].get("tool_calls"):
                for entry in self.db.execute(
                    "SELECT message FROM mind_entries WHERE scene=? AND seq>? ORDER BY seq",
                    (scene, entry_seq),
                ):
                    message = json.loads(entry[0])
                    if message["role"] == "assistant":
                        break
                    if message["role"] == "tool":
                        call["tool_results"].append(message)
                        if len(call["tool_results"]) == len(call["response"]["message"]["tool_calls"]):
                            break
            calls.append(call)
        return {"turn": turn_record(turn), "calls": calls}

    def recent_context_messages(self, scene: str, limit: int = 20) -> list[ChatMessage]:
        """Only inbound messages already batched for the mind, plus saved outbound."""
        rows = self.db.execute(
            "SELECT body FROM messages WHERE scene=? AND (raw IS NULL "
            "OR (json_extract(body,'$.is_self')=1 AND json_extract(body,'$.send_status')='sent') OR seq<="
            "COALESCE((SELECT last_message_seq FROM mind_sessions WHERE scene=?),0)) "
            "ORDER BY seq DESC LIMIT ?",
            (scene, scene, limit),
        ).fetchall()
        return [self._message(row[0]) for row in reversed(rows)]

    def attention_sample(self, scene: str, limit: int = 20, *,
                         exclude_uids: Sequence[str] = ()) -> list[tuple[ChatMessage, float]]:
        """Recent messages with instance observation or saved outbound time."""
        excluded = tuple(exclude_uids)
        exclude_clause = (
            " AND (json_extract(body,'$.is_self')=1 OR "
            "json_extract(body,'$.sender.uid') NOT IN (" + ",".join("?" for _ in excluded) + "))"
            if excluded else ""
        )
        rows = self.db.execute(
            "SELECT body,received_at,raw FROM messages WHERE scene=? "
            "AND (received_at IS NOT NULL OR raw IS NULL)" + exclude_clause + " ORDER BY seq DESC LIMIT ?",
            (scene, *excluded, limit),
        ).fetchall()
        sample = []
        for body, received_at, raw in reversed(rows):
            message = self._message(body)
            sample.append((message, message.time if raw is None else received_at))
        return sample

    def last_self_time(self, scene: str) -> float | None:
        row = self.db.execute(
            "SELECT CASE WHEN raw IS NULL THEN json_extract(body,'$.time') "
            "ELSE received_at END FROM messages WHERE scene=? "
            "AND json_extract(body,'$.is_self')=1 "
            "AND json_extract(body,'$.send_status') IN ('sent','received','simulated') "
            "AND (raw IS NULL OR received_at IS NOT NULL) "
            "ORDER BY seq DESC LIMIT 1",
            (scene,),
        ).fetchone()
        return None if row is None else float(row[0])

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

    def max_message_seq(self, scene: str) -> int:
        return self.db.execute(
            "SELECT COALESCE(MAX(seq),0) FROM messages WHERE scene=?", (scene,)
        ).fetchone()[0]

    def memory_messages(self, scene: str, after: int, *, limit: int,
                        through: int | None = None,
                        exclude_records: Sequence[int] = ()) -> list[tuple[int, ChatMessage, float]]:
        """Actual inbound and confirmed outbound content, never simulated expressions."""
        conditions = ["scene=?", "seq>?", "json_extract(body,'$.send_status') IN ('received','sent')",
                      "(raw IS NULL OR received_at IS NOT NULL)"]
        values: list[object] = [scene, after]
        if through is not None:
            conditions.append("seq<=?")
            values.append(through)
        if exclude_records:
            conditions.append("seq NOT IN (SELECT value FROM json_each(?))")
            values.append(encode(exclude_records))
        rows = self.db.execute(
            "SELECT seq,body,CASE WHEN raw IS NULL THEN json_extract(body,'$.time') ELSE received_at END "
            "FROM messages WHERE " + " AND ".join(conditions) + " ORDER BY seq LIMIT ?", [*values, limit],
        ).fetchall()
        return [(row[0], self._message(row[1]), row[2]) for row in rows]

    def last_memory_input_at(self, scene: str, after: int, *, exclude_records: Sequence[int] = ()) -> float | None:
        row = self.db.execute(
            "SELECT MAX(CASE WHEN raw IS NULL THEN json_extract(body,'$.time') ELSE received_at END) "
            "FROM messages WHERE scene=? AND seq>? AND json_extract(body,'$.send_status') IN ('received','sent') "
            "AND (raw IS NULL OR received_at IS NOT NULL) "
            "AND seq NOT IN (SELECT value FROM json_each(?))", (scene, after, encode(exclude_records)),
        ).fetchone()
        return row[0]

    def check_message_records(self, scene: str, records: Sequence[int]) -> None:
        found = {row[0] for row in self.db.execute(
            "SELECT seq FROM messages WHERE scene=? AND seq IN (SELECT value FROM json_each(?))",
            (scene, encode(records)),
        )}
        missing = sorted(set(records) - found)
        if missing:
            raise ValueError(f"当前场景没有这些原话记录：{missing}")

    def search_messages(self, scene: str, *, query: str | None, who: str | None,
                        after: float | None, before: float | None, snapshot: int,
                        offset: int, limit: int) -> list[tuple[int, ChatMessage]]:
        join = ""
        conditions = ["m.scene=?", "m.seq<=?"]
        params: list[object] = [scene, snapshot]
        if query is not None:
            join = " JOIN message_search s ON s.rowid=m.seq"
            normalized = query.casefold()
            if len(normalized) >= 3:
                conditions.append("s.search_text MATCH ?")
                params.append('"' + normalized.replace('"', '""') + '"')
            else:
                conditions.append("instr(s.search_text,?)>0")
                params.append(normalized)
        if who is not None:
            conditions.append("json_extract(m.body,'$.sender.uid')=?")
            params.append(who)
        if after is not None:
            conditions.append("json_extract(m.body,'$.time')>=?")
            params.append(after)
        if before is not None:
            conditions.append("json_extract(m.body,'$.time')<?")
            params.append(before)
        rows = self.db.execute(
            "SELECT m.seq,m.body FROM messages m" + join + " WHERE " + " AND ".join(conditions)
            + " ORDER BY json_extract(m.body,'$.time'),m.seq LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [(row[0], self._message(row[1])) for row in rows]

    def read_message(self, scene: str, record: int) -> ChatMessage | None:
        row = self.db.execute(
            "SELECT body FROM messages WHERE scene=? AND seq=?", (scene, record)
        ).fetchone()
        return None if row is None else self._message(row[0])

    def context_messages(self, scene: str, record: int) -> list[tuple[int, ChatMessage]]:
        center = self.db.execute(
            "SELECT body FROM messages WHERE scene=? AND seq=?", (scene, record)
        ).fetchone()
        if center is None:
            raise ValueError(f"Scene {scene} has no message {record}")
        message = self._message(center[0])
        earlier = self.db.execute(
            "SELECT seq,body FROM messages WHERE scene=? AND "
            "(json_extract(body,'$.time')<? OR "
            "(json_extract(body,'$.time')=? AND seq<?)) "
            "ORDER BY json_extract(body,'$.time') DESC,seq DESC LIMIT 3",
            (scene, message.time, message.time, record),
        ).fetchall()
        later = self.db.execute(
            "SELECT seq,body FROM messages WHERE scene=? AND "
            "(json_extract(body,'$.time')>? OR "
            "(json_extract(body,'$.time')=? AND seq>?)) "
            "ORDER BY json_extract(body,'$.time'),seq LIMIT 3",
            (scene, message.time, message.time, record),
        ).fetchall()
        return ([(row[0], self._message(row[1])) for row in reversed(earlier)]
                + [(record, message)]
                + [(row[0], self._message(row[1])) for row in later])

    def own_ids(self, scene: str) -> set[str]:
        return {row[0] for row in self.db.execute(
            "SELECT platform_id FROM messages WHERE scene=? AND platform_id IS NOT NULL "
            "AND json_extract(body,'$.is_self')=1", (scene,)
        )}

    @staticmethod
    def _schedule(row: sqlite3.Row) -> Schedule:
        return Schedule(**dict(row))

    def create_schedule(self, scene: str, *, due_at: float, timezone: str,
                        note: str, target: str, requester: str | None,
                        limit: int, interval_seconds: int | None = None,
                        cron: str | None = None) -> Schedule:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            unfinished = self.db.execute(
                "SELECT COUNT(*) FROM schedules WHERE scene=? AND status IN ('pending','blocked')",
                (scene,),
            ).fetchone()[0]
            if unfinished >= limit:
                raise ValueError(f"Scene {scene} has reached its unfinished schedule limit {limit}")
            cursor = self.db.execute(
                "INSERT INTO schedules(scene,created,due_at,timezone,note,target,requester,status,"
                "interval_seconds,cron) VALUES (?,?,?,?,?,?,?,'pending',?,?)",
                (scene, self.now(), due_at, timezone, note, target, requester,
                 interval_seconds, cron),
            )
            row = self.db.execute(
                "SELECT * FROM schedules WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        return self._schedule(row)

    def get_schedule(self, scene: str, id: int) -> Schedule:
        row = self.db.execute(
            "SELECT * FROM schedules WHERE scene=? AND id=?", (scene, id)
        ).fetchone()
        if row is None:
            raise ValueError(f"Scene {scene} has no schedule {id}")
        return self._schedule(row)

    def list_schedules(self, scene: str, *, status: str = "active", offset: int = 0,
                       limit: int = 20) -> list[Schedule]:
        if status == "all":
            where, parameters = "", ()
        elif status == "active":
            where, parameters = " AND status IN ('pending','blocked')", ()
        else:
            where, parameters = " AND status=?", (status,)
        rows = self.db.execute(
            "SELECT * FROM schedules WHERE scene=?" + where + " ORDER BY due_at,id LIMIT ? OFFSET ?",
            (scene, *parameters, limit, offset),
        ).fetchall()
        return [self._schedule(row) for row in rows]

    def next_schedule_at(self, scene: str) -> float | None:
        return self.db.execute(
            "SELECT MIN(due_at) FROM schedules WHERE scene=? AND status='pending'", (scene,)
        ).fetchone()[0]

    def due_schedules(self, scene: str, now: float) -> list[Schedule]:
        rows = self.db.execute(
            "SELECT * FROM schedules WHERE scene=? AND status='pending' AND due_at<=? "
            "ORDER BY due_at,id", (scene, now),
        ).fetchall()
        return [self._schedule(row) for row in rows]

    def cancel_schedule(self, scene: str, id: int) -> Schedule:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            current = self.get_schedule(scene, id)
            if current.status not in {"pending", "blocked"}:
                raise ValueError(f"Schedule {id} is {current.status}, not pending or blocked")
            self.db.execute(
                "UPDATE schedules SET status='cancelled' WHERE scene=? AND id=?", (scene, id)
            )
            result = self.get_schedule(scene, id)
        return result

    def block_schedule(self, scene: str, id: int, reason: str) -> None:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            current = self.get_schedule(scene, id)
            if current.status != "pending":
                raise ValueError(f"Schedule {id} is {current.status}, not pending")
            self.db.execute(
                "UPDATE schedules SET status='blocked',reason=? WHERE scene=? AND id=?",
                (reason, scene, id),
            )

    def latest_sender_role(self, scene: str, uid: str) -> str | None:
        row = self.db.execute(
            "SELECT json_extract(body,'$.sender.role') FROM messages "
            "WHERE scene=? AND raw IS NOT NULL "
            "AND json_extract(body,'$.sender.uid')=? ORDER BY seq DESC LIMIT 1",
            (scene, uid),
        ).fetchone()
        return None if row is None else row[0]

    def start_turn(self, scene: str, *, batch: tuple[int, list[str]] | None = None,
                   attention_state: dict | None = None,
                   scheduled: list[tuple[int, str]] | None = None,
                   task_notices: list[tuple[int, str]] | None = None,
                   plugin_events: list[tuple[int, str]] | None = None,
                   wake_received_at: float | None = None,
                   proactive: tuple[str, str, float] | None = None) -> str:
        """Start a turn; ``proactive`` is (wake text, scene-local date, idle since)."""
        turn_id = str(uuid4())
        with self.db:
            self.db.execute(
                "UPDATE turns SET ended=?,status='interrupted',error=COALESCE(error,?) "
                "WHERE scene=? AND ended IS NULL",
                (self.now(), "Previous turn handed off to a resumed turn", scene),
            )
            self.db.execute("INSERT INTO turns(id,scene,started,status,wake_received_at) VALUES (?,?,?,'queued',?)",
                            (turn_id, scene, self.now(), wake_received_at))
            if batch is not None:
                self._append_batch(scene, batch[0], batch[1])
            if scheduled is not None:
                self._append_schedules(scene, scheduled)
            if task_notices is not None:
                self._append_task_notices(scene, task_notices)
            if plugin_events is not None:
                self._append_plugin_events(scene, plugin_events)
            if proactive is not None:
                content, local_date, idle_since = proactive
                self._append(scene, {"role": "user", "content": content})
                self.db.execute(
                    "INSERT INTO proactive_wakes(scene,turn_id,woke_at,local_date,idle_since) VALUES (?,?,?,?,?)",
                    (scene, turn_id, self.now(), local_date, idle_since),
                )
            if attention_state is not None:
                self._save_attention(scene, attention_state)
        return turn_id

    def _append_schedules(self, scene: str, scheduled: list[tuple[int, str]]) -> None:
        for id, content in scheduled:
            item = self.get_schedule(scene, id)
            delivered_at = self.now()
            due_at, status = item.due_at, "delivered"
            reason = None
            if item.interval_seconds is not None:
                steps = max(1, math.floor((delivered_at - due_at) / item.interval_seconds) + 1)
                due_at += steps * item.interval_seconds
                status = "pending"
            elif item.cron is not None:
                try:
                    due_at = next_cron(parse_cron(item.cron), item.timezone, delivered_at)
                    status = "pending"
                except CronTimeError as error:
                    status, reason = "blocked", str(error)
                    content += "\n" + encode({"schedule_status": status, "next_time_error": reason})
            self._append(scene, {"role": "user", "content": content})
            updated = self.db.execute(
                "UPDATE schedules SET status=?,delivered_at=?,due_at=?,reason=? "
                "WHERE scene=? AND id=? AND status='pending'",
                (status, delivered_at, due_at, reason, scene, id),
            )
            if updated.rowcount != 1:
                raise ValueError(f"Scene {scene} schedule {id} is not pending")

    def append_schedules(self, scene: str, scheduled: list[tuple[int, str]], *,
                         turn_id: str) -> None:
        with self.db:
            updated = self.db.execute(
                "UPDATE turns SET status='queued' WHERE id=? AND scene=? AND ended IS NULL",
                (turn_id, scene),
            )
            if updated.rowcount != 1:
                raise ValueError(f"No active turn {turn_id} in scene {scene}")
            self._append_schedules(scene, scheduled)

    def _append_task_notices(self, scene: str, notices: list[tuple[int, str]]) -> None:
        for event_id, content in notices:
            self._append(scene, {"role": "user", "content": content})
            updated = self.db.execute(
                "UPDATE task_events SET delivered_at=? WHERE scene=? AND id=? "
                "AND notice IS NOT NULL AND delivered_at IS NULL",
                (self.now(), scene, event_id),
            )
            if updated.rowcount != 1:
                raise ValueError(f"Scene {scene} task notice {event_id} is not pending")

    def append_task_notices(self, scene: str, notices: list[tuple[int, str]], *,
                            turn_id: str) -> None:
        with self.db:
            updated = self.db.execute(
                "UPDATE turns SET status='queued' WHERE id=? AND scene=? AND ended IS NULL",
                (turn_id, scene),
            )
            if updated.rowcount != 1:
                raise ValueError(f"No active turn {turn_id} in scene {scene}")
            self._append_task_notices(scene, notices)

    def add_plugin_event(self, scene: str, plugin: str, kind: Literal["event", "reply"], content: str) -> int:
        with self.db:
            return self.db.execute(
                "INSERT INTO plugin_events(scene,plugin,kind,content,created) VALUES (?,?,?,?,?)",
                (scene, plugin, kind, content, self.now()),
            ).lastrowid

    def pending_plugin_events(self, scene: str) -> list[tuple[int, str]]:
        return [(row[0], row[1]) for row in self.db.execute(
            "SELECT id,content FROM plugin_events WHERE scene=? AND delivered_at IS NULL ORDER BY id", (scene,))]

    def plugin_wake_pending(self, scene: str) -> bool:
        """Only ``event`` rows wake the mind; ``reply`` rows wait for the next turn."""
        return self.db.execute(
            "SELECT 1 FROM plugin_events WHERE scene=? AND delivered_at IS NULL AND kind='event' LIMIT 1",
            (scene,)).fetchone() is not None

    def _append_plugin_events(self, scene: str, events: list[tuple[int, str]]) -> None:
        for event_id, content in events:
            self._append(scene, {"role": "user", "content": content})
            updated = self.db.execute(
                "UPDATE plugin_events SET delivered_at=? WHERE scene=? AND id=? AND delivered_at IS NULL",
                (self.now(), scene, event_id),
            )
            if updated.rowcount != 1:
                raise ValueError(f"Scene {scene} plugin event {event_id} is not pending")

    def append_plugin_events(self, scene: str, events: list[tuple[int, str]], *, turn_id: str) -> None:
        with self.db:
            updated = self.db.execute(
                "UPDATE turns SET status='queued' WHERE id=? AND scene=? AND ended IS NULL",
                (turn_id, scene),
            )
            if updated.rowcount != 1:
                raise ValueError(f"No active turn {turn_id} in scene {scene}")
            self._append_plugin_events(scene, events)

    def plugin_events(self, scene: str, *, limit: int = 20) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT id,plugin,kind,content,created,delivered_at FROM plugin_events "
            "WHERE scene=? ORDER BY id DESC LIMIT ?", (scene, limit))]

    def start_outgoing(self, message: ChatMessage) -> int:
        """Save a host-originated part (not a mind expression) before sending it."""
        with self.db:
            return self._save_message(message, None)

    def end_turn(self, turn_id: str, status: str, error: str | None = None,
                 *, attention_state: dict | None = None) -> bool:
        with self.db:
            if attention_state is not None:
                scene = self.db.execute("SELECT scene FROM turns WHERE id=?", (turn_id,)).fetchone()[0]
                self._save_attention(scene, attention_state)
            if status in {"timeout", "cancelled"}:
                queued = self.db.execute(
                    "UPDATE turns SET error=? WHERE id=? AND status='queued' AND ended IS NULL",
                    (error, turn_id),
                )
                if queued.rowcount == 1:
                    return True
            self.db.execute("UPDATE turns SET ended=?,status=?,error=? WHERE id=?",
                            (self.now(), status, error, turn_id))
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
                (turn_id, role, self.now(), encode(request)),
            )
        return cursor.lastrowid

    def end_call(self, call_id: int, response: dict | None, usage: dict | None,
                 error: str | None = None, *, cost: dict | None = None, append_to_scene: str | None = None,
                 recap_for: tuple[str, int] | None = None) -> None:
        with self.db:
            entry_seq = (None if append_to_scene is None else
                         self._append(append_to_scene, response["message"]))
            self.db.execute(
                "UPDATE model_calls SET ended=?,response=?,usage=?,error=?,mind_entry_seq=?,cost=? WHERE id=?",
                (self.now(), None if response is None else encode(response),
                 None if usage is None else encode(usage), error, entry_seq,
                 None if cost is None else encode(cost), call_id),
            )
            if append_to_scene is not None:
                if error is None and not response["message"].get("tool_calls"):
                    self.db.execute(
                        "UPDATE turns SET status='settling' WHERE id="
                        "(SELECT turn_id FROM model_calls WHERE id=?) AND ended IS NULL",
                        (call_id,),
                    )
            if recap_for is not None:
                scene, through = recap_for
                self.db.execute(
                    "INSERT INTO mind_sessions(scene,compact_through,recap,discovered_tools) "
                    "VALUES (?,?,?,'[]') "
                    "ON CONFLICT(scene) DO UPDATE SET compact_through=excluded.compact_through, "
                    "recap=excluded.recap,discovered_tools=excluded.discovered_tools",
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
                upload_notice = (" 文件上传结果未确认；用 task status 查看已保存回执，不自动重传。"
                                 if name == "send_file" else "")
                self._append(scene, {"role": "tool", "tool_call_id": call_id,
                                     "content": f"{name} 中断：{reason}。未重放此调用。{upload_notice}"})

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
                (self.now(), "Interrupted: previous process exited without a response", scene),
            )
            self.db.execute(
                "UPDATE turns SET ended=?,status='settled' WHERE scene=? AND status='settling' "
                "AND ended IS NULL",
                (self.now(), scene),
            )
        return needs_resume
