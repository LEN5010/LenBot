"""Explicit stopped-instance upgrade of the memory processing database to the current format."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import sqlite3
import sys

from ..config import load_instance_config
from ..instance_lock import instance_lock
from ..memory.jobs import FORMAT_VERSION
from .migrations import Format, upgrade
from .token_backfill import rename_and_backfill, upgrade_memory_job_calls
from ..runtime.logs import run_maintenance


APPLICATION_ID = 0x4C424D4A


def _exclusions(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE memory_exclusions ("
               "scene TEXT NOT NULL, message_seq INTEGER NOT NULL,"
               "PRIMARY KEY(scene,message_seq))")


def _summary_runs(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE memory_summary_runs ("
               "id INTEGER PRIMARY KEY, scene TEXT NOT NULL, scope TEXT NOT NULL, path TEXT NOT NULL,"
               "started REAL NOT NULL, ended REAL,"
               "status TEXT NOT NULL CHECK(status IN ('running','complete','failed','interrupted')),"
               "request TEXT NOT NULL, response TEXT, usage TEXT, cost TEXT, error TEXT)")
    db.execute("CREATE INDEX memory_summary_runs_path ON memory_summary_runs(scope,path,id)")


def _embedding_calls(db: sqlite3.Connection) -> None:
    db.execute("ALTER TABLE memory_summary_runs ADD COLUMN model_started REAL")
    db.execute("CREATE TABLE memory_embedding_calls ("
               "id INTEGER PRIMARY KEY,scene TEXT NOT NULL,purpose TEXT NOT NULL,"
               "started REAL NOT NULL,ended REAL,request TEXT NOT NULL,response TEXT,usage TEXT,cost TEXT,error TEXT)")
    db.execute("CREATE INDEX memory_embedding_usage ON memory_embedding_calls(started,scene)")


def _personas(db: sqlite3.Connection) -> None:
    db.execute('CREATE TABLE memory_personas (scene TEXT NOT NULL,persona_id TEXT NOT NULL,PRIMARY KEY(scene,persona_id))')


def _tokens(db: sqlite3.Connection) -> None:
    rename_and_backfill(db, 'memory_summary_runs', 'chat')
    rename_and_backfill(db, 'memory_embedding_calls', 'embedding')
    upgrade_memory_job_calls(db)


MEMORY_JOBS = Format('记忆处理库', APPLICATION_ID, FORMAT_VERSION, 1, {
    1: _exclusions, 2: _summary_runs, 3: _embedding_calls, 4: _personas, 5: _tokens,
})


def migrate_memory_jobs(path: Path, *, backup: bool = True) -> Path | None:
    """Upgrade one stopped database; returns the copy of the input format when one was kept."""
    return upgrade(Path(path).resolve(), MEMORY_JOBS, backup=backup)


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("Memory processing migration takes no arguments; stop the instance and run from its root")
    root = Path.cwd()
    with ExitStack() as locks:
        locks.enter_context(instance_lock(root))
        config = load_instance_config(root)
        trials = sorted((root / '.runtime' / 'chat-tests').glob('*/state.db.memory.sqlite3'))
        for path in trials:
            if path.is_symlink():
                raise ValueError(f'Trial processing database must not be a symbolic link: {path}')
            locks.enter_context(instance_lock(path.parent))
        paths = [config.database.with_name(config.database.name + '.memory.sqlite3'), *trials]
        for path in paths:
            if not path.exists():
                continue
            backup = migrate_memory_jobs(path)
            print(f"{path}: memory processing format {FORMAT_VERSION}"
                  + ("" if backup is None else f"; input-format copy: {backup}"))


if __name__ == '__main__':
    run_maintenance(main, 'migrate_memory_jobs')
