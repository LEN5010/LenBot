"""The one connection convention for LenBot's own SQLite files.

Rollback journal with synchronous=FULL: the business database keeps send intents and
consumer cursors, so a committed write must survive power loss, and stopped-instance
snapshots copy single database files without WAL side files. Store calls run on the
event loop, so a lock wait is bounded instead of blocking it for long.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

BUSY_TIMEOUT_SECONDS = 5.0


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Read-write connections set the journal and durability; read-only ones only read."""
    if readonly:
        return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=BUSY_TIMEOUT_SECONDS)
    db = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
    try:
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA foreign_keys=ON')
    except BaseException:
        db.close()
        raise
    return db
