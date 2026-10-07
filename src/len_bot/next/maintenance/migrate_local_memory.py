"""Offline local index upgrade; discard summaries whose generation clock is unknown."""

from contextlib import ExitStack, closing
from pathlib import Path
import sqlite3
import sys

from ..config import load_instance_config
from ..instance_lock import instance_lock
from ..memory.local import FORMAT_VERSION, SUMMARY_FILES, SUMMARY_SCHEMA, _APPLICATION_ID, _INDEX_NAME
from ..memory.service import LocalMemoryConfig
from ..runtime.logs import run_maintenance


def migrate(directory: Path) -> None:
    index = directory / _INDEX_NAME
    if not index.exists():
        print(f'{directory}: no local memory index to migrate')
        return
    with closing(sqlite3.connect(index.as_uri() + '?mode=rw', uri=True)) as db:
        actual = (db.execute('PRAGMA application_id').fetchone()[0],
                  db.execute('PRAGMA user_version').fetchone()[0])
        if actual == (_APPLICATION_ID, FORMAT_VERSION):
            print(f'{index}: local memory format {FORMAT_VERSION}')
            return
        if actual != (_APPLICATION_ID, 2):
            raise ValueError(f'Unsupported local memory index format: {actual!r}; path={index}')
        with db:
            db.execute('BEGIN EXCLUSIVE')
            db.execute(SUMMARY_SCHEMA)
            for category in ('public', 'scenes'):
                for name in SUMMARY_FILES:
                    for summary in (directory / category).rglob(name):
                        summary.unlink()
            db.execute(f'PRAGMA user_version={FORMAT_VERSION}')
    print(f'{index}: local memory format {FORMAT_VERSION}; old derived summaries cleared; content and history retained')


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit('Local memory migration takes no arguments; stop the instance and run from its root')
    root = Path.cwd()
    with ExitStack() as locks:
        locks.enter_context(instance_lock(root))
        trials = sorted((root / '.runtime' / 'chat-tests').glob('*/lenbot.config.json'))
        for path in trials:
            locks.enter_context(instance_lock(path.parent))
        directories = set()
        for instance in (root, *(path.parent for path in trials)):
            config = load_instance_config(instance)
            if isinstance(config.memory, LocalMemoryConfig):
                directories.add(config.memory.local.directory)
        for directory in sorted(directories):
            migrate(directory)


if __name__ == '__main__':
    run_maintenance(main, 'migrate_local_memory')
