"""Archive actual legacy media rows and local bytes, without adopting their meaning or enabling use."""

from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import sys

from pydantic import ValidationError

from ..config import HostConfig, LabConfig, load_instance_config
from ..instance_lock import instance_lock
from .import_media import LegacyAsset, TAGS, stopped
from ..storage.store import encode


def relative_file(root: PurePosixPath, original_file: str) -> Path:
    source = PurePosixPath(original_file)
    if not source.is_absolute() or '..' in source.parts or not source.is_relative_to(root):
        raise ValueError(f'Legacy media path is outside the declared original directory: {original_file!r}')
    relative = source.relative_to(root)
    if not relative.parts:
        raise ValueError(f'Legacy media file points to its directory: {original_file!r}')
    return Path(*relative.parts)


def copy_original(directory: Path, relative: Path, target: Path) -> int:
    source = directory / relative
    for path in (source, *source.parents):
        if path.is_symlink():
            raise ValueError(f'Media archive does not follow links: {path}')
        if path == directory:
            break
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as input_file:
        info = os.fstat(input_file.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f'Legacy media is not a regular file: {source}')
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(output_descriptor, 'wb') as output:
            remaining = info.st_size
            while remaining:
                data = input_file.read(min(1024 * 1024, remaining))
                if not data:
                    raise OSError(f'Legacy media became shorter during archive: {source}')
                output.write(data)
                remaining -= len(data)
            if input_file.read(1):
                raise OSError(f'Legacy media grew during archive: {source}')
            output.flush()
            os.fsync(output.fileno())
    return info.st_size


def write_json(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
        output.write(encode(value) + '\n')


def archive_media(config: HostConfig | LabConfig) -> dict:
    settings = config.media_archive
    if settings is None:
        raise ValueError('Configure media_archive explicitly in the root lenbot.config.json')
    stopped(settings.source)
    if not settings.directory.is_dir() or settings.directory.is_symlink():
        raise ValueError(f'Media archive requires the declared file snapshot directory: {settings.directory}')
    destination = settings.destination
    if destination.exists():
        raise FileExistsError(f'Media archive never overwrites or resumes a directory: {destination}')
    if (destination.is_relative_to(settings.directory) or settings.directory.is_relative_to(destination)
            or settings.source.is_relative_to(destination) or config.database.is_relative_to(destination)):
        raise ValueError('New media archive must be separate from its file snapshot and the source/current databases')
    scopes = [*settings.scenes, *(['global-safe'] if settings.include_public else [])]
    original_directory = PurePosixPath(settings.original_directory)
    assets: list[tuple[LegacyAsset, Path | None]] = []
    originals: list[dict] = []
    portable: dict[str, Path] = {}
    with closing(sqlite3.connect(settings.source.as_uri() + '?mode=ro&immutable=1', uri=True)) as source:
        source.row_factory = sqlite3.Row
        columns = {row[1] for row in source.execute('PRAGMA table_info(media_assets)')}
        if columns != set(LegacyAsset.model_fields):
            raise ValueError(f'Legacy media_assets differs from the known offline contract: {sorted(columns)!r}')
        for row in source.execute('SELECT * FROM media_assets WHERE scope IN (SELECT value FROM json_each(?)) ORDER BY rowid',
                                  (encode(scopes),)):
            raw = dict(row)
            try:
                asset = LegacyAsset.model_validate(raw)
                TAGS.validate_json(asset.tags_json)
            except ValidationError as error:
                raise ValueError(f'Invalid legacy media row: {error}; raw={repr(raw)[:1000]}') from error
            relative = None if asset.path is None else relative_file(original_directory, asset.path)
            if relative is not None:
                for count in range(1, len(relative.parts) + 1):
                    prefix = Path(*relative.parts[:count])
                    key = prefix.as_posix().casefold()
                    if key in portable and portable[key] != prefix:
                        raise ValueError(f'Original media paths collide on case-insensitive storage: {portable[key]!s}, {prefix!s}')
                    portable[key] = prefix
            assets.append((asset, relative))
            originals.append(raw)
    manifest = {'source': str(settings.source), 'directory': str(settings.directory),
                'original_directory': settings.original_directory, 'scopes': scopes,
                'assets': originals,
                'notice': 'Original asset rows and local files only. Disabled/curated/public flags stay original; '
                          'no new-core registration, model interpretation, transcript, file conversion or platform receipt. '
                          'Unknown MIME stays unknown. Unregistered files and task workspaces are not included. '
                          'Retain the complete original database and file snapshot separately.'}
    result = {'destination': str(destination), 'complete': False, 'copied': [],
              'without_file_path': [asset.id for asset, relative in assets if relative is None], 'error': None,
              'notice': 'Only listed completed copies are confirmed. On interruption inspect preserved partial files; '
                        'no automatic retry, rollback, database write, runtime enable or platform send.'}
    destination.mkdir(parents=True, mode=0o700)
    original_error: BaseException | None = None
    try:
        write_json(destination / 'manifest.json', manifest)
        copied: set[Path] = set()
        for _, relative in assets:
            if relative is None:
                continue
            if relative in copied:
                continue
            target = destination / 'files' / relative
            size = copy_original(settings.directory, relative, target)
            copied.add(relative)
            result['copied'].append({'path': target.relative_to(destination).as_posix(), 'bytes': size})
        result['complete'] = True
    except BaseException as error:
        original_error = error
        result['error'] = f'{type(error).__name__}: {error}'
        error.add_note(f'Media archive is incomplete; preserve and inspect {destination}')
        raise
    finally:
        try:
            write_json(destination / 'result.json', result)
        except Exception as report_error:
            if original_error is None:
                raise
            original_error.add_note(f'Archive result could not be saved: {type(report_error).__name__}: {report_error}')
            raise original_error from report_error
    return result


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit('Legacy media archive takes no overrides; stop the instance and use its root configuration')
    with instance_lock(Path.cwd()):
        print(encode(archive_media(load_instance_config(Path.cwd()))))


if __name__ == '__main__':
    main()
