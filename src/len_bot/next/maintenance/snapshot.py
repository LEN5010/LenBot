"""Stopped-instance snapshots used by the explicit upgrade and restore commands."""

import json
from pathlib import Path
import shutil
import os


def copy_path(source: Path, target: Path) -> None:
    if source.is_symlink():
        raise ValueError(f'Instance backup path must not be a symbolic link: {source}')
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns('.lenbot-instance.lock'))
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    if os.name == 'posix' and os.geteuid() == 0:
        pairs = [(source, target)]
        if source.is_dir():
            pairs.extend((path, target / path.relative_to(source)) for path in source.rglob('*')
                         if path.name != '.lenbot-instance.lock')
        for original, copied in pairs:
            info = original.lstat()
            os.chown(copied, info.st_uid, info.st_gid, follow_symlinks=False)


def create(paths: list[str], destination: Path) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    entries = []
    for index, name in enumerate(paths):
        source = Path(name)
        if destination.is_relative_to(source):
            raise ValueError(f'Backup must be outside every saved path: {destination}, {source}')
        present = source.exists()
        entries.append({'source': str(source), 'saved': str(index), 'present': present})
        if present:
            copy_path(source, destination / str(index))
    (destination / 'snapshot.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def restore(root: Path, snapshot: Path) -> None:
    entries = json.loads((snapshot / 'snapshot.json').read_text(encoding='utf-8'))
    for entry in entries:
        target = Path(entry['source'])
        if target == root:
            for child in root.iterdir():
                if child.name == '.lenbot-instance.lock':
                    continue
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            for child in (snapshot / entry['saved']).iterdir():
                copy_path(child, root / child.name)
        else:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
            if entry['present']:
                copy_path(snapshot / entry['saved'], target)
