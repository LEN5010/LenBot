"""Token records rebuilt from the usage each call stored, for the offline price-to-token upgrade.

The money estimates are dropped: they depended on configured prices, not on
what the services reported. Usage is parsed with the same parsers the calls
used, so an unparsable historical record stops the upgrade with its row.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

from ..memory.embeddings import _token_usage as embedding_usage
from ..models.asr import USAGE as ASR_USAGE, transcription_tokens
from ..models.client import parse_token_usage
from ..models.tokens import TokenUsage, token_record
from ..storage.store import encode

Kind = Literal["chat", "embedding", "asr"]


def record_from_usage(kind: Kind, usage: dict | None) -> dict | None:
    if usage is None:
        return None
    if kind == "chat":
        return token_record(parse_token_usage(usage))
    if kind == "embedding":
        return token_record(embedding_usage(usage))
    return transcription_tokens(ASR_USAGE.validate_python(usage))


def rename_and_backfill(db: sqlite3.Connection, table: str, kind: Kind | None) -> None:
    """Rename the money column to tokens and fill it from stored usage.

    ``kind=None`` reads the kind from a model_calls row's role.
    """
    db.execute(f"ALTER TABLE {table} RENAME COLUMN cost TO tokens")
    db.execute(f"UPDATE {table} SET tokens=NULL")
    columns = "id,usage" + (",role" if kind is None else "")
    for row in db.execute(f"SELECT {columns} FROM {table} WHERE usage IS NOT NULL").fetchall():
        row_kind = kind if kind is not None else ("asr" if row[2] == "asr" else "chat")
        try:
            tokens = record_from_usage(row_kind, json.loads(row[1]))
        except ValueError as error:
            raise ValueError(f"{table} row {row[0]} usage cannot be read as tokens: {row[1][:500]}") from error
        if tokens is not None:
            db.execute(f"UPDATE {table} SET tokens=? WHERE id=?", (encode(tokens), row[0]))


def upgrade_task_events(db: sqlite3.Connection) -> None:
    """Worker calls kept their parsed token usage next to the money estimate."""
    rows = db.execute("SELECT id,body FROM task_events WHERE kind='model_call'").fetchall()
    for event_id, raw in rows:
        body = json.loads(raw)
        if "response" not in body:
            continue  # Started and never settled.
        response = body["response"]
        response.pop("cost")
        known = response["token_usage"]
        response["tokens"] = None if known is None else token_record(TokenUsage(**known))
        db.execute("UPDATE task_events SET body=? WHERE id=?", (encode(body), event_id))


def upgrade_memory_job_calls(db: sqlite3.Connection) -> None:
    for job_id, raw in db.execute("SELECT id,details FROM memory_jobs").fetchall():
        details = json.loads(raw)
        finished = [call for call in details["calls"] if "ended" in call]  # Unfinished calls have no estimate.
        if not finished:
            continue
        for call in finished:
            call.pop("cost")
            try:
                call["tokens"] = record_from_usage("chat", call["usage"])
            except ValueError as error:
                raise ValueError(f"memory_jobs row {job_id} call usage cannot be read as tokens") from error
        db.execute("UPDATE memory_jobs SET details=? WHERE id=?", (encode(details), job_id))
