"""Explicit offline upgrades from the public business format baseline."""

from collections.abc import Callable
from contextlib import ExitStack, closing
from pathlib import Path
import sqlite3
import sys

from ..config import load_instance_config
from ..instance_lock import instance_lock
from ..storage.store import FORMAT_VERSION
from .token_backfill import rename_and_backfill, upgrade_task_events


APPLICATION_ID = 0x4C424E31


def _tokens_instead_of_prices(db: sqlite3.Connection) -> None:
    """Format 1 to 2: call records keep reported tokens instead of money estimates."""
    for table, kind in (('model_calls', None), ('audio_calls', 'asr'), ('learning_batches', 'chat'),
                        ('expression_embedding_calls', 'embedding'), ('jargon_calls', 'chat'),
                        ('sticker_calls', 'chat'), ('reply_effect_calls', 'chat')):
        rename_and_backfill(db, table, kind)
    upgrade_task_events(db)


UPGRADES: dict[int, Callable[[sqlite3.Connection], None]] = {1: _tokens_instead_of_prices}


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
    root = Path.cwd()
    with ExitStack() as locks:
        locks.enter_context(instance_lock(root))
        trials = sorted((root / '.runtime' / 'chat-tests').glob('*/state.db'))
        for path in trials:
            if path.is_symlink():
                raise ValueError(f'Trial database must not be a symbolic link: {path}')
            locks.enter_context(instance_lock(path.parent))
        for path in (load_instance_config(root).database, *trials):
            migrate(path)


if __name__ == '__main__':
    main()
