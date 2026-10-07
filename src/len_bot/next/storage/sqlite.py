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


def connect(path: Path, *, readonly: bool = False, immutable: bool = False, create: bool = True) -> sqlite3.Connection:
    """Read-write connections set the journal and durability; read-only ones only read.

    ``immutable`` reads a file nothing else writes (a stopped instance or a snapshot) without
    taking locks; ``create=False`` refuses to make a new empty file, for upgrades of existing data.
    """
    if readonly or immutable:
        query = '?mode=ro&immutable=1' if immutable else '?mode=ro'
        return sqlite3.connect(path.resolve().as_uri() + query, uri=True, timeout=BUSY_TIMEOUT_SECONDS)
    if create:
        db = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
    else:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=BUSY_TIMEOUT_SECONDS)
    try:
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA foreign_keys=ON')
    except BaseException:
        db.close()
        raise
    return db
