"""Locate and read task resources without duplicating files in a resource registry."""

from __future__ import annotations

import asyncio
import codecs
from contextlib import contextmanager
from dataclasses import dataclass
import mimetypes
import os
from pathlib import Path
import stat
from typing import BinaryIO, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .task_materials import copy_stream, finish_file_operation, material_directory
from .task_storage import DIRECTORY_FLAGS, output_entries
from .tasks_config import WorkerSettings
from .tasks_store import TERMINAL, TaskStore


ResourceScope = Literal['workspace', 'inputs', 'deliveries', 'runtime', 'shared']
PreviewKind = Literal['text', 'image', 'pdf', 'download']


class ResourceLocation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    scope: ResourceScope
    task_id: int | None = Field(default=None, gt=0)
    path: str = ''

    @field_validator('path')
    @classmethod
    def relative_path(cls, value: str) -> str:
        if value and (any(part in {'', '.', '..'} for part in value.split('/'))
                      or any(char in value for char in ('\x00', '\r', '\n'))):
            raise ValueError(f'Resource path must be relative to its selected root: {value!r}')
        return value

    @model_validator(mode='after')
    def owner(self) -> ResourceLocation:
        if (self.scope == 'shared') != (self.task_id is None):
            raise ValueError('Shared resources have no task_id; other scopes require a task_id')
        if self.scope == 'deliveries' and self.path:
            raise ValueError('Registered deliveries use file_id, not a filesystem path')
        return self


class ResourceFileRef(ResourceLocation):
    file_id: int | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def file_location(self) -> ResourceFileRef:
        if self.scope == 'deliveries':
            if self.file_id is None:
                raise ValueError('A registered delivery requires file_id')
        elif not self.path or self.file_id is not None:
            raise ValueError('A directory file requires path and does not accept file_id')
        return self


def preview_kind(name: str) -> tuple[str, PreviewKind]:
    mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    if mime in {'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp'}:
        return mime, 'image'
    if mime == 'application/pdf':
        return mime, 'pdf'
    if (mime.startswith('text/') or mime in {'application/json', 'application/xml', 'image/svg+xml', 'application/javascript'}
            or Path(name).suffix.lower() in {'.md', '.py', '.toml', '.yaml', '.yml', '.log', '.ini', '.sh', '.sql'}
            or name in {'Dockerfile', 'Makefile', '.gitignore', '.env'}):
        return mime, 'text'
    return mime, 'download'


def resource_purpose(scope: ResourceScope, path: str) -> str:
    if scope != 'workspace':
        return scope
    first = path.split('/')[0]
    return 'output' if first == 'out' else 'session' if first in {'session.jsonl', '.pi-sessions'} else 'workspace'


@dataclass
class OpenedResource:
    stream: BinaryIO
    size: int
    name: str
    mime_type: str
    path: Path


@contextmanager
def resource_parent(root: Path, relative: str):
    if root.resolve(strict=False) != root:
        raise ValueError(f'Resource root must not traverse a symbolic link: {root}')
    directory = os.open(root, DIRECTORY_FLAGS)
    try:
        parts = relative.split('/')
        for part in parts[:-1]:
            nested = os.open(part, DIRECTORY_FLAGS, dir_fd=directory)
            os.close(directory)
            directory = nested
        yield directory, parts[-1]
    finally:
        os.close(directory)


class TaskResources:
    def __init__(self, settings: WorkerSettings, records: TaskStore):
        self.settings, self.records = settings, records

    def root(self, scene: str, location: ResourceLocation) -> Path:
        if location.scope == 'shared':
            return material_directory(self.settings.workspace_root, scene)
        self.records.get(scene, location.task_id)
        if location.scope == 'workspace':
            return self.settings.workspace_root / scene / 'tasks' / str(location.task_id)
        if location.scope == 'inputs':
            return self.settings.runtime_root / scene / str(location.task_id) / 'inputs'
        if location.scope == 'runtime':
            return self.settings.runtime_root / scene / str(location.task_id)
        return self.settings.delivery_root / scene / str(location.task_id)

    async def list(self, scene: str, location: ResourceLocation, *, offset: int, limit: int) -> dict:
        root = self.root(scene, location)
        task = None if location.task_id is None else self.records.get(scene, location.task_id)
        mutable_work = task is not None and task.status in TERMINAL and task.container is None and not task.browser_active
        sources = {} if location.task_id is None else self.records.file_sources(scene, location.task_id)
        browser_sources = {} if location.task_id is None else self.records.browser_file_sources(scene, location.task_id)
        deleted = {} if location.task_id is None else self.records.file_deletions(scene, location.task_id)
        if location.scope == 'deliveries':
            files = self.records.list_files(scene, location.task_id)
            entries = []
            for file in files[offset:offset + limit]:
                mime, preview = preview_kind(file.name)
                source = sources.get(file.id)
                entries.append({'name': file.name, 'kind': 'file', 'size': file.size, 'modified': file.created,
                    'purpose': 'deliveries', 'mime_type': mime, 'preview': preview, 'exists': Path(file.path).is_file(),
                    'reference': ResourceFileRef(scope='deliveries', task_id=location.task_id, file_id=file.id).model_dump(),
                    'note': file.note, 'upload': self.records.latest_file_upload(file), 'source': source,
                    'browser_source': (browser_sources.get(source['path'])
                                       if source is not None and source['scope'] == 'workspace' else None),
                    'registrations': [], 'deletion': deleted.get(file.id), 'deletable': True})
            return {'scene': scene, **location.model_dump(), 'exists': root.is_dir(), 'entries': entries,
                    'next_offset': offset + limit if offset + limit < len(files) else None}
        result = await asyncio.to_thread(output_entries, root, location.path, offset, limit)
        inputs = self.records.input_sources(scene, location.task_id) if location.scope == 'inputs' else {}
        for entry in result['entries']:
            name = entry['path'].split('/')[-1]
            mime, preview = preview_kind(name)
            entry.update(name=name, purpose=resource_purpose(location.scope, entry['path']),
                         mime_type=mime, preview=preview, exists=True, note=None, upload=None,
                         source=inputs.get(name), deletion=None,
                         browser_source=browser_sources.get(entry['path']) if location.scope == 'workspace' else None,
                         deletable=(location.scope == 'shared' or (location.scope == 'workspace' and mutable_work
                                    and resource_purpose(location.scope, entry['path']) != 'session')),
                         registrations=[id for id, source in sources.items() if source is not None
                                        and source['scope'] == location.scope and source['path'] == entry['path']],
                         reference=ResourceFileRef(scope=location.scope, task_id=location.task_id,
                                                   path=entry['path']).model_dump())
        return {'scene': scene, **location.model_dump(), **result}

    def locate_file(self, scene: str, reference: ResourceFileRef) -> tuple[Path, str, str]:
        root = self.root(scene, reference)
        if reference.scope == 'deliveries':
            file = self.records.get_file(scene, reference.task_id, reference.file_id)
            path, name = Path(file.path), file.name
            if path.parent != root:
                raise ValueError(f'Registered file is outside the configured delivery root: {path}; root={root}')
            relative = path.name
        else:
            relative, name = reference.path, reference.path.split('/')[-1]
        return root, relative, name

    def open(self, scene: str, reference: ResourceFileRef) -> OpenedResource:
        root, relative, name = self.locate_file(scene, reference)
        with resource_parent(root, relative) as (directory, filename):
            descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stream = os.fdopen(descriptor, 'rb')
        try:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f'Resource is not a regular file: {relative}')
        except BaseException:
            stream.close()
            raise
        return OpenedResource(stream, info.st_size, name, preview_kind(name)[0], root / relative)

    async def read_text(self, scene: str, reference: ResourceFileRef, *, offset: int, limit: int) -> dict:
        opened = self.open(scene, reference)
        return await finish_file_operation(self._read_text, opened, offset, limit)

    @staticmethod
    def _read_text(opened: OpenedResource, offset: int, limit: int) -> dict:
        with opened.stream as source:
            if offset > opened.size:
                raise ValueError(f'Text offset exceeds file size: {offset} > {opened.size}')
            source.seek(offset)
            raw = source.read(limit)
            decoder = codecs.getincrementaldecoder('utf-8')()
            try:
                text = decoder.decode(raw, final=len(raw) < limit or offset + len(raw) >= opened.size)
            except UnicodeDecodeError as error:
                raise ValueError(f'Resource is not UTF-8 text at byte {offset + error.start}; download the original file') from error
            pending, _ = decoder.getstate()
            through = offset + len(raw) - len(pending)
            return {'text': text, 'offset': offset, 'size': opened.size,
                    'next_offset': through if len(raw) == limit and through < opened.size else None, 'encoding': 'utf-8'}

    def copy_opened(self, opened: OpenedResource, target: Path) -> int:
        with opened.stream as source:
            return copy_stream(source, target, self.settings.max_file_bytes)

    async def remove(self, scene: str, reference: ResourceFileRef) -> dict:
        root, relative, name = self.locate_file(scene, reference)
        size = await finish_file_operation(self._unlink, root, relative)
        return {'reference': reference.model_dump(), 'name': name, 'size': size, 'removed': True}

    @staticmethod
    def _unlink(root: Path, relative: str) -> int:
        with resource_parent(root, relative) as (directory, name):
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f'Only a regular resource file can be deleted: {relative}')
            os.unlink(name, dir_fd=directory)
            return info.st_size
