"""Explicit offline upgrades from the public business format baseline."""

from collections.abc import Callable
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

from ..config import load_instance_config
from ..instance_lock import instance_lock
from ..storage.store import FORMAT_VERSION


APPLICATION_ID = 0x4C424E31
UPGRADES: dict[int, Callable[[sqlite3.Connection], None]] = {}


def migrate(path: Path) -> None:
    with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)) as db:
        actual = (db.execute('PRAGMA application_id').fetchone()[0],
                  db.execute('PRAGMA user_version').fetchone()[0])
        if actual[0] != APPLICATION_ID or not 1 <= actual[1] <= FORMAT_VERSION:
            raise ValueError(f'Unsupported business database format: {actual!r}; path={path}')
        for version in range(actual[1], FORMAT_VERSION):
            with db:
                db.execute('BEGIN EXCLUSIVE')
                UPGRADES[version](db)
                db.execute(f'PRAGMA user_version={version + 1}')
    print(f'{path}: business format {FORMAT_VERSION}')


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit('Business migration takes no arguments; use the instance root configuration')
    with instance_lock(Path.cwd()):
        migrate(load_instance_config(Path.cwd()).database)


if __name__ == '__main__':
    main()
