"""Scene history and actual model exchanges in a separate SQLite database."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from itertools import islice
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal
from uuid import uuid4

from ..platform.messages import Notice, ChatMessage, plain_text
from ..persona.stickers import PersonaSticker
from ..learning.sticker_assets import CollectedSticker
from ..image_assets import OriginalImage
from ..models.tokens import token_summary
from ..chat.schedule_time import CronTimeError, next_cron, parse_cron
from .codec import decode_message, encode
from .schema import create_database
from ..chat.schedule_store import ScheduleStore


FORMAT_VERSION = 2


def turn_record(row: sqlite3.Row) -> dict:
    turn = dict(row)
    first, wake = turn["first_expression_at"], turn["wake_received_at"]
    turn["turn_to_first_expression_seconds"] = None if first is None else first - turn["started"]
    turn["wake_to_first_expression_seconds"] = None if first is None or wake is None else first - wake
    return turn


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
                        "run python -m len_bot.next.maintenance.migrate from the isolated instance directory"
                    )
                if application_id != 0x4C424E31 or version != FORMAT_VERSION:
                    raise ValueError(f"Not a supported next-core database: {path}")
            else:
                create_database(self.db, FORMAT_VERSION)
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

    def new_context(self, scene: str) -> int:
        """Keep original history and unread input; only move the active mind boundary."""
        with self.db:
            through = self.db.execute("SELECT COALESCE(MAX(seq),0) FROM mind_entries WHERE scene=?",
                                      (scene,)).fetchone()[0]
            self.db.execute(
                "INSERT INTO mind_sessions(scene,compact_through,recap,discovered_tools) VALUES (?,?,NULL,'[]') "
                "ON CONFLICT(scene) DO UPDATE SET compact_through=excluded.compact_through,"
                "recap=NULL,discovered_tools='[]'", (scene, through))
        return through

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

    def _call_tokens(self, scenes: Sequence[str], since: float, until: float, *, calls=None) -> list:
        from ..models.usage import call_records
        if calls is None:
            return call_records(self, list(scenes), since, until)
        return [call for call in calls if since <= call.started < until]

    def hourly_overview(self, scenes: Sequence[str], since: float, hours: int, *, calls=None) -> dict:
        """Per-hour counts from ``since``: received and spoken messages, reply turns, reported tokens."""
        placeholders = ",".join("?" for _ in scenes)
        until = since + hours * 3600

        def series(query: str) -> list[int]:
            values = [0] * hours
            for hour, count in self.db.execute(query, (since, *scenes, since, until)):
                values[hour] = count
            return values

        received = series(
            "SELECT CAST((received_at-?)/3600 AS INTEGER),COUNT(*) FROM messages "
            f"WHERE scene IN ({placeholders}) AND raw IS NOT NULL AND received_at>=? AND received_at<? GROUP BY 1")
        spoke = series(
            "SELECT CAST((json_extract(body,'$.time')-?)/3600 AS INTEGER),COUNT(*) FROM messages "
            f"WHERE scene IN ({placeholders}) AND raw IS NULL AND json_extract(body,'$.send_status') IN ('sent','simulated') "
            "AND json_extract(body,'$.time')>=? AND json_extract(body,'$.time')<? GROUP BY 1")
        turns = series(
            "SELECT CAST((started-?)/3600 AS INTEGER),COUNT(*) FROM turns "
            f"WHERE scene IN ({placeholders}) AND started>=? AND started<? GROUP BY 1")
        tokens = [0] * hours
        from ..models.usage import UNBUDGETED_ROLES
        for call in self._call_tokens(scenes, since, until, calls=calls):
            if call.tokens is not None and call.role not in UNBUDGETED_ROLES:
                tokens[int((call.started - since) // 3600)] += call.tokens["input"] + call.tokens["output"]
        return {"since": since, "hours": hours, "received": received, "spoke": spoke, "turns": turns, "tokens": tokens}

    def recent_activity(self, scenes: Sequence[str], limit: int) -> list[dict]:
        """Latest reply turns, finished tasks and failed sends, newest first."""
        placeholders = ",".join("?" for _ in scenes)
        items = [{"kind": "turn", "scene": row[0], "at": row[1], "id": row[2], "status": row[3]} for row in self.db.execute(
            f"SELECT scene,started,id,status FROM turns WHERE scene IN ({placeholders}) ORDER BY started DESC LIMIT ?",
            (*scenes, limit))]
        items += [{"kind": "task", "scene": row[0], "at": row[1], "id": row[2], "status": row[3], "goal": row[4]}
                  for row in self.db.execute(
                      f"SELECT scene,ended,id,status,goal FROM tasks WHERE scene IN ({placeholders}) AND ended IS NOT NULL "
                      "ORDER BY ended DESC LIMIT ?", (*scenes, limit))]
        items += [{"kind": "send", "scene": row[0], "at": row[1], "id": row[2], "status": row[3]} for row in self.db.execute(
            "SELECT scene,json_extract(body,'$.time'),seq,json_extract(body,'$.send_status') FROM messages "
            f"WHERE scene IN ({placeholders}) AND raw IS NULL AND json_extract(body,'$.send_status') IN ('failed','unconfirmed') "
            "ORDER BY json_extract(body,'$.time') DESC LIMIT ?", (*scenes, limit))]
        return sorted(items, key=lambda item: item["at"], reverse=True)[:limit]

    def failed_tasks(self, scenes: Sequence[str], since: float) -> dict[str, int]:
        placeholders = ",".join("?" for _ in scenes)
        return dict(self.db.execute(
            f"SELECT scene,COUNT(*) FROM tasks WHERE scene IN ({placeholders}) AND status='failed' AND ended>=? GROUP BY scene",
            (*scenes, since)))

    def daily_overview(self, scenes: Sequence[str], since: float, until: float, *, calls=None) -> dict:
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
        calls = self._call_tokens(scenes, since, until, calls=calls)
        reactions = dict(self.db.execute(
            "SELECT COALESCE(reaction,CASE WHEN observed_seqs='[]' THEN 'no_messages' ELSE 'waiting' END),"
            f"COUNT(*) FROM reply_effects WHERE scene IN ({placeholders}) AND closed_at IS NOT NULL "
            "AND first_sent_at>=? AND first_sent_at<? GROUP BY 1", (*scenes, since, until),
        ))
        from ..models.usage import UNBUDGETED_ROLES
        token_records = token_summary([call.tokens for call in calls if call.role not in UNBUDGETED_ROLES])
        unfinished = sum(call.ended is None for call in calls)
        pending = self.db.execute(
            f"SELECT COUNT(*) FROM schedules WHERE scene IN ({placeholders}) AND status='pending'", scenes,
        ).fetchone()[0]
        errors = [dict(row) for row in self.db.execute(
            f"SELECT id,scene,started,status,error FROM turns WHERE scene IN ({placeholders}) "
            "AND error IS NOT NULL ORDER BY started DESC LIMIT 10", scenes,
        )]
        undelivered = dict(self.db.execute(
            "SELECT scene,COUNT(*) FROM messages "
            f"WHERE scene IN ({placeholders}) AND raw IS NULL "
            "AND json_extract(body,'$.send_status') IN ('failed','unconfirmed') "
            "AND json_extract(body,'$.time')>=? AND json_extract(body,'$.time')<? GROUP BY scene",
            (*scenes, since, until),
        ))
        reviews = {}
        for kind, query in (
            ("expressions", "SELECT scene,COUNT(*) FROM expressions WHERE status='pending'"),
            ("stickers", "SELECT scene,COUNT(*) FROM sticker_candidates "
                         "WHERE review='pending' AND status='complete'"),
        ):
            for scene, count in self.db.execute(
                f"{query} AND scene IN ({placeholders}) GROUP BY scene", scenes,
            ):
                reviews.setdefault(scene, {})[kind] = count
        return {"messages": messages, "turns": turns, "model_calls": len(calls),
                "unfinished_calls": unfinished,
                "tokens": {key: token_records[key] for key in ("input", "output", "cached")},
                "unknown_token_calls": token_records["unknown_calls"],
                "pending_schedules": pending, "recent_errors": errors,
                "reply_effects": reactions, "undelivered": undelivered, "pending_reviews": reviews}

    def _append(self, scene: str, message: dict) -> int:
        cursor = self.db.execute(
            "INSERT INTO mind_entries(scene,message,created) VALUES (?,?,?)",
            (scene, encode(message), self.now()),
        )
        return cursor.lastrowid

    def append(self, scene: str, message: dict) -> None:
        with self.db:
            self._append(scene, message)

    def save_notice(self, notice: Notice) -> None:
        platform_id = notice.platform_message_id
        with self.db:
            self.db.execute(
                "INSERT INTO notices(scene,kind,platform_id,time,received_at,raw,body) VALUES (?,?,?,?,?,?,?)",
                (notice.scene, notice.notice_type, platform_id, notice.time, self.now(), encode(dict(notice.raw)),
                 encode({"user_id": notice.user_id, "operator_id": notice.operator_id,
                         "duration": notice.duration, "sub_type": notice.sub_type})),
            )
            if platform_id is not None:
                self.db.execute("UPDATE messages SET body=json_set(body,'$.recalled',json('true')) "
                                "WHERE scene=? AND platform_id=?", (notice.scene, platform_id))

    def bot_muted_until(self, scene: str, bot_id: str) -> float | None:
        row = self.db.execute(
            "SELECT time,body FROM notices WHERE scene=? AND kind='group_ban' "
            "AND json_extract(body,'$.user_id')=? ORDER BY time DESC,id DESC LIMIT 1",
            (scene, bot_id),
        ).fetchone()
        if row is None:
            return None
        notice = json.loads(row["body"])
        until = row["time"] + notice["duration"]
        return until if notice["sub_type"] == "ban" and until > self.now() else None

    def notice_page(self, scene: str, *, before: int | None = None, limit: int = 50) -> dict:
        rows = self.db.execute(
            "SELECT * FROM notices WHERE scene=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?",
            (scene, before, before, limit + 1),
        ).fetchall()
        return {"items": [{**dict(row), "raw": json.loads(row["raw"])} for row in rows[:limit]],
                "next_before": rows[limit-1]["id"] if len(rows) > limit else None}

    def _save_message(self, message: ChatMessage, raw: dict | None,
                      received_at: float | None = None, *, persona_id: str | None = None) -> int:
        if message.platform_message_id is not None and self.db.execute(
            "SELECT 1 FROM notices WHERE scene=? AND platform_id=? "
            "AND kind IN ('group_recall','friend_recall') LIMIT 1",
            (message.scene, message.platform_message_id),
        ).fetchone() is not None:
            message = replace(message, recalled=True)
        cursor = self.db.execute(
            "INSERT INTO messages(scene,platform_id,body,raw,received_at,persona_id) VALUES (?,?,?,?,?,?)",
            (message.scene, message.platform_message_id, encode(asdict(message)),
             None if raw is None else encode(raw), received_at, persona_id),
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
                collect_stickers: bool = False, transcribe_audio: bool = False,
                plugin_claim: tuple[str, str] | None = None) -> int:
        """Store one received platform message without adding it to the mind yet."""
        with self.db:
            seq = self._save_message(message, raw, received_at)
            if plugin_claim is not None:
                plugin, content = plugin_claim
                self.db.execute(
                    "INSERT INTO plugin_events(scene,plugin,kind,content,created) VALUES (?,?,'reply',?,?)",
                    (message.scene, plugin, content, received_at),
                )
            if transcribe_audio:
                for index, _ in enumerate((part for part in message.segments if part.type == "record"), 1):
                    self.db.execute("INSERT INTO audio_cache(scene,platform_id,audio_index,status,created,updated) "
                                    "VALUES (?,?,?,'queued',?,?)",
                                    (message.scene, message.platform_message_id, index, received_at, received_at))
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
        return [(row[0], decode_message(row[1]), row[2]) for row in rows]

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
        return [(decode_message(body), received_at) for body, received_at in reversed(rows)]

    def last_pending_arrival(self, scene: str, exclude_uids: Sequence[str]) -> float | None:
        """Latest unbatched arrival eligible for merging into the next input."""
        excluded = tuple(exclude_uids)
        exclude_clause = (
            " AND (json_extract(body,'$.mentions_bot')=1 OR scene LIKE 'onebot:private:%' "
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
        self.db.execute("UPDATE audio_cache SET announced_at=? WHERE scene=? AND transcript IS NOT NULL "
            "AND platform_id IN (SELECT platform_id FROM messages WHERE scene=? AND seq<=? "
            "AND seq>COALESCE((SELECT last_message_seq FROM mind_sessions WHERE scene=?),0))",
            (self.now(), scene, scene, through, scene))
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

    def prepare_limit_notice(self, scene: str, state: dict, content: str) -> int:
        with self.db:
            self._save_attention(scene, state)
            return self._append(scene, {"role": "user", "content": content})

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
                              *, persona_id: str, turn_id: str | None = None,
                              sticker: tuple[str, PersonaSticker] | CollectedSticker | None = None) -> int:
        """Only attempted parts become chat messages; the remainder stays in the result."""
        with self.db:
            message_seq = self._save_message(expression, None, persona_id=persona_id)
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
            if expression.platform_message_id is not None and self.db.execute(
                "SELECT 1 FROM notices WHERE scene=? AND platform_id=? "
                "AND kind IN ('group_recall','friend_recall') LIMIT 1",
                (expression.scene, expression.platform_message_id),
            ).fetchone() is not None:
                expression = replace(expression, recalled=True)
            echo = (None if expression.platform_message_id is None else self.db.execute(
                "SELECT seq,body FROM messages WHERE scene=? AND platform_id=?",
                (expression.scene, expression.platform_message_id),
            ).fetchone())
            if echo is not None:
                received = decode_message(echo[1])
                if (not received.is_self or received.sender.uid != expression.sender.uid
                        or echo[0] <= message_seq or received.send_status != "received"):
                    raise ValueError(f"Send receipt conflicts with an existing message: {expression.platform_message_id}")
                self.db.execute("UPDATE messages SET body=?,persona_id=(SELECT persona_id FROM messages WHERE seq=?) WHERE seq=?",
                                (encode(asdict(replace(received, send_status="sent"))), message_seq, echo[0]))
                self.db.execute("UPDATE message_media SET message_seq=? WHERE message_seq=?",
                                (echo[0], message_seq))
                self.db.execute("UPDATE media SET source_message_seq=? WHERE source_message_seq=?",
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
            saved = decode_message(row[1])
            if not message.is_self or message.sender.uid != saved.sender.uid:
                raise ValueError(f"Own message echo conflicts with another sender: {message.platform_message_id}")
            if row[2] is not None:
                return
            self.db.execute("UPDATE messages SET body=?,raw=?,received_at=? WHERE seq=?",
                            (encode(asdict(replace(message, id=saved.id, send_status="sent", recalled=saved.recalled))),
                             encode(raw), received_at, row[0]))
            self.db.execute("UPDATE message_search SET search_text=? WHERE rowid=?",
                            (plain_text(message).casefold(), row[0]))

    def recent(self, scene: str, limit: int = 20) -> list[ChatMessage]:
        rows = self.db.execute(
            "SELECT body FROM messages WHERE scene=? ORDER BY seq DESC LIMIT ?", (scene, limit)
        ).fetchall()
        return [decode_message(row[0]) for row in reversed(rows)]

    def recent_records(self, scene: str, limit: int = 50, *, snapshot: int | None = None,
                       offset: int = 0) -> list[tuple[int, ChatMessage]]:
        conditions = "scene=?" if snapshot is None else "scene=? AND seq<=?"
        values = (scene,) if snapshot is None else (scene, snapshot)
        rows = self.db.execute(
            f"SELECT seq,body FROM messages WHERE {conditions} ORDER BY seq DESC LIMIT ? OFFSET ?",
            (*values, limit, offset),
        ).fetchall()
        return [(row[0], decode_message(row[1])) for row in reversed(rows)]

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
            for key in ("request", "response", "usage", "tokens"):
                call[key] = None if call[key] is None else json.loads(call[key])
            entry_seq = call.pop("mind_entry_seq")
            call["tool_results"] = None if entry_seq is None else []
            call["snapshot_expired_at"] = call["request"].get("snapshot_expired_at")
            native = None if entry_seq is None else self.db.execute(
                "SELECT message FROM mind_entries WHERE scene=? AND seq=?", (scene, entry_seq)).fetchone()
            if call["role"] == "mind" and call["response"] is not None and call["error"] is None:
                tool_calls = call["response"]["message"].get("tool_calls", [])
            else:
                tool_calls = [] if native is None else json.loads(native[0]).get("tool_calls", [])
            call["native_tool_calls"] = tool_calls
            if tool_calls and entry_seq is not None:
                for entry in self.db.execute(
                    "SELECT message FROM mind_entries WHERE scene=? AND seq>? ORDER BY seq", (scene, entry_seq),
                ):
                    message = json.loads(entry[0])
                    if message["role"] == "assistant":
                        break
                    if message["role"] == "tool":
                        call["tool_results"].append(message)
                        if len(call["tool_results"]) == len(tool_calls):
                            break
            calls.append(call)
        return {"turn": turn_record(turn), "calls": calls}

    def recent_context_messages(self, scene: str, limit: int = 20) -> list[ChatMessage]:
        """Only inbound messages already batched for the mind, plus saved outbound."""
        return list(reversed(list(islice(self.iter_recent_context(scene), limit))))

    def iter_recent_context(self, scene: str, *, sender: str | None = None) -> Iterator[ChatMessage]:
        """Newest consumed/outbound first, for token-bounded readers without a row cap."""
        rows = self.db.execute(
            "SELECT body FROM messages WHERE scene=? AND (raw IS NULL "
            "OR (json_extract(body,'$.is_self')=1 AND json_extract(body,'$.send_status')='sent') OR seq<="
            "COALESCE((SELECT last_message_seq FROM mind_sessions WHERE scene=?),0)) "
            + ("AND json_extract(body,'$.sender.uid')=? " if sender is not None else "")
            + "ORDER BY seq DESC",
            (scene, scene) if sender is None else (scene, scene, sender),
        )
        for row in rows:
            yield decode_message(row[0])

    def ordered_messages(self, scene: str, ids: Sequence[str]) -> list[ChatMessage]:
        """Keep original reception order, including equal or out-of-order platform clocks."""
        rows = self.db.execute(
            "SELECT body FROM messages WHERE scene=? AND json_extract(body,'$.id') IN "
            "(SELECT value FROM json_each(?)) ORDER BY seq", (scene, encode(list(ids))),
        )
        return [decode_message(row[0]) for row in rows]

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
            message = decode_message(body)
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


    def find_message(self, scene: str, platform_id: str) -> ChatMessage | None:
        row = self.db.execute(
            "SELECT body FROM messages WHERE scene=? AND platform_id=?", (scene, platform_id)
        ).fetchone()
        return None if row is None else decode_message(row[0])

    def messages_after(self, scene: str, platform_id: str) -> int:
        """Count actual visible messages after a known reply target in this scene."""
        return self.db.execute(
            "SELECT COUNT(*) FROM messages WHERE scene=? AND seq>("
            "SELECT seq FROM messages WHERE scene=? AND platform_id=?) "
            "AND json_extract(body,'$.send_status') IN ('received','sent','simulated')",
            (scene, scene, platform_id),
        ).fetchone()[0]

    def max_message_seq(self, scene: str) -> int:
        return self.db.execute(
            "SELECT COALESCE(MAX(seq),0) FROM messages WHERE scene=?", (scene,)
        ).fetchone()[0]

    def memory_messages(self, scene: str, after: int, *, limit: int,
                        through: int | None = None,
                        exclude_records: Sequence[int] = ()) -> list[tuple[int, ChatMessage, float, str | None]]:
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
            "SELECT seq,body,CASE WHEN raw IS NULL THEN json_extract(body,'$.time') ELSE received_at END,persona_id "
            "FROM messages WHERE " + " AND ".join(conditions) + " ORDER BY seq LIMIT ?", [*values, limit],
        ).fetchall()
        return [(row[0], decode_message(row[1]), row[2], row[3]) for row in rows]

    def message_persona_ids(self, scene: str) -> set[str]:
        """Loaded role identities actually recorded for this scene, including unsent expressions."""
        return {row[0] for row in self.db.execute(
            "SELECT DISTINCT persona_id FROM messages WHERE scene=? AND persona_id IS NOT NULL "
            "AND json_extract(body,'$.is_self')=1", (scene,))}

    def message_persona_scenes(self) -> set[str]:
        return {row[0] for row in self.db.execute(
            "SELECT DISTINCT scene FROM messages WHERE persona_id IS NOT NULL AND json_extract(body,'$.is_self')=1")}

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
        return [(row[0], decode_message(row[1])) for row in rows]

    def read_message(self, scene: str, record: int) -> ChatMessage | None:
        row = self.db.execute(
            "SELECT body FROM messages WHERE scene=? AND seq=?", (scene, record)
        ).fetchone()
        return None if row is None else decode_message(row[0])

    def context_messages(self, scene: str, record: int) -> list[tuple[int, ChatMessage]]:
        center = self.db.execute(
            "SELECT body FROM messages WHERE scene=? AND seq=?", (scene, record)
        ).fetchone()
        if center is None:
            raise ValueError(f"Scene {scene} has no message {record}")
        message = decode_message(center[0])
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
        return ([(row[0], decode_message(row[1])) for row in reversed(earlier)]
                + [(record, message)]
                + [(row[0], decode_message(row[1])) for row in later])

    def own_ids(self, scene: str) -> set[str]:
        return {row[0] for row in self.db.execute(
            "SELECT platform_id FROM messages WHERE scene=? AND platform_id IS NOT NULL "
            "AND json_extract(body,'$.is_self')=1", (scene,)
        )}


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
                    "INSERT INTO proactive_wakes(scene,turn_id,woke_at,local_date,idle_since,assessment) "
                    "VALUES (?,?,?,?,?,'reply_effects')",
                    (scene, turn_id, self.now(), local_date, idle_since),
                )
            if attention_state is not None:
                self._save_attention(scene, attention_state)
        return turn_id

    def _append_schedules(self, scene: str, scheduled: list[tuple[int, str]]) -> None:
        for id, content in scheduled:
            item = ScheduleStore(self).get_schedule(scene, id)
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


    def start_outgoing(self, message: ChatMessage, *, persona_id: str, image: tuple[OriginalImage, str] | None = None) -> int:
        """Save a host-originated part (not a mind expression) before sending it."""
        with self.db:
            seq = self._save_message(message, None, persona_id=persona_id)
            if image is not None:
                original, description = image
                media_id = self.db.execute(
                    "INSERT INTO media(source_message_seq,source_image_index,mime_type,width,height,animated,data) "
                    "VALUES (?,1,?,?,?,?,?)", (seq, original.mime_type, original.width, original.height,
                                              int(original.animated), original.data)).lastrowid
                self.db.execute("INSERT INTO message_media(message_seq,image_index,media_id,description,emotions,tags) "
                                "VALUES (?,1,?,?,'[]','[]')", (seq, media_id, description))
            return seq

    def end_turn(self, turn_id: str, status: str, error: str | None = None,
                 *, attention_state: dict | None = None) -> bool:
        with self.db:
            if attention_state is not None:
                scene = self.db.execute("SELECT scene FROM turns WHERE id=?", (turn_id,)).fetchone()[0]
                self._save_attention(scene, attention_state)
            if status == "limited":
                self.db.execute("UPDATE turns SET status='queued',error=? WHERE id=? AND ended IS NULL",
                                (error, turn_id))
                return True
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
                "UPDATE turns SET status='running' WHERE id=? AND ended IS NULL RETURNING scene", (turn_id,)
            ).fetchone()
            if updated is None:
                raise ValueError(f"No active turn {turn_id}")
            cursor = self.db.execute(
                "INSERT INTO model_calls(turn_id,role,started,request,scene) VALUES (?,?,?,?,?)",
                (turn_id, role, self.now(), encode(request), updated[0]),
            )
        return cursor.lastrowid


    def end_call(self, call_id: int, response: dict | None, usage: dict | None,
                 error: str | None = None, *, tokens: dict | None = None, append_to_scene: str | None = None,
                 recap_for: tuple[str, int] | None = None) -> None:
        with self.db:
            entry_seq = (None if append_to_scene is None else
                         self._append(append_to_scene, response["message"]))
            self.db.execute(
                "UPDATE model_calls SET ended=?,response=?,usage=?,error=?,mind_entry_seq=?,tokens=? WHERE id=?",
                (self.now(), None if response is None else encode(response),
                 None if usage is None else encode(usage), error, entry_seq,
                 None if tokens is None else encode(tokens), call_id),
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

    def last_mind_usage(self, scene: str) -> tuple[dict, dict | None] | None:
        """Reuse the last completed request; a failed latest call isn't a usage anchor."""
        row = self.db.execute(
            "SELECT request,usage,error,ended FROM model_calls WHERE scene=? AND role='mind' "
            "AND plugin IS NULL ORDER BY id DESC LIMIT 1", (scene,),
        ).fetchone()
        if row is None or row['ended'] is None or row['error'] is not None:
            return None
        return json.loads(row['request']), None if row['usage'] is None else json.loads(row['usage'])

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
