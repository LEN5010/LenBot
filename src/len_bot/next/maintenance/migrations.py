"""Step-by-step upgrades of LenBot's own SQLite files, done the same way for every database.

Run while the instance is stopped. Integrity is checked before and after. Each step is one
``BEGIN IMMEDIATE`` transaction that also sets ``user_version``, so an interrupted upgrade
stops at a whole format. Work on other files (such as deleting derived summaries) is returned
by the step and runs only after its transaction committed. A standalone run keeps one copy of
the input file beside it, ``<file>.v<format>.bak``; the upgrade command already holds a full
snapshot and skips it.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from ..storage.sqlite import connect

Step = Callable[[sqlite3.Connection], Callable[[], None] | None]


@dataclass(frozen=True)
class Format:
    label: str
    application_id: int
    current: int
    oldest: int
    steps: dict[int, Step]  # format N -> step that produces N + 1


def identity(path: Path) -> tuple[int, int]:
    with closing(connect(path, readonly=True)) as db:
        return _identity(db)


def _identity(db: sqlite3.Connection) -> tuple[int, int]:
    return db.execute('PRAGMA application_id').fetchone()[0], db.execute('PRAGMA user_version').fetchone()[0]


def _check(db: sqlite3.Connection, path: Path, when: str) -> None:
    problems = [row[0] for row in db.execute('PRAGMA integrity_check').fetchall() if row[0] != 'ok']
    if problems:
        raise ValueError(f'{path} 完整性检查（{when}）失败：{problems[:10]!r}')


def _keep_copy(db: sqlite3.Connection, copy: Path) -> None:
    with copy.open('xb'):
        pass
    try:
        with closing(connect(copy)) as target:
            db.backup(target)
    except BaseException:
        copy.unlink()
        raise


def upgrade(path: Path, kind: Format, *, backup: bool = True) -> Path | None:
    """Bring one existing file to the current format; returns the input-format copy if one was kept."""
    with closing(connect(path, create=False)) as db:
        db.isolation_level = None
        application, version = _identity(db)
        if (application, version) == (kind.application_id, kind.current):
            return None
        if application != kind.application_id or not kind.oldest <= version < kind.current:
            raise ValueError(f'{path} 不是可升级的{kind.label}：application_id={application:#x}, '
                             f'格式 {version}，支持 {kind.oldest}–{kind.current - 1} 升到 {kind.current}')
        _check(db, path, '升级前')
        copy = None
        if backup:
            copy = path.with_name(f'{path.name}.v{version}.bak')
            _keep_copy(db, copy)
        for step in range(version, kind.current):
            db.execute('BEGIN IMMEDIATE')
            try:
                if _identity(db) != (kind.application_id, step):
                    raise ValueError(f'{path} 在升级过程中被改动')
                after = kind.steps[step](db)
                db.execute(f'PRAGMA user_version={step + 1}')
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise
            if after is not None:
                after()
        _check(db, path, '升级后')
    return copy
