"""Byte-preserving native role ZIPs; import creates an unbound package, never a live replacement."""

from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile
import zlib

from .persona import PERSONA_FILES, load_persona

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 2048
ROOT_FILES = frozenset((*PERSONA_FILES, 'avatar.png', 'README.md', 'LICENSE', 'NOTICE'))
ROOT_DIRECTORIES = frozenset(('knowledge', 'stickers'))


def package_path(name: str, *, directory: bool) -> str:
    original = name
    if directory and name.endswith('/'):
        name = name[:-1]
    parts = name.split('/')
    if (not name or '\\' in name or any(part in {'', '.', '..'} or part.startswith('.')
            or any(ord(char) < 32 or char == ':' for char in part) for part in parts)):
        raise ValueError(f'Role ZIP needs a canonical relative path, without hidden files: {original!r}')
    if directory:
        allowed = parts[0] in ROOT_DIRECTORIES
    else:
        allowed = ((len(parts) == 1 and name in ROOT_FILES)
                   or (len(parts) > 1 and parts[0] in ROOT_DIRECTORIES))
    if not allowed:
        raise ValueError(f'Unknown native role package path: {original!r}; put the four role files at ZIP root')
    return name


def read_zip(data: bytes) -> dict[str, bytes]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError('Role ZIP upload exceeds 64 MiB')
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                raise ValueError('Role ZIP contains more than 2048 entries')
            total = sum(entry.file_size for entry in entries)
            if total > MAX_EXPANDED_BYTES:
                raise ValueError('Role ZIP expanded contents exceed 128 MiB')
            names: dict[str, bool] = {}
            for entry in entries:
                if entry.filename != entry.orig_filename or entry.flag_bits & 1:
                    raise ValueError(f'Role ZIP has a truncated name or encrypted entry: {entry.orig_filename!r}')
                name = package_path(entry.filename, directory=entry.is_dir())
                kind = stat.S_IFMT(entry.external_attr >> 16)
                allowed_kind = stat.S_IFDIR if entry.is_dir() else stat.S_IFREG
                if kind not in {0, allowed_kind}:
                    raise ValueError(f'Role ZIP entry is a link or special file: {entry.filename!r}')
                if name in names:
                    raise ValueError(f'Role ZIP repeats a file/directory path: {name!r}')
                names[name] = entry.is_dir()
            for name in names:
                for parent in Path(name).parents:
                    if parent.as_posix() in names and not names[parent.as_posix()]:
                        raise ValueError(f'Role ZIP file is also a parent directory: {parent.as_posix()!r}')
            missing = set(PERSONA_FILES) - {name for name, directory in names.items() if not directory}
            if missing:
                raise ValueError(f'Role ZIP lacks required root files: {sorted(missing)!r}')
            files = {entry.filename: archive.read(entry) for entry in entries if not entry.is_dir()}
            if sum(len(content) for content in files.values()) > MAX_EXPANDED_BYTES:
                raise ValueError('Actual expanded role bytes exceed 128 MiB')
            return files
    except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, NotImplementedError) as error:
        raise ValueError(f'Invalid native role ZIP: {error}; raw={data[:64]!r}') from error


def export_package(directory: Path) -> bytes:
    files: dict[str, bytes] = {}
    total = 0
    if directory.is_symlink():
        raise ValueError(f'Role ZIP export does not follow a package symlink: {directory}')
    def listing_error(error: OSError) -> None:
        raise error

    for folder, directories, filenames in os.walk(directory, followlinks=False, onerror=listing_error):
        for name in sorted((*directories, *filenames)):
            path = Path(folder) / name
            if path.is_symlink():
                raise ValueError(f'Role ZIP export does not follow links: {path}')
            kind = path.stat(follow_symlinks=False).st_mode
            is_dir = stat.S_ISDIR(kind)
            if not is_dir and not stat.S_ISREG(kind):
                raise ValueError(f'Role ZIP export found a special file: {path}')
            relative = package_path(path.relative_to(directory).as_posix(), directory=is_dir)
            if not is_dir:
                total += path.stat().st_size
                if total > MAX_EXPANDED_BYTES or len(files) >= MAX_ENTRIES:
                    raise ValueError('Role package exceeds the ZIP expanded-size or entry limit')
                files[relative] = path.read_bytes()
    missing = set(PERSONA_FILES) - files.keys()
    if missing:
        raise ValueError(f'Role package lost required files during export: {sorted(missing)!r}')
    load_persona(directory)
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
    data = stream.getvalue()
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError('Exported role ZIP exceeds the 64 MiB upload/download contract')
    return data


def import_package(root: Path, name: str, data: bytes) -> dict:
    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name) is None:
        raise ValueError(f'New role directory needs 1–64 ASCII letters/digits/_/-, starting with a letter/digit: {name!r}')
    files = read_zip(data)
    collection = root / 'personas'
    if collection.is_symlink():
        raise ValueError(f'Imported role collection must not be a symlink: {collection}')
    collection.mkdir(mode=0o700, exist_ok=True)
    destination = collection / name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f'Role import never overwrites an existing directory: {destination}')
    with tempfile.TemporaryDirectory(prefix='.persona-import-', dir=collection) as temporary:
        package = Path(temporary) / 'package'
        package.mkdir()
        for filename, content in files.items():
            path = package / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(content)
        persona = load_persona(package)
        # The host's shared write lock serializes package creation and role edits.
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f'Role import destination appeared before publish: {destination}')
        package.rename(destination)
    return {'path': str(destination), 'config_path': destination.relative_to(root).as_posix(),
            'persona': {'id': persona.id, 'name': persona.name, 'brief': persona.brief},
            'files': len(files), 'examples': len(persona.examples), 'knowledge': len(persona.knowledge),
            'stickers': len(persona.stickers), 'bound': False,
            'notice': '仅创建独立角色包；未修改场景绑定、根配置或运行人格。请核对后在场景配置中明确采用。'}
