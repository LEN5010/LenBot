"""Scoped image IO and native model input preparation. Never calls a model."""
from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import logging
import shutil
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urldefrag

import httpx
from PIL import Image, ImageOps

from len_bot.media.models import CHARACTER_REFERENCE_TAG, CuratedMediaBaseline, CuratedMediaSavedError, PreparedMediaContext
from len_bot.media.store import PALETTE_UNCHANGED
from len_bot.tools.http import PublicReadError, fetch_public
from len_bot.tools.pdf_reader import MAX_PDF_BYTES, read_pdf
from len_bot.tools.results import ToolNextCall, ToolResult, ToolSource, error_message, error_source_url

logger = logging.getLogger(__name__)

FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}
MEDIA_SUFFIXES = {"video/mp4": "mp4", "video/webm": "webm", "audio/mpeg": "mp3", "audio/ogg": "ogg", "audio/wav": "wav", "audio/mp4": "m4a", "audio/flac": "flac"}


def sniff_media_mime(data: bytes, declared: str | None = None, expected_type: str | None = None) -> str:
    """Accept only payloads whose bytes identify a supported media container."""
    if not data:
        raise ValueError("媒体响应为空")
    declared = (declared or "").split(';', 1)[0].strip().lower()
    detected = None
    if len(data) >= 12 and data[4:8] == b"ftyp":
        detected = "video/mp4" if declared.startswith("video/") else "audio/mp4" if declared.startswith("audio/") else "video/mp4"
    elif data.startswith(b"\x1a\x45\xdf\xa3"):
        detected = "video/webm"
    elif data.startswith(b"OggS"):
        detected = "audio/ogg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        detected = "audio/wav"
    elif data.startswith(b"fLaC"):
        detected = "audio/flac"
    elif data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        detected = "audio/mpeg"
    if not detected:
        raise ValueError("媒体响应不是可识别的受支持容器")
    if expected_type and not detected.startswith(expected_type + "/"):
        raise ValueError(f"下载内容是{detected}，不是声明的{expected_type}媒体")
    if declared and declared not in {"application/octet-stream", "binary/octet-stream"}:
        declared_type = declared.split('/', 1)[0]
        if not detected.startswith(declared_type + "/"):
            raise ValueError(f"响应类型{declared}与媒体容器{detected}不一致")
        if declared in MEDIA_SUFFIXES and declared != detected:
            raise ValueError(f"响应类型{declared}与实际容器{detected}不一致")
        if declared in MEDIA_SUFFIXES:
            return declared
    return detected


def validate_image(data: bytes, *, max_bytes: int, max_pixels: int):
    if not data or len(data) > max_bytes:
        raise ValueError(f"图片为空或超过 {max_bytes} 字节上限")
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in FORMATS:
            raise ValueError("仅支持PNG、JPEG、WEBP和GIF")
        if image.width * image.height > max_pixels:
            raise ValueError("图片像素超过上限")
        mime_type = FORMATS[image.format]
        image.verify()
    return mime_type


def prepare_image(data: bytes, *, max_bytes: int, max_pixels: int, max_dimension: int):
    validate_image(data, max_bytes=max_bytes, max_pixels=max_pixels)
    with Image.open(io.BytesIO(data)) as image:
        animated = getattr(image, "n_frames", 1) > 1
        image.seek(0)
        frame = ImageOps.exif_transpose(image).convert("RGBA")
        frame.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        background = Image.new("RGBA", frame.size, "white")
        background.alpha_composite(frame)
        output = io.BytesIO()
        # The frame is already composited onto white and flattened to RGB, so a
        # lossless encoding preserves nothing an alpha channel would have kept.
        # It only inflates the request body: measured on real group images, six
        # pictures reach 13.6 MB at p90 and 37 MB at worst as PNG, which is what
        # the relay drops mid-upload.
        background.convert("RGB").save(output, format="JPEG", quality=85, optimize=True)
        return output.getvalue(), animated, frame.width, frame.height


def image_block(data: bytes):
    return {"type": "image_url", "image_url": {
        "url": "data:image/jpeg;base64," + base64.b64encode(data).decode(), "detail": "high"}}


class MediaService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.root = (Path(runtime.config.db_path).resolve().parent / "media").resolve()
        self._client = httpx.AsyncClient(timeout=runtime.config.media_request_timeout_seconds,
                                       follow_redirects=False, trust_env=False)
        self._locks: dict[str, asyncio.Lock] = {}
        self._io_slots = asyncio.Semaphore(runtime.config.media_io_concurrency)

    async def close(self):
        await self._client.aclose()

    async def reset_cache(self):
        self._locks.clear()
        retained = {Path(path).resolve() for path in await self.runtime.event_store.retained_media_paths()}
        if self.root.exists():
            if not retained:
                await asyncio.to_thread(shutil.rmtree, self.root)
            else:
                await asyncio.to_thread(self._clear_unretained_files, retained)

    def _clear_unretained_files(self, retained):
        for path in self.root.rglob("*"):
            if path.is_file() and path.resolve() not in retained:
                path.unlink()
        for path in sorted(self.root.rglob("*"), key=lambda path: len(path.parts), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()

    async def _store_bytes(self, data):
        try:
            mime = await asyncio.to_thread(validate_image, data,
                max_bytes=self.runtime.config.media_max_image_bytes,
                max_pixels=self.runtime.config.media_max_image_pixels)
        except (OSError, Image.DecompressionBombError) as error:
            raise ValueError("图片内容无法验证") from error
        suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}[mime]
        file_id = uuid.uuid4().hex
        path = self.root / f"{file_id}.{suffix}"
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / f".{file_id}.tmp"
        try:
            await asyncio.to_thread(temporary.write_bytes, data)
            await asyncio.to_thread(temporary.replace, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            raise
        return mime, str(path)

    async def _store_file(self, data: bytes, mime_type: str, *, expected_type: str | None = None):
        if not data or len(data) > self.runtime.config.media_max_file_bytes:
            raise ValueError(f"媒体为空或超过 {self.runtime.config.media_max_file_bytes} 字节上限")
        mime_type = sniff_media_mime(data, mime_type, expected_type=expected_type)
        suffix = MEDIA_SUFFIXES.get(mime_type, mime_type.split('/', 1)[1].split('+', 1)[0][:8] or 'bin')
        file_id = uuid.uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{file_id}.{suffix}"
        temporary = self.root / f".{file_id}.tmp"
        try:
            await asyncio.to_thread(temporary.write_bytes, data)
            await asyncio.to_thread(temporary.replace, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            raise
        return mime_type, str(path)

    async def get_bytes(self, asset_id, scene_id, *, include_disabled=False):
        async with self._locks.setdefault(asset_id, asyncio.Lock()), self._io_slots:
            asset = await self.runtime.event_store.get_media(asset_id, [scene_id, "global-safe"], include_disabled=include_disabled)
            if not asset:
                raise ValueError("图片不存在、已停用或不在本场景中")
            if asset["path"]:
                path = Path(asset["path"]).resolve()
                if (not path.is_relative_to(self.root) or not path.is_file()
                        or path.stat().st_size > self.runtime.config.media_max_image_bytes):
                    raise ValueError("图片缓存不可用")
                data = await asyncio.to_thread(path.read_bytes)
                return asset, data
            locator = asset["locator"]
            if locator.startswith("base64://"):
                if len(locator) > self.runtime.config.media_max_image_bytes * 4 // 3 + 100:
                    raise ValueError("图片超过上限")
                data = base64.b64decode(locator.removeprefix("base64://"), validate=True)
            elif locator.startswith(("https://", "http://")):
                _, _, data = await fetch_public(self._client, locator, max_bytes=self.runtime.config.media_max_image_bytes)
            else:
                raise ValueError("图片只有平台文件标识，没有可读取地址；不会读取任意本地路径")
            mime, path = await self._store_bytes(data)
            event = await self.runtime.event_store.save_media_file(asset_id, asset["scope"], mime, path)
            await self.runtime.commit_tool_observation(event)
            asset.update(mime_type=mime, path=path)
            return asset, data

    async def get_file_bytes(self, asset_id, scene_id, *, include_disabled=False):
        """Read a previously registered image/video/audio asset without assuming pixels."""
        async with self._locks.setdefault(asset_id, asyncio.Lock()), self._io_slots:
            asset = await self.runtime.event_store.get_media(asset_id, [scene_id, "global-safe"], include_disabled=include_disabled)
            if not asset:
                raise ValueError("媒体不存在、已停用或不在本场景中")
            if asset["path"]:
                path = Path(asset["path"]).resolve()
                if not path.is_relative_to(self.root) or not path.is_file() or path.stat().st_size > self.runtime.config.media_max_file_bytes:
                    raise ValueError("媒体缓存不可用")
                data = await asyncio.to_thread(path.read_bytes)
                asset["mime_type"] = sniff_media_mime(data, asset.get("mime_type"))
                return asset, data
            locator = asset["locator"]
            if locator.startswith("base64://"):
                if len(locator) > self.runtime.config.media_max_file_bytes * 4 // 3 + 100:
                    raise ValueError("媒体超过上限")
                data = base64.b64decode(locator.removeprefix("base64://"), validate=True)
            elif locator.startswith(("https://", "http://")):
                _, headers, data = await fetch_public(self._client, locator, max_bytes=self.runtime.config.media_max_file_bytes)
                if not asset.get("mime_type"):
                    asset["mime_type"] = headers.get("content-type", "").split(';', 1)[0].lower()
            else:
                raise ValueError("媒体只有平台文件标识，没有可读取地址；不会读取任意本地路径")
            mime, path = await self._store_file(data, asset.get("mime_type") or "application/octet-stream")
            event = await self.runtime.event_store.save_media_file(asset_id, asset["scope"], mime, path)
            await self.runtime.commit_tool_observation(event)
            asset.update(mime_type=mime, path=path)
            return asset, data

    async def save_downloaded(self, asset_id: str, scene_id: str, source_url: str, data: bytes, mime_type: str, description: str, *, source_event_id: str | None = None, expected_type: str | None = None):
        if not source_url.startswith(("https://", "http://")):
            raise ValueError("媒体来源必须是公开HTTP(S)地址")
        # Validate and persist bytes before creating the catalog row. A failed
        # HTML/JSON response must not leave behind a sendable-looking asset.
        mime, path = (await self._store_bytes(data) if expected_type == 'image'
                      else await self._store_file(data, mime_type, expected_type=expected_type))
        asset = await self.runtime.event_store.get_media(asset_id, [scene_id, "global-safe"], include_disabled=True)
        if asset is None:
            await self.runtime.event_store.register_external_media(asset_id, scene_id, source_url, source_event_id=source_event_id)
        event = await self.runtime.event_store.save_media_file(asset_id, scene_id, mime, path, description=description)
        await self.runtime.commit_tool_observation(event)
        return await self.runtime.event_store.get_media(asset_id, [scene_id], include_disabled=True)

    async def read_image_bytes(self, asset_id: str, scene_id: str):
        """One referenced picture's real bytes, read only when asked for by id.

        A message that arrived with a picture registers an asset whose bytes are
        not on disk yet: only the locator is.  Handing those bytes to an
        execution therefore means fetching and validating them here — through
        ``get_bytes``, the same scoped read the model's own image input uses —
        and never letting a caller name a path.

        The bytes are then opened as an image before they leave.  The cache
        path hands back whatever file it stored, so "the row says image/png" is
        not the same fact as "Pillow can decode this"; an execution that was
        told it received a picture must not receive an undecodable blob with
        that name.
        """
        if not self.runtime.config.media_enabled:
            raise ValueError("媒体能力已停用，不能把图片导入执行")
        asset, data = await self.get_bytes(asset_id, scene_id)
        try:
            mime = await asyncio.to_thread(validate_image, data,
                max_bytes=self.runtime.config.media_max_image_bytes,
                max_pixels=self.runtime.config.media_max_image_pixels)
        except (OSError, Image.DecompressionBombError) as error:
            raise ValueError("图片内容无法验证") from error
        asset["mime_type"] = mime
        return asset, data

    async def upload(self, data, scope, description, tags):
        if scope != "global-safe" and not scope.startswith(("group:", "private:")):
            raise ValueError("请选择群聊、私聊或global-safe素材范围")
        if CHARACTER_REFERENCE_TAG in tags and '表情包' in tags:
            raise ValueError('人物参考与反应表情使用不同标签')
        mime, path = await self._store_bytes(data)
        asset_id = "image_" + uuid.uuid4().hex
        event = await self.runtime.event_store.save_media_file(asset_id, scope, mime, path,
            description=description, tags=tags, curated=True)
        return await self._finish_curated_save(event, asset_id, scope)

    async def _finish_curated_save(self, event, asset_id, scope):
        # The original media transaction has already committed. A subsequent
        # event/read failure must not invite another upload of the same file.
        try:
            await self.runtime.commit_tool_observation(event)
        except Exception as error:
            raise CuratedMediaSavedError(asset_id, scope, event.id, 'event', type(error).__name__) from error
        try:
            asset = await self.runtime.event_store.get_media(asset_id, [scope], include_disabled=True)
            if asset is None:
                raise ValueError('Saved asset no longer available')
        except Exception as error:
            raise CuratedMediaSavedError(asset_id, scope, event.id, 'readback', type(error).__name__) from error
        return asset

    async def save_generated(self, data: bytes, scene_id: str, source_event_id: str, description: str):
        """Store a command image as source-owned output, never as a curated example."""
        mime, path = await self._store_bytes(data)
        asset_id = 'image_' + uuid.uuid4().hex
        event = await self.runtime.event_store.save_generated_media(
            asset_id, scene_id, source_event_id, mime, path, description)
        await self.runtime.commit_tool_observation(event)
        return await self.runtime.event_store.get_media(asset_id, [scene_id])

    async def edit(self, asset_id, scope, description, tags, enabled, *, baseline: CuratedMediaBaseline, palette_order=PALETTE_UNCHANGED):
        async with self.runtime.config_update_lock:
            bound=any(item.asset_id==asset_id for item in self.runtime.config_store.current.runtime.character_reference_assets)
            if bound and CHARACTER_REFERENCE_TAG not in tags:
                raise ValueError('该图片仍绑定人物参考；先解除绑定，再改变素材用途')
            event = await self.runtime.event_store.edit_media(asset_id, scope, description, tags, enabled,
                baseline=baseline, palette_order=palette_order)
        return await self._finish_curated_save(event, asset_id, scope)

    @staticmethod
    def character_reference_issue(asset):
        if asset is None:
            return '素材不存在或不在当前可读范围'
        if not asset['curated'] or not (asset['mime_type'] or '').startswith('image/') or not asset['path']:
            return '人物参考需要已登记的运营图片'
        if not asset['enabled']:
            return '素材已停用'
        if CHARACTER_REFERENCE_TAG not in asset['tags'] or '表情包' in asset['tags']:
            return '人物参考需使用独立的人物参考标签'
        if asset['palette_order'] is not None:
            return '人物参考的表情目录顺序须留空'
        return None

    async def validate_character_references(self, bindings):
        assets=await self.runtime.event_store.reference_assets_for_operator([item.asset_id for item in bindings])
        for index,item in enumerate(bindings):
            issue=self.character_reference_issue(assets.get(item.asset_id))
            if issue:
                raise ValueError(f'character_reference_assets[{index}].asset_id: {issue}')

    async def character_reference_catalog(self, scene_id, bindings):
        if not self.runtime.config.media_enabled:
            return []
        catalog=[]
        for item in bindings:
            asset=await self.runtime.event_store.get_media(item.asset_id,[scene_id,'global-safe'])
            if self.character_reference_issue(asset) is not None:
                continue
            catalog.append({'character_key':item.character_key,'outfit':item.outfit,
                'asset_id':item.asset_id,'description':asset['description'][:240],'status':'catalog_only'})
        return catalog

    async def _prepare_asset(self, asset_id, scene_id):
        asset, data = await self.get_bytes(asset_id, scene_id)
        prepared, animated, width, height = await asyncio.to_thread(prepare_image, data,
            max_bytes=self.runtime.config.media_max_image_bytes,
            max_pixels=self.runtime.config.media_max_image_pixels,
            max_dimension=self.runtime.config.media_max_dimension)
        return prepared, {"asset_id": asset_id, "status": "included", "source_event_id": asset["source_event_id"],
            "coverage": "first_frame" if animated else "image", "width": width, "height": height}

    @staticmethod
    def _media_error(asset_id, error, *, source_url=None):
        error_type = type(error).__name__
        http_status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        if isinstance(error, (httpx.HTTPStatusError, httpx.RequestError)):
            source_url = str(error.request.url)
        if isinstance(error, (httpx.TimeoutException, TimeoutError)):
            code, reason = 'timeout', f'本次媒体读取超时：{error}'
        elif isinstance(error, httpx.HTTPStatusError):
            code = 'not_found' if error.response.status_code in {404, 410} else 'media_http_error'
            reason = f'媒体源返回HTTP {error.response.status_code}：{error}'
        elif isinstance(error, PublicReadError):
            code, reason = 'public_read_failed', str(error)
        elif isinstance(error, ValueError):
            code, reason = 'media_unavailable', str(error)
        elif isinstance(error, OSError):
            code, reason = 'media_io_error', f'媒体文件读写失败：{error}'
        else:
            code, reason = 'media_unavailable', f'媒体读取失败：{error}'
        # The model is told about this through the manifest, but nothing reached
        # the operator: a third of stored assets had no file and the only trace
        # was an httpx access line. A failed read is an operational fact.
        logger.warning("Media read failed: asset=%s code=%s http=%s %s: %s",
                       asset_id, code, http_status, error_type, error)
        return {"asset_id": asset_id, "status": "error", "error_code": code, "error_type": error_type,
                "error_stage": "execution", "http_status": http_status,
                "source_url": error_source_url(source_url) if source_url else None,
                "reason": error_message(f'{error_type}: {reason}')}

    async def prepare_context_images(self, scene_id: str, asset_ids: Sequence[str], *, limit: int,
                                     read_cache: dict[str, PreparedMediaContext] | None = None,
                                     supports_segment_vision: bool = False,
                                     preparation_stats: dict[str, int | float] | None = None) -> PreparedMediaContext:
        """Native image blocks, with explicit omissions and no hidden model call."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= self.runtime.config.max_context_images:
            raise ValueError("Image limit must be within the configured image window")
        result: PreparedMediaContext = {"blocks": [], "manifest": []}
        reads = read_cache if read_cache is not None else {}
        def record(name, value):
            if preparation_stats is not None:
                preparation_stats[name]=round(preparation_stats.get(name,0)+value,3)
        for asset_id in dict.fromkeys(asset_ids):
            if not self.runtime.config.media_enabled:
                result["manifest"].append({"asset_id": asset_id, "status": "omitted", "reason": "media_disabled"})
                continue
            started=time.monotonic()
            try:
                asset = await self.runtime.event_store.get_media(asset_id, [scene_id, 'global-safe'])
            finally:
                record('asset_checks',1)
                record('asset_lookup_ms',(time.monotonic()-started)*1000)
            if asset is None:
                # Prepared pixels cannot outlive the original asset's current
                # scope or enablement, even inside one packing attempt cache.
                reads.pop(asset_id,None)
                result['manifest'].append({'asset_id':asset_id,'status':'omitted','reason':'asset_unavailable',
                    'note':'图片不存在、已停用或不在本场景中，本次未装入像素'})
                continue
            mime = (asset or {}).get('mime_type') or ''
            if mime.startswith(('audio/', 'video/')):
                result['manifest'].append({'asset_id': asset_id, 'status': 'available', 'media_type': mime,
                    'source_event_id': asset['source_event_id'], 'coverage': 'registered_media_reference_only',
                    'note': '音视频引用已登记；本请求未装入音轨或连续画面。可用的转写与采样帧须沿各自实际工具结果核对。'})
                continue
            if asset_id.startswith('segment_') and not supports_segment_vision:
                result['manifest'].append({'asset_id': asset_id, 'status': 'omitted', 'reason': 'capability_missing',
                    'note': '当前模型绑定尚未确认视觉能力；采样帧已保存但未向模型装配图片'})
                continue
            if len(result["blocks"]) >= limit:
                result["manifest"].append({"asset_id": asset_id, "status": "omitted", "reason": "image_limit"})
                continue
            if asset_id not in reads:
                started=time.monotonic()
                record('source_preparations',1)
                try:
                    prepared, manifest = await self._prepare_asset(asset_id, scene_id)
                    reads[asset_id] = {'blocks':[image_block(prepared)], 'manifest':[manifest]}
                except (ValueError, OSError, httpx.HTTPError, Image.DecompressionBombError) as error:
                    reads[asset_id] = {'blocks':[], 'manifest':[self._media_error(asset_id, error)]}
                    record('preparation_failures',1)
                finally:
                    record('source_prepare_ms',(time.monotonic()-started)*1000)
            else:
                record('prepared_reuses' if reads[asset_id]['blocks'] else 'failure_reuses',1)
            one = reads[asset_id]
            manifest = copy.deepcopy(one['manifest'][0])
            if one['blocks']:
                manifest['block_index'] = len(result['blocks'])
                result['blocks'].append(copy.deepcopy(one['blocks'][0]))
            result["manifest"].append(manifest)
        return result

    async def read_media(self, asset_id: str, scene_id: str) -> ToolResult:
        """Read and validate the asset; the active model receives its pixels later."""
        if not self.runtime.config.media_enabled:
            return ToolResult(status="unsupported", content="媒体能力已停用", error_code="media_disabled")
        try:
            _, manifest = await self._prepare_asset(asset_id, scene_id)
        except (ValueError, OSError, httpx.HTTPError, Image.DecompressionBombError) as error:
            failure = self._media_error(asset_id, error)
            return ToolResult.failure(failure["reason"], failure["error_code"], stage='execution',
                tool_name='read_media', http_status=failure['http_status'],
                sources=[ToolSource(url=failure['source_url'], title='本次媒体读取来源')]
                if failure['source_url'] else [])
        manifest['status'] = 'prepared'
        manifest['note'] = '图片已取得并准备；是否进入当前模型以本次请求的图片清单为准。'
        return ToolResult(content=json.dumps(manifest, ensure_ascii=False), attachments=[asset_id],
            sources=[ToolSource(event_id=manifest["source_event_id"], title="原始图片")],
            coverage=manifest["coverage"], evidence_kind="retrieval")

    async def read_web_media(self, url: str, page: int | None = None):
        """Prepare public pixels; the toolkit commits files and observation together."""
        if not self.runtime.config.media_enabled:
            return ToolResult(status='unsupported', content='媒体能力已停用', error_code='media_disabled'), []
        source_next_call = None
        try:
            async with self._io_slots:
                final_url, headers, data = await fetch_public(self._client, url, max_bytes=MAX_PDF_BYTES)
                media_type = headers.get('content-type', '').split(';')[0].lower()
                if media_type == 'application/pdf' or data.startswith(b'%PDF-'):
                    rendered = await read_pdf(data, page=1 if page is None else page)
                    data = base64.b64decode(rendered['png_base64'], validate=True)
                    description = f"PDF第{rendered['page']}页，共{rendered['page_count']}页"
                    document_url = urldefrag(final_url)[0]
                    source_url, coverage = f"{document_url}#page={rendered['page']}", 'pdf_page'
                    if rendered['page'] < rendered['page_count']:
                        source_next_call = ToolNextCall(name='read_web_media',
                            arguments={'url': document_url, 'page': rendered['page'] + 1})
                else:
                    if page is not None:
                        return ToolResult(status='error', error_code='invalid_arguments', evidence_kind='external',
                            error_stage='arguments', tool_name='read_web_media',
                            content='仅PDF支持page页码；图片请省略page或填null。',
                            sources=[ToolSource(url=error_source_url(final_url))]), []
                    description, source_url, coverage = '网页原始图片', final_url, 'web_image'
                mime, path = await self._store_bytes(data)
        except (ValueError, OSError, httpx.HTTPError, Image.DecompressionBombError, TimeoutError) as error:
            failure = self._media_error(None, error, source_url=url)
            return ToolResult(status='error', content=failure['reason'], error_code=failure['error_code'],
                error_stage='execution', tool_name='read_web_media', http_status=failure['http_status'],
                sources=[ToolSource(url=failure['source_url'], title='本次媒体读取来源')]
                if failure['source_url'] else [], evidence_kind='external'), []
        result = ToolResult(content=description+'已取得并验证，资产随本次观察登记，attachments提供场景资产引用，可用read_media回读。'
            '像素是否装入以当前请求的图片清单为准；动图只采用首帧，未识别或未覆盖的细节不能当作已核实。',
            sources=[ToolSource(url=source_url, title=description)], evidence_kind='external', coverage=coverage,
            source_next_call=source_next_call)
        files = [{'mime_type': mime, 'path': path, 'locator': source_url, 'description': description}]
        return result, files

    async def recent_usage(self, scene_id: str, asset_ids: Sequence[str], *, through_rowid: int | None = None):
        """Recent means the existing bounded send window, not the asset's lifetime."""
        usage = await self.runtime.event_store.recent_media_sends(scene_id, bot_actor_id=self.runtime.bot_actor_id,
            limit=self.runtime.config.conversation_outbound_limit, through_rowid=through_rowid)
        return {asset_id: dict(usage.get(asset_id, {'last_sent_at': None, 'recent_send_count': 0,
                                                 'used_in_last_reply': False})) for asset_id in dict.fromkeys(asset_ids)}

    async def prepare_palette(self, scene_id: str, *, through_rowid: int | None = None) -> PreparedMediaContext:
        """The operator's fixed, scoped catalog with stable asset references."""
        if not self.runtime.config.media_enabled:
            return {"blocks": [], "manifest": []}
        assets = await self.runtime.event_store.list_palette(scene_id,
            limit=self.runtime.config.media_palette_limit)
        usage = await self.recent_usage(scene_id, [asset['id'] for asset in assets], through_rowid=through_rowid)
        def rank(asset):
            record = usage[asset['id']]
            return (record['recent_send_count'] > 0, record['recent_send_count'],
                    asset.get('palette_order') if asset.get('palette_order') is not None else 10**9, asset['id'])
        assets = sorted(assets, key=rank)
        manifest = []
        for index, asset in enumerate(assets):
            description = asset["description"] or ""
            tags = list(asset["tags"] or [])
            name = "、".join(tags[:3]) if tags else description[:40]
            manifest.append({
                "asset_id": asset["id"], "ref": f"P{index+1:02d}",
                "name": name[:40], "description": description[:120],
                "description_truncated": len(description) > 120,
                "tags": tags, "source_event_id": asset["source_event_id"],
                "status": "catalog_only", **usage[asset['id']]})
        return {"blocks": [], "manifest": manifest}

    async def prepare_action(self, action):
        segments = action.segments
        images = {}
        sticker_ids = set()
        for segment in segments:
            if segment.type in {"image", "video", "audio"}:
                if not self.runtime.config.media_enabled:
                    raise ValueError("媒体能力已停用")
                asset, data = (await self.get_bytes(segment.asset_id, action.scene_id)
                               if segment.type == "image" else await self.get_file_bytes(segment.asset_id, action.scene_id))
                if segment.type == "video" and not (asset.get("mime_type") or "").startswith("video/"):
                    raise ValueError("视频段引用的素材不是视频")
                if segment.type == "audio" and not (asset.get("mime_type") or "").startswith("audio/"):
                    raise ValueError("音频段引用的素材不是音频")
                images[segment.asset_id] = "base64://" + base64.b64encode(data).decode()
                if segment.type == "image" and asset["curated"] and "表情包" in asset["tags"]:
                    sticker_ids.add(segment.asset_id)
        return action.model_copy(update={"segments": segments, "resolved_images": images,
                                         "resolved_sticker_ids": sticker_ids})
