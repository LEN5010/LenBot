"""Offline legacy event schema conversion with content-redacted real row shapes."""

import json
from pathlib import Path
import re
import sqlite3
import stat

import pytest

from len_bot.eval import extract_history_candidates


FIXTURE = Path(__file__).parent / "fixtures" / "eval" / "history_sample.json"


def _backup(path: Path) -> None:
    sample = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE events (id TEXT PRIMARY KEY,event_type TEXT NOT NULL,scene_id TEXT NOT NULL,"
            "actor_id TEXT NOT NULL,timestamp REAL NOT NULL,payload TEXT NOT NULL,metadata TEXT NOT NULL)"
        )
        for index, item in enumerate(sample["events"], 1):
            db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                (f"event-{index}", item["event_type"], item["scene_id"], item["actor_id"],
                 item["timestamp"], json.dumps(item["payload"], ensure_ascii=False),
                 json.dumps(item["metadata"], ensure_ascii=False)),
            )


def test_legacy_conversion_redacts_identity_and_preserves_delivery_facts(tmp_path: Path) -> None:
    source = tmp_path / "offline-backup.db"
    _backup(source)
    directory = tmp_path / "private-output"
    extract_history_candidates(source, directory)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for name in ("candidates.jsonl", "annotations.template.jsonl", "manifest.json"):
        assert stat.S_IMODE((directory / name).stat().st_mode) == 0o600
    raw = (directory / "candidates.jsonl").read_text(encoding="utf-8")
    assert "1000001" not in raw and "2000000" not in raw
    assert not re.search(r"\[CQ:|(?<!\d)\d{7,}(?!\d)", raw)
    candidates = [json.loads(line) for line in raw.splitlines()]
    assert any(item["observed"] == "no_confirmed_send_within_5m" for item in candidates if item["set"] == "timing")
    assert any(item["observed_sends"][0]["source_rowid"] == 3
               for item in candidates if item["set"] == "timing" and item["observed_sends"])


def test_invalid_legacy_payload_exposes_row_fragment(tmp_path: Path) -> None:
    source = tmp_path / "offline-backup.db"
    _backup(source)
    with sqlite3.connect(source) as db:
        payload = json.loads(db.execute("SELECT payload FROM events WHERE id='event-2'").fetchone()[0])
        del payload["reply_bot"]
        db.execute("UPDATE events SET payload=? WHERE id='event-2'", (json.dumps(payload, ensure_ascii=False),))
    with pytest.raises(ValueError, match=r"Invalid event row 2:.*原文已移除"):
        extract_history_candidates(source, tmp_path / "output")
