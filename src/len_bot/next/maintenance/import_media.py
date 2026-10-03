"""Transfer stopped legacy image files by their actual archived references, never by URL guesses."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import sys

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ..config import HostConfig, LabConfig, load_instance_config
from ..instance_lock import instance_lock
from ..image_assets import MAX_IMAGE_BYTES, inspect_image
from .import_history import EVENT_COLUMNS, _backup, _event, _reject_constant
from ..platform.messages import ChatMessage
from ..storage.store import Store, encode

STRICT = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class LegacyAsset(BaseModel):
    model_config = STRICT
    id: str = Field(min_length=1)
    scope: str
    source_event_id: str
    locator: str
    mime_type: str | None
    path: str | None
    description: str
    tags_json: str
    enabled: int = Field(ge=0, le=1)
    curated: int = Field(ge=0, le=1)
    created_at: float
    palette_order: int | None


class LegacyReference(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore')
    asset_id: str = Field(min_length=1)
    type: str
    source_event_id: str


REFERENCES = TypeAdapter(list[LegacyReference])
TAGS = TypeAdapter(list[str])
MESSAGES = TypeAdapter(ChatMessage)


def stopped(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f'Media transfer requires an existing regular stopped SQLite file: {path}')
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError(f'Media transfer requires a complete checkpointed snapshot; nonempty {sidecar}')


def copied_file(directory: Path, original_directory: str, original_file: str) -> bytes:
    source = PurePosixPath(original_file)
    root = PurePosixPath(original_directory)
    if not source.is_absolute() or '..' in source.parts or not source.is_relative_to(root):
        raise ValueError(f'Legacy image file is outside its explicitly configured original directory: {original_file!r}')
    relative = source.relative_to(root)
    target = directory.joinpath(*relative.parts)
    for path in (target, *target.parents):
        if path.is_symlink():
            raise ValueError(f'Image import does not follow file/directory links: {path}')
        if path == directory:
            break
    if not target.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f'Image import path escapes the declared file snapshot: {target}')
    if not stat.S_ISREG(target.stat(follow_symlinks=False).st_mode):
        raise ValueError(f'Archived image is not a regular file: {target}')
    with target.open('rb') as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    return data


def import_media(config: HostConfig | LabConfig) -> dict:
    settings = config.media_import
    if settings is None:
        raise ValueError('Configure media_import explicitly in the root lenbot.config.json')
    source, target, backup = settings.source, config.database, settings.backup
    stopped(source)
    stopped(target)
    if source == target or backup in {source, target} or source.samefile(target):
        raise ValueError('Media source, target and backup must be distinct files')
    if backup.exists():
        raise FileExistsError(f'Media target backup already exists: {backup}')
    if not settings.directory.is_dir() or settings.directory.is_symlink():
        raise ValueError(f'Media directory must be an explicit regular snapshot directory: {settings.directory}')
    if any(path.is_relative_to(settings.directory) for path in (target, backup)):
        raise ValueError('Target database and backup must not be inside the read-only media source directory')
    report = {'source': str(source), 'target': str(target), 'backup': str(backup),
              'imported': [], 'already_present': [], 'not_transferred': [],
              'notice': 'Images only. No network, model call, role adoption, platform send or runtime switch. '
                        'Audio/video/task files, disabled/global media and old backend ownership stay for explicit handoff.'}
    referenced: set[str] = set()
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro&immutable=1', uri=True)) as old:
        old.row_factory = sqlite3.Row
        columns = {row[1] for row in old.execute('PRAGMA table_info(media_assets)')}
        if columns != set(LegacyAsset.model_fields):
            raise ValueError(f'Legacy media_assets does not match its explicit offline contract: columns={sorted(columns)!r}')
        with Store(target) as store:
            _backup(store, backup)
            with store.db:
                store.db.execute('BEGIN EXCLUSIVE')
                for scene in settings.scenes:
                    rows = store.db.execute('SELECT seq,body,raw FROM messages WHERE scene=? ORDER BY seq', (scene,)).fetchall()
                    originals = 0
                    for row in rows:
                        message = MESSAGES.validate_json(row['body'], strict=True)
                        packet = None if row['raw'] is None else json.loads(row['raw'], parse_constant=_reject_constant)
                        if packet is None or 'legacy_events' not in packet:
                            continue
                        originals += 1
                        pictures = [segment for segment in message.segments if segment.type == 'image']
                        if message.send_status not in {'received', 'sent'}:
                            report['not_transferred'].append({'scene': scene, 'record': row['seq'],
                                                             'reason': f'message status {message.send_status}; not confirmed platform content'})
                            continue
                        provenance = list(packet['legacy_media']) if 'legacy_media' in packet else []
                        for original in packet['legacy_events']:
                            event = original['event']
                            if event['scene_id'] != scene:
                                raise ValueError(f'Original media event belongs to another scene: {event["id"]!r}')
                            actual = old.execute(f"SELECT rowid,{','.join(EVENT_COLUMNS)} FROM events WHERE id=?", (event['id'],)).fetchone()
                            if actual is None or actual['rowid'] != original['rowid'] or _event(actual) != event:
                                raise ValueError(f'Imported original event differs from source snapshot: {event["id"]!r}')
                            references: list[tuple[int, str, str | None]] = []
                            if event['event_type'] in {'GROUP_MESSAGE_RECEIVED', 'PRIVATE_MESSAGE_RECEIVED'}:
                                if 'media' not in event['metadata']:
                                    continue
                                try:
                                    incoming = REFERENCES.validate_python(event['metadata']['media'])
                                except ValidationError as error:
                                    raise ValueError(f'Invalid legacy media references: {error}; raw={repr(event["metadata"]["media"])[:1000]}') from error
                                if any(item.type != 'image' for item in incoming):
                                    report['not_transferred'].append({'scene': scene, 'event_id': event['id'],
                                                                     'reason': 'non-image reference array; no positional guess'})
                                    continue
                                references = [(index, item.asset_id, item.source_event_id) for index, item in enumerate(incoming, 1)]
                            elif 'segments' in event['payload'] and event['payload']['segments'] is not None:
                                references = [(index, segment['asset_id'], None) for index, segment in enumerate(
                                    (item for item in event['payload']['segments'] if item['type'] == 'image'), 1)]
                            if references and len(references) != len(pictures):
                                report['not_transferred'].append({'scene': scene, 'event_id': event['id'],
                                                                 'reason': 'actual image segments and original references differ; no reconstruction'})
                                continue
                            for index, asset_id, origin in references:
                                original_asset = old.execute('SELECT * FROM media_assets WHERE id=?', (asset_id,)).fetchone()
                                if original_asset is None:
                                    raise ValueError(f'Legacy media reference has no actual asset: {asset_id!r}')
                                raw_asset = dict(original_asset)
                                try:
                                    asset = LegacyAsset.model_validate(raw_asset)
                                    TAGS.validate_json(asset.tags_json)
                                except ValidationError as error:
                                    raise ValueError(f'Invalid legacy image asset: {error}; raw={repr(raw_asset)[:1000]}') from error
                                referenced.add(asset.id)
                                if origin is not None and (origin != event['id'] or asset.source_event_id != origin):
                                    raise ValueError(f'Image reference and original source event differ: {asset.id!r}')
                                reason = ('other/public source scope' if asset.scope != scene else
                                          'original media disabled; no automatic enable' if not asset.enabled else
                                          'original file never downloaded' if asset.path is None else None)
                                if reason is not None:
                                    report['not_transferred'].append({'asset_id': asset.id, 'record': row['seq'], 'reason': reason})
                                    continue
                                data = copied_file(settings.directory, settings.original_directory, asset.path)
                                mime, width, height, animated = inspect_image(data)
                                if asset.mime_type != mime:
                                    raise ValueError(f'Legacy image MIME differs from actual bytes: {asset.id!r}, saved={asset.mime_type!r}, actual={mime!r}')
                                current = store.original_image(scene, row['seq'], index)
                                existing_source = [item for item in provenance if item['asset']['id'] == asset.id]
                                record = {'asset': raw_asset, 'image_index': index}
                                if existing_source and existing_source != [record]:
                                    raise ValueError(f'Legacy source asset changed after import: {asset.id!r}')
                                if current is not None:
                                    if current != (mime, data):
                                        raise ValueError(f'Original images conflict at {scene} record {row["seq"]} image {index}')
                                    report['already_present'].append({'asset_id': asset.id, 'record': row['seq'], 'image': index})
                                else:
                                    media = store.db.execute('INSERT INTO media(source_message_seq,source_image_index,mime_type,width,height,animated,data) '
                                                             'VALUES(?,?,?,?,?,?,?)', (row['seq'], index, mime, width, height, int(animated), data)).lastrowid
                                    store.db.execute("INSERT INTO message_media(message_seq,image_index,media_id,description,emotions,tags) VALUES(?,?,?,'','[]','[]')",
                                                     (row['seq'], index, media))
                                    report['imported'].append({'asset_id': asset.id, 'record': row['seq'], 'image': index,
                                                               'bytes': len(data), 'mime_type': mime, 'platform_id': message.platform_message_id})
                                if not existing_source:
                                    provenance.append(record)
                        if provenance:
                            packet['legacy_media'] = provenance
                            store.db.execute('UPDATE messages SET raw=? WHERE seq=?', (encode(packet), row['seq']))
                    if not originals:
                        raise ValueError(f'Selected scene has no original imported legacy events: {scene}')
                for row in old.execute('SELECT id,scope,source_event_id,path FROM media_assets WHERE scope IN (SELECT value FROM json_each(?)) ORDER BY id',
                                       (encode(settings.scenes),)):
                    if row['id'] not in referenced:
                        report['not_transferred'].append({**dict(row), 'reason': 'not linked by the selected imported image references'})
    return report


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit('Media import takes no overrides; stop the instance and configure its root file')
    with instance_lock(Path.cwd()):
        print(encode(import_media(load_instance_config(Path.cwd()))))


if __name__ == '__main__':
    main()
