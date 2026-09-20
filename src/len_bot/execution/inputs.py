"""Host-side input export: this work's own sources, never arbitrary bytes.

An execution receives two kinds of input and both are chosen by the *work*,
not by whatever the model typed into the tool call:

* an observation the work already saved — its saved body, as text;
* an authorized attachment — the real bytes behind a media asset that one of
  this work's own source events, or one of its own saved observations, already
  refers to.

The model can name an id; it cannot name a host path, a Cookie, a root-config
file or another scene's picture.  A named observation must already be filed on
this work, and a named asset must be one this work's own sources already
refer to; anything else is refused rather than quietly dropped, because "the
file was not there" must not be delivered as an empty input the script
silently works around.

The manifest is the read-only record the container sees.  It keeps the
original identities — the observation id (R), the event that registered the
media (M) and the asset id — plus the coverage and the byte count that were
actually exported, so a later reader can tell what the script was given from
what the script claims it had.  It describes this export, not the source.
"""
from __future__ import annotations

import base64
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from len_bot.execution.protocol import ExecutionInputFile
from len_bot.tools.results import ObservationProvenance

# One execution's inputs together stay well inside the wire field's own bound
# and the Gateway's workspace byte cap; a work that needs more bytes than this
# is a deployment decision, not something a tool call may take.
MAX_INPUT_TOTAL_BYTES = 24_000_000

# Observations and attachments share one admission count, so a script cannot
# ask for eight observations plus eight pictures and land in a directory
# nobody sized.
MAX_INPUTS = 8


class InputFactView(BaseModel):
    """Only provenance fields of a saved export; never its payload or script."""
    model_config = ConfigDict(extra='ignore', strict=True)
    kind: Literal['text', 'asset']
    name: str
    container_path: str = Field(validation_alias='path')
    bytes: int = Field(ge=0)
    coverage: str
    result_id: str | None = None
    status: str | None = None
    source_truncated: bool | None = None
    provenance: ObservationProvenance | None = None
    asset_id: str | None = None
    source_event_id: str | None = None
    scope: str | None = None
    mime_type: str | None = None

    @model_validator(mode='after')
    def source_identity(self):
        if self.kind == 'text' and (not self.result_id or self.status is None):
            raise ValueError('文本输入缺少原资料身份或取得状态')
        if self.kind == 'asset' and (not self.asset_id or not self.scope):
            raise ValueError('媒体输入缺少原资产身份或场景')
        if self.container_path != '/lenbot-control/input/' + self.name:
            raise ValueError('输入清单不是原只读控制目录')
        return self


class InputManifestView(BaseModel):
    """A read-only projection; a missing old revision remains unrecorded."""
    model_config = ConfigDict(extra='ignore', strict=True)
    job_id: str
    scene_id: str
    job_revision: int | None = Field(default=None, ge=1)
    input_directory: Literal['/lenbot-control/input']
    inputs: list[InputFactView] = Field(max_length=MAX_INPUTS)

# Only used to give the exported file a name the container can open; the
# original display identity travels in the manifest, never in this name.
MEDIA_SUFFIXES = {
    'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif',
    'video/mp4': 'mp4', 'video/webm': 'webm', 'audio/mpeg': 'mp3', 'audio/ogg': 'ogg',
    'audio/wav': 'wav', 'audio/mp4': 'm4a', 'audio/flac': 'flac',
}


def _suffix(mime_type: str | None) -> str:
    """A plain container-readable extension, never the display name itself."""
    return MEDIA_SUFFIXES.get((mime_type or '').split(';', 1)[0].strip().lower(), 'bin')


def _media_ids(metadata: dict) -> list[str]:
    """Every asset id one event's metadata already refers to, in saved order.

    The registration path writes the ids under ``media``; a quoted message's
    pictures travel beside it in ``quote_context.media`` with the same shape.
    Reading both here means an imported picture keeps the identity a reader
    would find on the message itself.
    """
    groups = [metadata.get('media') or []]
    quote = metadata.get('quote_context')
    if isinstance(quote, dict):
        groups.append(quote.get('media') or [])
    return [item['asset_id'] for group in groups for item in group
            if isinstance(item, dict) and item.get('asset_id')]


async def allowed_input_assets(event_store, job, scene_id: str) -> set[str]:
    """The asset ids this work already refers to, and no others.

    Two admissible origins, both already saved before this call: the media
    registered on this work's own source events (a quoted message included),
    and the attachments of the observations this work itself produced.  Both
    are read through scene-scoped queries, so a picture from another group
    cannot enter by being referenced from an id this scene does not own.

    The set is exact and complete: a named asset outside it is refused, so a
    model cannot reach a picture merely because it knows a plausible id.
    """
    allowed: set[str] = set()
    for event_id in dict.fromkeys(job.get('source_event_ids') or ()):
        rows = await event_store.read_context(event_id, 0, 0, [scene_id])
        for row in rows:
            if row.get('id') != event_id:
                continue
            allowed.update(_media_ids(row.get('metadata') or {}))
    for result_id in dict.fromkeys(job.get('result_ids') or ()):
        observation = await event_store.read_tool_observation(result_id, [scene_id])
        if observation is not None:
            allowed.update(observation.attachments)
    return allowed


async def collect_input_entries(event_store, job, job_id: str, scene_id: str,
                                result_ids, asset_ids, read_asset) -> list[dict]:
    """Resolve both input kinds into entries the caller can write or send.

    ``read_asset(asset_id)`` is the deployment's own scoped media read; it is
    passed in rather than imported so this module stays free of the media and
    HTTP stacks — and so the Gateway process never gains them by importing the
    protocol it shares with the host.

    Order is preserved: observations first, then attachments, each in the
    order the caller named them, with repeats collapsed.  A caller that asks
    for the same id twice gets one file and one manifest entry.
    """
    wanted = [*dict.fromkeys(result_ids), *dict.fromkeys(asset_ids)]
    if len(wanted) > MAX_INPUTS:
        raise ValueError(f'一次执行最多导入 {MAX_INPUTS} 份资料（观察与附件合计）')
    registered = set(job.get('result_ids') or ())
    entries: list[dict] = []
    total = 0
    for result_id in dict.fromkeys(result_ids):
        if result_id not in registered:
            raise ValueError(f'资料 {result_id} 尚未登记为当前工作的资料')
        observation = await event_store.read_tool_observation(result_id, [scene_id])
        if observation is None:
            raise ValueError(f'资料 {result_id} 不属于当前场景或已不存在')
        content = observation.content
        total += len(content.encode('utf-8'))
        if total > MAX_INPUT_TOTAL_BYTES:
            raise ValueError('本次导入的资料合计超过字节上限')
        name = f'result_{len(entries) + 1}.txt'
        entries.append({'kind': 'text', 'name': name,
            'path': f'/lenbot-control/input/{name}',
            'result_id': result_id, 'coverage': observation.coverage,
            'source_truncated': observation.source_truncated or (observation.truncated and observation.displayed_range is None),
            'provenance': observation.provenance.model_dump(mode='json') if observation.provenance else None,
            'status': observation.status, 'bytes': len(content.encode('utf-8')),
            'sources': [source.model_dump(mode='json') for source in observation.sources],
            'text': content, 'data': None})
    if asset_ids:
        allowed = await allowed_input_assets(event_store, job, scene_id)
    for asset_id in dict.fromkeys(asset_ids):
        if asset_id not in allowed:
            raise ValueError(f'媒体资产 {asset_id} 不是当前工作已取得的资料，不能导入')
        asset, data = await read_asset(asset_id)
        total += len(data)
        if total > MAX_INPUT_TOTAL_BYTES:
            raise ValueError('本次导入的资料合计超过字节上限')
        name = f'asset_{len(entries) + 1}.{_suffix(asset.get("mime_type"))}'
        entries.append({'kind': 'asset', 'name': name,
            'path': f'/lenbot-control/input/{name}',
            'asset_id': asset_id, 'source_event_id': asset.get('source_event_id'),
            'scope': asset.get('scope'), 'mime_type': asset.get('mime_type'),
            'bytes': len(data), 'description': asset.get('description') or '',
            'coverage': 'media_bytes', 'text': None, 'data': data})
    return entries


def manifest_of(entries: list[dict], job_id: str, scene_id: str, job_revision: int) -> dict:
    """The manifest as the container reads it, without the payloads.

    The bytes themselves stay in this process; only the identity, the coverage
    and the exported size are written down, so the manifest cannot be mistaken
    for the file it describes.
    """
    return {'job_id': job_id, 'scene_id': scene_id, 'job_revision': job_revision,
            'input_directory': '/lenbot-control/input',
            'inputs': [{key: value for key, value in entry.items()
                        if key not in {'text', 'data'}} for entry in entries]}


def wire_input_files(entries: list[dict], manifest: dict) -> list[ExecutionInputFile]:
    """The entries as the wire carries them, plus the manifest that names them.

    Bytes travel as the host's own export; the Gateway writes them down and is
    never asked to fetch an asset by id.
    """
    files = [ExecutionInputFile(name=entry['name'], text=entry['text'])
             if entry['kind'] == 'text' else
             ExecutionInputFile(name=entry['name'],
                                content_base64=base64.b64encode(entry['data']).decode())
             for entry in entries]
    files.append(ExecutionInputFile(name='manifest.json',
                                    text=json.dumps(manifest, ensure_ascii=False, indent=2)))
    return files
