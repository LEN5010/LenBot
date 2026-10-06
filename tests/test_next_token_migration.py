"""Offline upgrade from configured-price estimates to reported token records.

Usage bodies follow the shape the local OpenAI-compatible service returned on
2026-10-06 (numbers kept, no content).
"""

import json
from pathlib import Path
import sqlite3

import pytest

from len_bot.next.maintenance.migrate import migrate
from len_bot.next.maintenance.migrate_config import migrate_config
from len_bot.next.maintenance.migrate_memory_jobs import migrate_memory_jobs
from len_bot.next.memory.jobs import FORMAT_VERSION as MEMORY_FORMAT, MemoryJobs
from len_bot.next.storage.store import FORMAT_VERSION, Store

CHAT_USAGE = {"completion_tokens": 524, "total_tokens": 60389, "prompt_tokens": 59865,
              "completion_tokens_details": {"reasoning_tokens": 467}}
WORKER_USAGE = {"completion_tokens": 26, "total_tokens": 6118, "prompt_tokens": 5401,
                "completion_tokens_details": {"reasoning_tokens": 691}}
EMBEDDING_USAGE = {"prompt_tokens": 263, "total_tokens": 263}
OLD_ESTIMATE = {"basis": "configured_estimate", "currency": "USD", "amount": "0.01"}
CALL_TABLES = ("model_calls", "audio_calls", "learning_batches", "expression_embedding_calls",
               "jargon_calls", "sticker_calls", "reply_effect_calls")


def _business_format1(path: Path) -> None:
    with Store(path):
        pass
    with sqlite3.connect(path) as db:
        for table in CALL_TABLES:
            db.execute(f"ALTER TABLE {table} RENAME COLUMN tokens TO cost")
        db.execute("INSERT INTO model_calls(id,turn_id,role,started,ended,request,response,usage,error,cost,scene) "
                   "VALUES(1,NULL,'mind',1.0,2.0,'{}','{}',?,NULL,?,'onebot:group:80001')",
                   (json.dumps(CHAT_USAGE), json.dumps(OLD_ESTIMATE)))
        db.execute("INSERT INTO model_calls(id,turn_id,role,started,ended,request,response,usage,error,cost,scene) "
                   "VALUES(2,NULL,'mind',3.0,4.0,'{}',NULL,NULL,'provider reported nothing',NULL,'onebot:group:80001')")
        db.execute("INSERT INTO tasks(id,scene,requester,goal,deliverable,context,input,status,created) "
                   "VALUES(5,'onebot:group:80001','onebot:70001','g','d','c','i','failed',1.0)")
        response = {"usage": WORKER_USAGE, "cost": None,
                    "token_usage": {"prompt_tokens": 5401, "completion_tokens": 26, "cached_tokens": None}}
        db.execute("INSERT INTO task_events(scene,task_id,kind,body,created) VALUES(?,?,?,?,?)",
                   ('onebot:group:80001', 5, 'model_call', json.dumps({"request": {}, "response": response}), 1.0))
        db.execute("INSERT INTO task_events(scene,task_id,kind,body,created) VALUES(?,?,?,?,?)",
                   ('onebot:group:80001', 5, 'model_call', json.dumps({"request": {}}), 2.0))
        db.execute("PRAGMA user_version=1")


def test_business_upgrade_replaces_estimates_with_reported_tokens(tmp_path):
    path = tmp_path / "state.db"
    _business_format1(path)
    migrate(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == FORMAT_VERSION
        rows = dict(db.execute("SELECT id,tokens FROM model_calls ORDER BY id").fetchall())
        assert json.loads(rows[1]) == {"input": 59865, "output": 524, "cached": None}
        assert rows[2] is None
        bodies = [json.loads(body) for (body,) in db.execute("SELECT body FROM task_events ORDER BY id")]
    assert "cost" not in bodies[0]["response"]
    assert bodies[0]["response"]["tokens"] == {"input": 5401, "output": 26, "cached": None}
    assert bodies[0]["response"]["usage"] == WORKER_USAGE
    assert bodies[1] == {"request": {}}
    with Store(path):
        pass


def test_business_upgrade_stops_on_unreadable_usage(tmp_path):
    path = tmp_path / "state.db"
    _business_format1(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE model_calls SET usage=? WHERE id=1", (json.dumps({"prompt_tokens": -1}),))
    with pytest.raises(ValueError, match="model_calls row 1"):
        migrate(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_memory_upgrade_replaces_estimates_in_jobs_and_embedding_calls(tmp_path):
    path = tmp_path / "state.db.memory.sqlite3"
    with MemoryJobs(path):
        pass
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE memory_summary_runs RENAME COLUMN tokens TO cost")
        db.execute("ALTER TABLE memory_embedding_calls RENAME COLUMN tokens TO cost")
        details = {"calls": [{"started": 1.0, "request": {}, "ended": 2.0, "response": {}, "usage": CHAT_USAGE,
                              "error": None, "cost": None},
                             {"started": 3.0, "request": {}}], "writes": [], "tools": []}
        db.execute("INSERT INTO memory_jobs VALUES(47,'onebot:group:80001','local',1,2,'failed',1.0,2.0,?,NULL)",
                   (json.dumps(details),))
        db.execute("INSERT INTO memory_embedding_calls(scene,purpose,started,ended,request,usage,cost) "
                   "VALUES('onebot:group:80001','query',1.0,2.0,'{}',?,NULL)", (json.dumps(EMBEDDING_USAGE),))
        db.execute("PRAGMA user_version=5")
    migrate_memory_jobs(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == MEMORY_FORMAT
        calls = json.loads(db.execute("SELECT details FROM memory_jobs").fetchone()[0])["calls"]
        embedding = json.loads(db.execute("SELECT tokens FROM memory_embedding_calls").fetchone()[0])
    assert calls[0]["tokens"] == {"input": 59865, "output": 524, "cached": None} and "cost" not in calls[0]
    assert calls[1] == {"started": 3.0, "request": {}}
    assert embedding == {"input": 263, "output": 0, "cached": None}


def test_config_upgrade_drops_prices_and_refuses_money_limits(tmp_path):
    path = tmp_path / "lenbot.config.json"
    source = {"models": {"providers": {}, "roles": {"mind": {"provider": "local", "model": "m"},
                                                     "asr": {"provider": "asr", "model": "a", "price": None},
                                                     "vision": None},
                         "prices": {}},
              "limits": {"currency": "USD", "daily_model_cost": None, "scene_daily_model_cost": {},
                         "messages_per_hour": 60}}
    path.write_text(json.dumps(source))
    assert migrate_config(path) is True
    upgraded = json.loads(path.read_text())
    assert "prices" not in upgraded["models"] and "price" not in upgraded["models"]["roles"]["asr"]
    assert upgraded["limits"] == {"messages_per_hour": 60}
    assert json.loads(path.with_name("lenbot.config.json.pre-tokens.bak").read_text()) == source
    assert migrate_config(path) is False

    money = tmp_path / "money" / "lenbot.config.json"
    money.parent.mkdir()
    source["limits"]["daily_model_cost"] = "1"
    money.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="daily_model_cost"):
        migrate_config(money)
    assert json.loads(money.read_text()) == source
