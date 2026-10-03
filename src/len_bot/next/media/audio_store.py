"""Actual voice-message processing, cached bytes and off-turn ASR calls."""
from __future__ import annotations

import json
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING

from ..storage.codec import encode

if TYPE_CHECKING:
    from ..storage.store import Store


SCHEMA = """
CREATE TABLE audio_cache (
    scene TEXT NOT NULL, platform_id TEXT NOT NULL, audio_index INTEGER NOT NULL,
    wav BLOB, duration REAL, fetched_at REAL,
    transcript TEXT, provider TEXT, model TEXT, transcribed_at REAL,
    status TEXT NOT NULL CHECK(status IN ('idle','queued','running','complete','failed','interrupted')),
    created REAL NOT NULL, updated REAL NOT NULL, error TEXT, announced_at REAL,
    PRIMARY KEY(scene, platform_id, audio_index)
);
CREATE INDEX audio_queued ON audio_cache(scene,created) WHERE status='queued';
CREATE INDEX audio_results ON audio_cache(scene,transcribed_at)
    WHERE announced_at IS NULL AND transcript IS NOT NULL;
CREATE TABLE audio_calls (
    id INTEGER PRIMARY KEY, scene TEXT NOT NULL, platform_id TEXT NOT NULL,
    audio_index INTEGER NOT NULL, started REAL NOT NULL, ended REAL,
    request TEXT NOT NULL, response TEXT, usage TEXT, error TEXT, cost TEXT
);
CREATE INDEX audio_calls_source ON audio_calls(scene,platform_id,audio_index,id);
CREATE INDEX audio_calls_usage ON audio_calls(started,scene);
"""


class AudioStore:
    def __init__(self, store: Store):
        self.store, self.db = store, store.db

    def recover(self, scene: str, enabled: bool) -> None:
        with self.db:
            error = "Interrupted: previous audio processing ended without a result; not replayed"
            self.db.execute("UPDATE audio_cache SET status='interrupted',error=?,updated=? "
                "WHERE scene=? AND status='running'", (error, self.store.now(), scene))
            if not enabled:
                self.db.execute("UPDATE audio_cache SET status='interrupted',error=?,updated=? "
                    "WHERE scene=? AND status='queued'",
                    ("Automatic transcription is disabled; queued audio was not processed", self.store.now(), scene))
            self.db.execute("UPDATE audio_calls SET ended=?,error=? WHERE scene=? AND ended IS NULL",
                            (self.store.now(), error, scene))

    def item(self, scene: str, message: str, index: int):
        return self.db.execute("SELECT * FROM audio_cache WHERE scene=? AND platform_id=? AND audio_index=?",
                               (scene, message, index)).fetchone()

    def pending(self, scene: str):
        return self.db.execute("SELECT platform_id,audio_index FROM audio_cache "
                               "WHERE scene=? AND status='queued' ORDER BY created,audio_index LIMIT 1", (scene,)).fetchone()

    def waiting_since(self, scene: str) -> float | None:
        return self.db.execute("SELECT MIN(a.created) FROM audio_cache a JOIN messages m ON m.scene=a.scene "
            "AND m.platform_id=a.platform_id WHERE a.scene=? AND a.status IN ('queued','running') "
            "AND m.seq>COALESCE((SELECT last_message_seq FROM mind_sessions WHERE scene=?),0) LIMIT 1",
            (scene, scene)).fetchone()[0]

    def results_pending(self, scene: str) -> bool:
        return self.db.execute("SELECT 1 FROM audio_cache a JOIN messages m ON m.scene=a.scene AND m.platform_id=a.platform_id "
            "WHERE a.scene=? AND a.announced_at IS NULL AND a.transcript IS NOT NULL "
            "AND m.seq<=COALESCE((SELECT last_message_seq FROM mind_sessions WHERE scene=?),0) LIMIT 1",
            (scene, scene)).fetchone() is not None

    def captions(self, scene: str, message: str) -> dict[int, str]:
        captions = {}
        for row in self.db.execute("SELECT audio_index,transcript,status,error FROM audio_cache "
                                   "WHERE scene=? AND platform_id=?", (scene, message)):
            if row[1] is not None:
                captions[row[0]] = "ASR：" + (row[1] or "未识别出文字")
            elif row[2] in {"failed", "interrupted"}:
                captions[row[0]] = "转写未完成：" + row[3][:200]
            else:
                captions[row[0]] = "等待转写" if row[2] != "running" else "正在转写"
        return captions

    def append_late(self, scene: str) -> None:
        with self.db:
            rows = self.db.execute("SELECT a.platform_id,a.audio_index,a.transcript,a.transcribed_at FROM audio_cache a "
                "JOIN messages m ON m.scene=a.scene AND m.platform_id=a.platform_id "
                "WHERE a.scene=? AND a.transcript IS NOT NULL AND a.announced_at IS NULL "
                "AND m.seq<=COALESCE((SELECT last_message_seq FROM mind_sessions WHERE scene=?),0) "
                "ORDER BY a.transcribed_at,a.audio_index", (scene, scene)).fetchall()
            for row in rows:
                prompt = Template((Path(__file__).resolve().parents[2] / "prompts" / "next_audio_ready.md").read_text())
                self.store._append(scene, {"role": "user", "content":
                    prompt.substitute(message=row[0], audio=row[1], text=row[2]).strip()})
                self.db.execute("UPDATE audio_cache SET announced_at=? WHERE scene=? AND platform_id=? AND audio_index=?",
                                (self.store.now(), scene, row[0], row[1]))

    def items(self, scene: str, limit: int, offset: int) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT platform_id,audio_index,duration,fetched_at,transcript,provider,model,transcribed_at,"
            "status,created,updated,error,length(wav) AS wav_bytes FROM audio_cache "
            "WHERE scene=? ORDER BY created DESC,platform_id,audio_index LIMIT ? OFFSET ?", (scene, limit, offset))]

    def calls(self, scene: str, message: str, index: int) -> dict:
        def decoded(rows):
            items = []
            for row in rows:
                item = dict(row)
                for field in ("request", "response", "usage", "cost"):
                    item[field] = None if item[field] is None else json.loads(item[field])
                items.append(item)
            return items
        return {
            "off_turn": decoded(self.db.execute("SELECT id,started,ended,request,response,usage,cost,error FROM audio_calls "
                "WHERE scene=? AND platform_id=? AND audio_index=? ORDER BY id DESC LIMIT 20", (scene, message, index))),
            "in_turn": decoded(self.db.execute("SELECT c.id,c.turn_id,c.started,c.ended,c.request,c.response,c.usage,c.cost,c.error "
                "FROM model_calls c JOIN turns t ON t.id=c.turn_id WHERE t.scene=? AND c.role='asr' "
                "AND json_extract(c.request,'$.file.platform_message_id')=? "
                "AND json_extract(c.request,'$.file.audio')=? ORDER BY c.id DESC LIMIT 20", (scene, message, index))),
        }

    def start_call(self, scene: str, message: str, index: int, request: dict) -> int:
        with self.db:
            return self.db.execute("INSERT INTO audio_calls(scene,platform_id,audio_index,started,request) VALUES (?,?,?,?,?)",
                                   (scene, message, index, self.store.now(), encode(request))).lastrowid

    def end_call(self, call: int, response, usage, error: str | None = None, *, cost: dict | None = None) -> None:
        with self.db:
            self.db.execute("UPDATE audio_calls SET ended=?,response=?,usage=?,error=?,cost=? WHERE id=?",
                (self.store.now(), None if response is None else encode(response),
                 None if usage is None else encode(usage), error, None if cost is None else encode(cost), call))
