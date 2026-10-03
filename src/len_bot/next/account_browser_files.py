"""Native browser file IPC: stream bytes outside model text, retain actual receipts."""

from __future__ import annotations

import base64
import binascii
from contextlib import asynccontextmanager
from typing import Annotated, BinaryIO, Literal, TYPE_CHECKING

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

from .task_materials import require_name, finish_file_operation
from .task_resources import OpenedResource

if TYPE_CHECKING:
    from .account_browser import AccountBrowser


class NativeRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


class FileTarget(NativeRecord):
    ref: str | None = Field(default=None, min_length=1)
    selector: str | None = Field(default=None, min_length=1)
    tab_id: int | None = None
    timeout_ms: int | None = Field(default=None, gt=0, le=4294967295)

    @model_validator(mode='after')
    def one_target(self):
        if (self.ref is None) == (self.selector is None):
            raise ValueError('upload/download requires exactly one observed ref or selector')
        return self


class UploadTarget(FileTarget):
    mode: Literal['input', 'drop'] = 'input'


class ScreenshotTarget(NativeRecord):
    ref: str | None = None
    tab_id: int | None = None


class TransferBegin(NativeRecord):
    transfer_id: str = Field(min_length=1)
    chunk_size: int = Field(gt=0, le=512 * 1024)


class TransferChunk(NativeRecord):
    next_offset: int = Field(ge=0)
    eof: bool = False
    data_base64: str | None = None


class TransferReady(NativeRecord):
    transfer_id: str
    byte_size: int = Field(ge=0)


class TransferRelease(NativeRecord):
    released: bool


class UploadReceipt(NativeRecord):
    tab_id: int
    file_names: list[str]
    used_ref: str | None = None
    used_selector: str | None = None


class DownloadReceipt(NativeRecord):
    tab_id: int
    suggested_filename: Annotated[str, AfterValidator(require_name)]
    byte_size: int = Field(ge=0)
    transfer_id: str = Field(min_length=1)
    used_ref: str | None = None
    used_selector: str | None = None
    mime: str | None = None
    danger: str | None = None


class Page(NativeRecord):
    tab_id: int
    title: str | None = None
    url: str | None = None
    window_id: int | None = None
    active: bool | None = None
    scope: Literal['user', 'agent'] | None = None


class Pages(NativeRecord):
    tabs: list[Page]


class ScreenshotReceipt(NativeRecord):
    image_base64: str
    format: Literal['png']
    tab_id: int
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    capture_id: str | None = None
    capture_unavailable: str | None = None
    dialogs: list[dict] = Field(default_factory=list)


def parse[T: BaseModel](model: type[T], result: dict) -> T:
    try:
        return model.model_validate(result)
    except ValidationError as error:
        metadata = {key: value for key, value in result.items() if key not in {'data_base64', 'image_base64'}}
        raise ValueError(f'Browser file response invalid: {error.errors(include_input=False)}; '
                         f'metadata={repr(metadata)[:500]}') from error


class BrowserTransfers:
    """The task service holds the client's session lock and owns local streams."""

    def __init__(self, client: AccountBrowser):
        self.client = client

    async def page(self, session: str, tab_id: int | None) -> Page:
        pages = parse(Pages, await self.client.rpc('tool.tab_list', {'session_id': session, 'scope': 'all'}))
        selected = [page for page in pages.tabs if page.tab_id == tab_id] if tab_id is not None else [
            page for page in pages.tabs if page.scope == 'agent' and page.active]
        if len(selected) != 1:
            raise ValueError(f'Browser file action has no unique target tab: tab_id={tab_id}; pages={pages.model_dump()}')
        return selected[0]

    async def tool(self, session: str, method: str, target: FileTarget, **params) -> dict:
        timeout = target.timeout_ms if target.timeout_ms is not None else int(self.client.settings.timeout_seconds * 1000)
        return await self.client.rpc('tool.' + method, {**target.model_dump(exclude_none=True),
            **params, 'session_id': session, 'timeout_ms': timeout}, response_timeout_seconds=timeout / 1000 + 15)

    async def screenshot(self, session: str, target: ScreenshotTarget) -> tuple[ScreenshotReceipt, bytes]:
        receipt = parse(ScreenshotReceipt, await self.client.rpc('tool.screenshot', {
            **target.model_dump(exclude_none=True), 'session_id': session}))
        try:
            image = base64.b64decode(receipt.image_base64, validate=True)
            if not image.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('screenshot is not PNG')
        except (TypeError, ValueError, binascii.Error) as error:
            raise ValueError(f'Browser screenshot parse failed: {error}; tab_id={receipt.tab_id}') from error
        if receipt.tab_id != target.tab_id:
            raise ValueError(f'Browser screenshot returned another tab: {receipt.tab_id} != {target.tab_id}')
        return receipt, image

    @asynccontextmanager
    async def staging(self):
        transfers = []
        original = None
        try:
            yield transfers
        except BaseException as error:
            original = error
            raise
        finally:
            errors = []
            for transfer in transfers:
                try:
                    result = parse(TransferRelease, await self.client.rpc('transfer.release', {'transfer_id': transfer}))
                    if not result.released:
                        raise RuntimeError(f'Browser transfer.release did not release {transfer}')
                except Exception as error:
                    errors.append(f'{type(error).__name__}: {error}')
            if errors:
                message = 'Browser transfer cleanup failed: ' + '; '.join(errors)
                if original is not None:
                    original.add_note(message)
                else:
                    raise RuntimeError(message)

    @asynccontextmanager
    async def upload(self, session: str, target: UploadTarget, files: list[OpenedResource]):
        async with self.staging() as ids:
            uploaded = []
            for source in files:
                begin = parse(TransferBegin, await self.client.rpc('transfer.begin', {
                    'session_id': session, 'name': source.name, 'byte_size': source.size}))
                ids.append(begin.transfer_id)
                offset = 0
                while offset < source.size:
                    data = await finish_file_operation(source.stream.read, min(begin.chunk_size, source.size - offset))
                    if not data:
                        raise OSError(f'Browser upload source became shorter: {source.path}')
                    result = parse(TransferChunk, await self.client.rpc('transfer.chunk', {
                        'transfer_id': begin.transfer_id, 'offset': offset, 'data_base64': base64.b64encode(data).decode('ascii')}))
                    offset += len(data)
                    if result.next_offset != offset:
                        raise ValueError(f'Browser upload offset differs: {result.next_offset} != {offset}')
                if await finish_file_operation(source.stream.read, 1):
                    raise OSError(f'Browser upload source grew: {source.path}')
                ready = parse(TransferReady, await self.client.rpc('transfer.finish', {'transfer_id': begin.transfer_id}))
                if ready.transfer_id != begin.transfer_id or ready.byte_size != source.size:
                    raise ValueError(f'Browser upload completion differs: {ready.model_dump()}')
                uploaded.append({'transfer_id': begin.transfer_id, 'name': source.name})
            receipt = parse(UploadReceipt, await self.tool(session, 'upload', target, files=uploaded))
            if receipt.tab_id != target.tab_id:
                raise ValueError(f'Browser upload returned another tab: {receipt.tab_id} != {target.tab_id}')
            yield receipt

    @asynccontextmanager
    async def download(self, session: str, target: FileTarget, max_bytes: int):
        async with self.staging() as ids:
            result = await self.tool(session, 'download', target)
            receipt = parse(DownloadReceipt, result)
            ids.append(receipt.transfer_id)
            if receipt.tab_id != target.tab_id:
                raise ValueError(f'Browser download returned another tab: {receipt.tab_id} != {target.tab_id}')
            if receipt.byte_size > max_bytes:
                raise ValueError(f'Browser download exceeds worker.max_file_bytes: {receipt.byte_size} > {max_bytes}')
            yield receipt

    async def read(self, receipt: DownloadReceipt, destination: BinaryIO) -> None:
        offset = 0
        while True:
            chunk = parse(TransferChunk, await self.client.rpc('transfer.read', {
                'transfer_id': receipt.transfer_id, 'offset': offset, 'data_base64': ''}))
            try:
                data = base64.b64decode(chunk.data_base64, validate=True)
            except (TypeError, ValueError, binascii.Error) as error:
                raise ValueError(f'Invalid browser download bytes at offset {offset}: {error}') from error
            next_offset = offset + len(data)
            if (chunk.next_offset != next_offset or next_offset > receipt.byte_size
                    or chunk.eof != (next_offset == receipt.byte_size) or (not data and not chunk.eof)):
                raise ValueError(f'Browser download chunk boundary differs: offset={offset}, '
                                 f'next={chunk.next_offset}, decoded={len(data)}, eof={chunk.eof}, size={receipt.byte_size}')
            await finish_file_operation(destination.write, data)
            offset = next_offset
            if chunk.eof:
                return
