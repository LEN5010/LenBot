"""Explicit offline upgrades from the public business format baseline."""

from contextlib import ExitStack
from pathlib import Path
import sqlite3
import sys

from ..config import load_instance_config
from ..instance_lock import instance_lock
from ..storage.store import FORMAT_VERSION
from .migrations import Format, upgrade
from .token_backfill import rename_and_backfill, upgrade_task_events
from ..runtime.logs import run_maintenance


APPLICATION_ID = 0x4C424E31


def _tokens_instead_of_prices(db: sqlite3.Connection) -> None:
    """Format 1 to 2: call records keep reported tokens instead of money estimates."""
    for table, kind in (('model_calls', None), ('audio_calls', 'asr'), ('learning_batches', 'chat'),
                        ('expression_embedding_calls', 'embedding'), ('jargon_calls', 'chat'),
                        ('sticker_calls', 'chat'), ('reply_effect_calls', 'chat')):
        rename_and_backfill(db, table, kind)
    upgrade_task_events(db)


def _qualified_schedule_targets(db: sqlite3.Connection) -> None:
    """Format 2 to 3: remind an account in the schedule's actual platform."""
    db.execute("UPDATE schedules SET target=substr(scene,1,instr(scene,':'))||target "
               "WHERE target!='self' AND instr(target,':')=0")


BUSINESS = Format('业务数据库', APPLICATION_ID, FORMAT_VERSION, 1, {
    1: _tokens_instead_of_prices,
    2: _qualified_schedule_targets,
})


def migrate(path: Path, *, backup: bool = True) -> Path | None:
    copy = upgrade(path, BUSINESS, backup=backup)
    print(f'{path}: business format {FORMAT_VERSION}' + ('' if copy is None else f'; input-format copy: {copy}'))
    return copy


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
    run_maintenance(main, 'migrate')
