"""Stopped-instance snapshots used by the explicit upgrade and restore commands."""

import hashlib
import json
from pathlib import Path
import shutil
import os

LOCK = '.lenbot-instance.lock'


def copy_path(source: Path, target: Path) -> None:
    if source.is_symlink():
        raise ValueError(f'Instance backup path must not be a symbolic link: {source}')
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns(LOCK))
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    if os.name == 'posix' and os.geteuid() == 0:
        pairs = [(source, target)]
        if source.is_dir():
            pairs.extend((path, target / path.relative_to(source)) for path in source.rglob('*')
                         if path.name != LOCK)
        for original, copied in pairs:
            info = original.lstat()
            os.chown(copied, info.st_uid, info.st_gid, follow_symlinks=False)


def _item(path: Path) -> dict:
    if path.is_symlink():
        return {'link': os.readlink(path)}
    if path.is_dir():
        return {'dir': True}
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1 << 20):
            digest.update(chunk)
    return {'size': path.stat().st_size, 'sha256': digest.hexdigest()}


def manifest(path: Path) -> dict[str, dict]:
    """Every saved file, link and directory with its length and SHA-256, keyed by relative path."""
    if path.is_symlink() or not path.is_dir():
        return {'': _item(path)}
    return {str(item.relative_to(path)): _item(item) for item in sorted(path.rglob('*'))
            if LOCK not in item.relative_to(path).parts}


def verify(snapshot: Path) -> list[dict]:
    """Check the whole snapshot against its manifest before anything is removed."""
    entries = json.loads((snapshot / 'snapshot.json').read_text(encoding='utf-8'))
    for entry in entries:
        if not entry['present']:
            continue
        if 'files' not in entry:
            raise ValueError(f'Snapshot {snapshot} has no content manifest for {entry["source"]}; it cannot be verified before restore')
        saved = snapshot / entry['saved']
        if not saved.exists() and not saved.is_symlink():
            raise ValueError(f'Snapshot copy of {entry["source"]} is missing: {saved}')
        actual = manifest(saved)
        if actual != entry['files']:
            changed = sorted(name for name in entry['files'].keys() | actual.keys()
                             if entry['files'].get(name) != actual.get(name))
            raise ValueError(f'Snapshot copy of {entry["source"]} does not match its manifest: {changed[:10]!r}'
                             + (f' and {len(changed) - 10} more' if len(changed) > 10 else ''))
    return entries


def create(paths: list[str], destination: Path) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    entries = []
    for index, name in enumerate(paths):
        source = Path(name)
        if destination.is_relative_to(source):
            raise ValueError(f'Backup must be outside every saved path: {destination}, {source}')
        present = source.exists()
        entry = {'source': str(source), 'saved': str(index), 'present': present}
        if present:
            copy_path(source, destination / str(index))
            entry['files'] = manifest(destination / str(index))
        entries.append(entry)
    (destination / 'snapshot.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def restore(root: Path, snapshot: Path) -> None:
    entries = verify(snapshot)
    for entry in entries:
        target = Path(entry['source'])
        if target == root:
            for child in root.iterdir():
                if child.name == LOCK:
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
