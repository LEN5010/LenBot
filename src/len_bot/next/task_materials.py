"""Preserve explicitly selected scene deliverables as ordinary shared files."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
from pathlib import Path
import stat
import tempfile
from typing import Annotated, BinaryIO, TYPE_CHECKING

from pydantic import AfterValidator, Field, TypeAdapter

if TYPE_CHECKING:
    from .tasks_store import TaskFile


def require_name(value: str) -> str:
    if (not value.strip() or value in {'.', '..'}
            or any(character in value for character in ('/', '\\', '\x00', '\r', '\n'))):
        raise ValueError(f'Material name must be a nonblank filename: {value!r}')
    return value


def unique_names(values: list[str]) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(f'Material selection contains duplicate filenames: {values!r}')
    return values


MaterialName = Annotated[str, Field(min_length=1, max_length=240), AfterValidator(require_name)]
MaterialSelection = Annotated[list[MaterialName], Field(max_length=16), AfterValidator(unique_names)]
MATERIALS = TypeAdapter(MaterialSelection)


async def finish_file_operation[T](operation: Callable[..., T], *args: object, **kwargs: object) -> T:
    pending = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError as cancelled:
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        try:
            pending.result()
        except BaseException as error:
            cancelled.add_note(f'Material filesystem operation also failed: {type(error).__name__}: {error}')
            raise cancelled from error
        raise


def material_directory(workspace_root: Path, scene: str) -> Path:
    return workspace_root / scene / 'shared'


def require_directory(path: Path) -> None:
    if path.resolve(strict=False) != path:
        raise ValueError(f'Shared material directory must not traverse a symbolic link: {path}')


def list_materials(directory: Path) -> dict:
    require_directory(directory)
    try:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {'directory': str(directory), 'exists': False, 'files': []}
    try:
        files = []
        with os.scandir(descriptor) as entries:
            for entry in entries:
                info = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError(f'Shared material must be a regular file: {directory / entry.name}; '
                                     f'mode={oct(info.st_mode)}')
                files.append({'name': entry.name, 'size': info.st_size})
        return {'directory': str(directory), 'exists': True, 'files': sorted(files, key=lambda file: file['name'])}
    finally:
        os.close(descriptor)


def open_regular(path: Path) -> tuple[BinaryIO, int]:
    if path.resolve(strict=True) != path:
        raise ValueError(f'Material source must not traverse a symbolic link: {path}')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    source = os.fdopen(descriptor, 'rb')
    try:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f'Material source must be a regular file: {path}; mode={oct(info.st_mode)}')
        return source, info.st_size
    except BaseException:
        source.close()
        raise


def copy_stream(source: BinaryIO, target: Path, max_bytes: int) -> int:
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        total = 0
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f'File exceeds worker.max_file_bytes: {total} > {max_bytes}')
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    return total


def save_shared(source: BinaryIO, directory: Path, name: str, max_bytes: int) -> dict:
    require_directory(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.resource-copy-', dir=directory.parent) as temporary:
        staged = Path(temporary) / 'content'
        size = copy_stream(source, staged, max_bytes)
        target = directory / name
        os.link(staged, target, follow_symlinks=False)
    return {'directory': str(directory), 'name': name, 'path': str(target), 'size': size}


def adopt_file(directory: Path, source: TaskFile, *, delivery_root: Path, name: str, max_bytes: int) -> dict:
    require_directory(directory)
    origin = Path(source.path)
    expected = delivery_root / source.scene / str(source.task_id)
    if origin.parent != expected:
        raise ValueError(f'Registered delivery is not in its current task directory: {origin}; expected={expected}')
    if source.size > max_bytes:
        raise ValueError(f'Registered file exceeds current worker.max_file_bytes: {source.size} > {max_bytes}')
    target = directory / name
    if os.path.lexists(target):
        raise FileExistsError(f'Shared material already exists; no overwrite: {target}')
    opened, size = open_regular(origin)
    with opened as input_file:
        if size != source.size:
            raise ValueError(f'Registered delivery size changed: {origin}; registered={source.size}, actual={size}')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        require_directory(directory)
        descriptor, temporary_name = tempfile.mkstemp(prefix='.material-copy-', dir=directory.parent)
        temporary = Path(temporary_name)
        original_error: BaseException | None = None
        published = False
        try:
            with os.fdopen(descriptor, 'wb') as output:
                remaining = size
                while remaining:
                    data = input_file.read(min(1024 * 1024, remaining))
                    if not data:
                        raise OSError(f'Registered delivery became shorter during adoption: {origin}')
                    output.write(data)
                    remaining -= len(data)
                if input_file.read(1):
                    raise OSError(f'Registered delivery grew during adoption: {origin}')
                output.flush()
                os.fsync(output.fileno())
            os.link(temporary, target, follow_symlinks=False)
            published = True
            return {'directory': str(directory), 'name': name, 'path': str(target), 'size': size,
                    'source_task_id': source.task_id, 'source_file_id': source.id,
                    'source_path': str(origin), 'source_preserved': True, 'copied': True}
        except BaseException as error:
            original_error = error
            raise
        finally:
            try:
                temporary.unlink()
            except OSError as cleanup_error:
                if original_error is None:
                    cleanup_error.add_note(f'Shared copy published={published}; inspect {target}; source is preserved')
                    raise
                original_error.add_note(f'Material temporary cleanup also failed at {temporary}: {cleanup_error}')
