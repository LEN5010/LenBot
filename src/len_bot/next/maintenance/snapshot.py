"""Stopped-instance snapshots used by the explicit upgrade and restore commands."""

import hashlib
import json
from pathlib import Path
import shutil
import os

LOCK = '.lenbot-instance.lock'
STAGING = '.lenbot-restoring'
# Room left after the copy for migrations' journals and the new version's first writes.
HEADROOM = 256 * 1024 * 1024


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


def _size(path: Path) -> int:
    if path.is_symlink() or not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.lstat().st_size for item in path.rglob('*') if item.is_file() and not item.is_symlink())


def check_space(paths: list[str], destination: Path) -> None:
    """Stop before copying anything when the snapshot and some working room do not fit."""
    needed = sum(_size(Path(name)) for name in paths) + HEADROOM
    existing = next(parent for parent in (destination, *destination.parents) if parent.exists())
    free = shutil.disk_usage(existing).free
    if free < needed:
        raise OSError(f'快照需要约 {needed / 2**30:.2f} GiB（含 {HEADROOM // 2**20} MiB 余量），'
                      f'{existing} 所在磁盘只剩 {free / 2**30:.2f} GiB；清理空间后再升级')


def create(paths: list[str], destination: Path) -> None:
    check_space(paths, destination)
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


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def restore(root: Path, snapshot: Path) -> None:
    """Verify, copy everything beside its target, then swap item by item.

    A full disk or unreadable snapshot stops during the copy, before the instance changes;
    the swap afterwards only removes and renames within the same directories.
    """
    entries = verify(snapshot)
    staged: list[tuple[dict, Path]] = []
    try:
        for entry in entries:
            target = Path(entry['source'])
            staging = root / STAGING if target == root else target.with_name(f'.{target.name}{STAGING}')
            _remove(staging)
            staged.append((entry, staging))
            if target == root:
                staging.mkdir()
                for child in (snapshot / entry['saved']).iterdir():
                    copy_path(child, staging / child.name)
            elif entry['present']:
                copy_path(snapshot / entry['saved'], staging)
    except BaseException:
        for _, staging in staged:
            _remove(staging)
        raise
    for entry, staging in staged:
        target = Path(entry['source'])
        if target == root:
            for child in root.iterdir():
                if child.name not in (LOCK, STAGING):
                    _remove(child)
            for child in staging.iterdir():
                child.rename(root / child.name)
            staging.rmdir()
        else:
            _remove(target)
            if entry['present']:
                staging.rename(target)
