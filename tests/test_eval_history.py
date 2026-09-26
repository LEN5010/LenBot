"""Offline legacy event schema conversion with content-redacted real row shapes."""

import copy
import json
from pathlib import Path
import re
import sqlite3
import stat

import pytest

from len_bot.eval import extract_history_candidates, report_history_baseline


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


def test_redacted_legacy_backup_remains_unscored_until_human_review(tmp_path: Path) -> None:
    source = tmp_path / "offline-backup.db"
    _backup(source)
    directory = tmp_path / "private-output"
    manifest = extract_history_candidates(source, directory)
    assert manifest["candidate_counts"] == {"coherence": 1, "timing": 2}
    assert manifest["source_first_event"] < manifest["source_last_event"]
    assert manifest["source_scene_count"] == 2
    assert manifest["candidate_scene_count"] == 2
    assert manifest["candidate_strata_counts"]["timing"] == {
        "direct_sent": 1, "direct_no_sent": 0, "ambient_sent": 0, "ambient_no_sent": 1,
    }
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
    report = report_history_baseline(directory / "candidates.jsonl", directory / "annotations.template.jsonl",
                                     directory / "baseline.json")
    assert report["sets"]["coherence"]["pass_rate"] is None
    assert report["sets"]["timing"]["unreviewed"] == 2
    assert report["coverage"]["source_scene_count"] == 2
    assert not report["historical_baseline_complete"]

    annotations = [json.loads(line) for line in (directory / "annotations.template.jsonl").read_text().splitlines()]
    annotations[0].update(verdict="pass", reason="人工核对了前后两次发言。")
    annotations[1].update(verdict="fail", expected_speak=True, reason="人工判定应在观察窗口内回应。")
    label_path = directory / "annotations.jsonl"
    label_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in annotations))
    scored = report_history_baseline(directory / "candidates.jsonl", label_path, directory / "labelled.json")
    assert scored["sets"]["coherence"]["pass"] == 1
    assert scored["sets"]["timing"]["fail"] == 1
    assert scored["sets"]["timing"]["unreviewed"] == 1
    assert stat.S_IMODE((directory / "labelled.json").stat().st_mode) == 0o600

    annotations[2].update(verdict="uncertain", reason="场景当时是否在线未知。")
    label_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in annotations))
    uncertain = report_history_baseline(directory / "candidates.jsonl", label_path, directory / "uncertain.json")
    assert uncertain["sets"]["timing"]["uncertain"] == 1
    assert uncertain["sets"]["timing"]["unreviewed"] == 0


def test_invalid_legacy_payload_exposes_row_fragment(tmp_path: Path) -> None:
    source = tmp_path / "offline-backup.db"
    _backup(source)
    with sqlite3.connect(source) as db:
        payload = json.loads(db.execute("SELECT payload FROM events WHERE id='event-2'").fetchone()[0])
        del payload["reply_bot"]
        db.execute("UPDATE events SET payload=? WHERE id='event-2'", (json.dumps(payload, ensure_ascii=False),))
    with pytest.raises(ValueError, match=r"Invalid event row 2:.*原文已移除"):
        extract_history_candidates(source, tmp_path / "output")


def test_dense_observation_keeps_actual_send_and_ambiguous_name_neutral(tmp_path: Path) -> None:
    source = tmp_path / "offline-backup.db"
    _backup(source)
    with sqlite3.connect(source) as db:
        received = json.loads(db.execute("SELECT payload FROM events WHERE id='event-2'").fetchone()[0])
        for number in range(16):
            payload = copy.deepcopy(received)
            payload["message_id"] = f"dense-{number}"
            payload["at_bot"] = False
            payload["reply_bot"] = False
            payload["reply_to_message_id"] = None
            db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                (f"dense-{number}", "GROUP_MESSAGE_RECEIVED", "group:1000000", "user:1000001",
                 1788695065.1 + number / 10, json.dumps(payload, ensure_ascii=False), "{}"),
            )
        second = json.loads(db.execute("SELECT payload FROM events WHERE id='event-4'").fetchone()[0])
        second["sender"]["nickname"] = "匿名用户2"
        second["raw_text"] = "匿名用户2提到一个话题"
        db.execute("UPDATE events SET payload=? WHERE id='event-4'", (json.dumps(second, ensure_ascii=False),))

    directory = tmp_path / "private-output"
    extract_history_candidates(source, directory)
    candidates = [json.loads(line) for line in (directory / "candidates.jsonl").read_text().splitlines()]
    direct = next(item for item in candidates if item["set"] == "timing" and item["trigger_rowid"] == 2)
    assert direct["context"]["omitted_within_observation"] > 0
    assert all(message["source_rowid"] != 3 for message in direct["context"]["messages"])
    assert [message["source_rowid"] for message in direct["observed_sends"]] == [3]
    assert "[原文已移除]" in direct["observed_sends"][0]["text"]
    assert any("[同名群友]" in message["text"]
               for item in candidates for message in item["context"]["messages"])


def test_weak_coherence_pool_can_meet_fifty_case_floor(tmp_path: Path) -> None:
    source = tmp_path / "offline-backup.db"
    _backup(source)
    sample = json.loads(FIXTURE.read_text(encoding="utf-8"))["events"]
    with sqlite3.connect(source) as db:
        for index in range(55):
            scene = f"group:{3000000 + index}"
            moment = 1788700000 + index * 4000
            for offset, original in enumerate(sample[:3]):
                row = copy.deepcopy(original)
                row["scene_id"] = scene
                row["timestamp"] = moment + offset * 10
                row["payload"]["message_id"] = f"sample-{index}-{offset}"
                if row["event_type"] == "GROUP_MESSAGE_RECEIVED":
                    row["payload"]["at_bot"] = False
                    row["payload"]["reply_bot"] = False
                    row["payload"]["reply_to_message_id"] = None
                db.execute(
                    "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                    (f"sample-{index}-{offset}", row["event_type"], scene, row["actor_id"],
                     row["timestamp"], json.dumps(row["payload"], ensure_ascii=False),
                     json.dumps(row["metadata"], ensure_ascii=False)),
                )

    manifest = extract_history_candidates(source, tmp_path / "private-output")
    assert manifest["candidate_counts"]["coherence"] == 56
    assert manifest["candidate_strata_counts"]["coherence"] == {
        "direct_followup": 1, "nearby_followup": 55,
    }
