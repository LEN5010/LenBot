"""Offline rollback of message-linked original images, not cached JPEGs or learned palettes."""

from pathlib import Path
import sqlite3
import stat
import uuid

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .image_assets import MAX_IMAGE_BYTES, inspect_image
from .import_media import LegacyAsset
from .legacy_archive import ArchivedEvent
from .messages import ChatMessage
from .store import encode


class SavedImage(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', allow_inf_nan=False)
    image_index: int = Field(gt=0)
    id: int = Field(gt=0)
    persona_id: str | None
    file: str | None
    source_message_seq: int | None
    source_image_index: int | None
    mime_type: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    animated: int = Field(ge=0, le=1)
    data: bytes
    description: str
    emotions: str
    tags: str


STRINGS = TypeAdapter(list[str])
EXTENSIONS = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp'}


class MediaExport:
    """One stopped export's existing asset/file boundary; no additional migration state."""

    def __init__(self, new: sqlite3.Connection, old: sqlite3.Connection, directory: Path,
                 database_paths: tuple[Path, ...]):
        for path in (directory, *directory.parents):
            if path.is_symlink():
                raise ValueError(f'Image rollback never follows directory links: {path}')
        if any(path.resolve().is_relative_to(directory.resolve()) for path in database_paths):
            raise ValueError('Rollback image directory cannot contain a source/target database or its backup')
        columns = {row[1] for row in old.execute('PRAGMA table_info(media_assets)')}
        if columns != set(LegacyAsset.model_fields):
            raise ValueError(f'Legacy media_assets differs from the offline contract: {sorted(columns)!r}')
        directory.mkdir(parents=True, exist_ok=True)
        self.new, self.old, self.directory = new, old, directory
        self.report = {'directory': str(directory), 'assets_created': [], 'assets_present': [],
                       'files_created': [], 'files_present': [], 'originals_unavailable': [],
                       'notice': 'Database rollback does not delete copied files. Keep this directory with the legacy database; '
                                 'no download, platform send, palette adoption or runtime switch.'}

    def outgoing_assets(self, message: ChatMessage) -> dict[int, str]:
        # Same actual asset convention as the frozen legacy image entrance.
        return {index: self.asset_id(message, index) for index, _ in enumerate(
            (segment for segment in message.segments if segment.type == 'image'), 1)}

    @staticmethod
    def asset_id(message: ChatMessage, index: int) -> str:
        return 'image_' + uuid.uuid5(uuid.NAMESPACE_URL, f'{message.scene}:{message.id}:{index - 1}').hex

    def save_file(self, asset_id: str, image: SavedImage) -> Path:
        path = self.directory / (asset_id + EXTENSIONS[image.mime_type])
        if path.exists() or path.is_symlink():
            if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
                raise ValueError(f'Rollback image target is not a regular file: {path}')
            with path.open('rb') as stream:
                existing = stream.read(MAX_IMAGE_BYTES + 1)
            if existing != image.data:
                raise ValueError(f'Rollback image file differs; never overwrite: {path}')
            self.report['files_present'].append(str(path))
        else:
            with path.open('xb') as stream:
                stream.write(image.data)
            self.report['files_created'].append(str(path))
        return path

    def append(self, message: ChatMessage, seq: int, event: ArchivedEvent) -> None:
        pictures = [segment for segment in message.segments if segment.type == 'image']
        rows = self.new.execute(
            'SELECT mm.image_index,m.*,mm.description,mm.emotions,mm.tags FROM message_media mm '
            'LEFT JOIN media m ON m.id=mm.media_id WHERE mm.message_seq=? ORDER BY mm.image_index', (seq,),
        ).fetchall()
        try:
            saved = [SavedImage.model_validate(dict(row)) for row in rows]
            for image in saved:
                STRINGS.validate_json(image.emotions, strict=True)
                STRINGS.validate_json(image.tags, strict=True)
        except ValidationError as error:
            raise ValueError(f'Invalid saved image at message {seq}: {error}; '
                             f'raw={repr([dict(row) for row in rows])[:1000]}') from error
        by_index = {image.image_index: image for image in saved}
        if any(index > len(pictures) for index in by_index):
            raise ValueError(f'Message {seq} has original image associations beyond actual image segments')
        snapshots, references = [], []
        for index, segment in enumerate(pictures, 1):
            asset_id = self.asset_id(message, index)
            image = by_index.get(index)
            if image is None and message.is_self:
                raise ValueError(f'Outgoing image {index} of message {seq} has no saved original; no guessed asset')
            if image is None:
                self.report['originals_unavailable'].append({'scene': message.scene, 'record': seq, 'image': index})
                path, mime, description, tags = None, None, '', '[]'
            else:
                actual = inspect_image(image.data)
                expected = (image.mime_type, image.width, image.height, bool(image.animated))
                if actual != expected:
                    raise ValueError(f'Saved original image metadata differs from bytes at message {seq} image {index}: '
                                     f'saved={expected!r}, actual={actual!r}')
                path = str(self.save_file(asset_id, image))
                mime, description, tags = image.mime_type, image.description, image.tags
                snapshots.append(image.model_dump(exclude={'data'}))
            if message.is_self:
                locator = ''
            else:
                locator = segment.data.get('url')
                if locator is None:
                    locator = segment.data.get('file')
                if not isinstance(locator, str):
                    raise ValueError(f'Incoming image {index} of message {seq} lacks an actual text url/file: '
                                     f'raw={repr(segment.data)[:500]}')
            asset = LegacyAsset(id=asset_id, scope=message.scene, source_event_id=message.id,
                                locator=locator, mime_type=mime, path=path, description=description,
                                tags_json=tags, enabled=1, curated=0, created_at=float(message.time), palette_order=None)
            record = asset.model_dump()
            previous = self.old.execute('SELECT * FROM media_assets WHERE id=?', (asset_id,)).fetchone()
            if previous is not None:
                if dict(previous) != record:
                    raise ValueError(f'Rollback image asset differs; never overwrite: {asset_id!r}; '
                                     f'actual={repr(dict(previous))[:700]}')
                self.report['assets_present'].append(asset_id)
            else:
                self.old.execute(f"INSERT INTO media_assets({','.join(record)}) VALUES({','.join('?' for _ in record)})",
                                 tuple(record.values()))
                self.report['assets_created'].append(asset_id)
            references.append({'asset_id': asset_id, 'type': 'image', 'source_event_id': message.id})
        if references and not message.is_self:
            event.metadata['media'] = references
        if snapshots:
            event.metadata['next_media'] = snapshots
