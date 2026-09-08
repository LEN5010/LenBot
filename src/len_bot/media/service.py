"""Scoped image IO and native model input preparation. Never calls a model."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from len_bot.media.models import PreparedMediaContext
from len_bot.media.store import PALETTE_UNCHANGED
from len_bot.tools.http import fetch_public
from len_bot.tools.pdf_reader import MAX_PDF_BYTES, read_pdf
from len_bot.tools.results import ToolResult, ToolSource

FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}


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
        background.convert("RGB").save(output, format="PNG")
        return output.getvalue(), animated, frame.width, frame.height


def image_block(data: bytes):
    return {"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(data).decode(), "detail": "high"}}


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
        await asyncio.to_thread(temporary.write_bytes, data)
        await asyncio.to_thread(temporary.replace, path)
        return mime, str(path)

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

    async def upload(self, data, scope, description, tags):
        if scope != "global-safe" and not scope.startswith(("group:", "private:")):
            raise ValueError("请选择群聊、私聊或global-safe素材范围")
        mime, path = await self._store_bytes(data)
        asset_id = "image_" + uuid.uuid4().hex
        event = await self.runtime.event_store.save_media_file(asset_id, scope, mime, path,
            description=description, tags=tags, curated=True)
        await self.runtime.commit_tool_observation(event)
        return await self.runtime.event_store.get_media(asset_id, [scope])

    async def save_generated(self, data: bytes, scene_id: str, source_event_id: str, description: str):
        """Store a command image as source-owned output, never as a curated example."""
        mime, path = await self._store_bytes(data)
        asset_id = 'image_' + uuid.uuid4().hex
        event = await self.runtime.event_store.save_generated_media(
            asset_id, scene_id, source_event_id, mime, path, description)
        await self.runtime.commit_tool_observation(event)
        return await self.runtime.event_store.get_media(asset_id, [scene_id])

    async def edit(self, asset_id, scope, description, tags, enabled, *, palette_order=PALETTE_UNCHANGED):
        event = await self.runtime.event_store.edit_media(asset_id, scope, description, tags, enabled,
            palette_order=palette_order)
        await self.runtime.commit_tool_observation(event)
        return await self.runtime.event_store.get_media(asset_id, [scope], include_disabled=True)

    async def _prepare_asset(self, asset_id, scene_id):
        asset, data = await self.get_bytes(asset_id, scene_id)
        prepared, animated, width, height = await asyncio.to_thread(prepare_image, data,
            max_bytes=self.runtime.config.media_max_image_bytes,
            max_pixels=self.runtime.config.media_max_image_pixels,
            max_dimension=self.runtime.config.media_max_dimension)
        return prepared, {"asset_id": asset_id, "status": "included", "source_event_id": asset["source_event_id"],
            "coverage": "first_frame" if animated else "image", "width": width, "height": height}

    @staticmethod
    def _media_error(asset_id, error):
        return {"asset_id": asset_id, "status": "error",
            "reason": str(error) if isinstance(error, ValueError) else f"图片读取失败：{type(error).__name__}"}

    async def prepare_context_images(self, scene_id: str, asset_ids: Sequence[str], *, limit: int) -> PreparedMediaContext:
        """Native image blocks, with explicit omissions and no hidden model call."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= self.runtime.config.max_context_images:
            raise ValueError("Image limit must be within the configured image window")
        result: PreparedMediaContext = {"blocks": [], "manifest": []}
        for asset_id in dict.fromkeys(asset_ids):
            if not self.runtime.config.media_enabled:
                result["manifest"].append({"asset_id": asset_id, "status": "omitted", "reason": "media_disabled"})
                continue
            if len(result["blocks"]) >= limit:
                result["manifest"].append({"asset_id": asset_id, "status": "omitted", "reason": "image_limit"})
                continue
            try:
                prepared, manifest = await self._prepare_asset(asset_id, scene_id)
            except (ValueError, OSError, httpx.HTTPError, Image.DecompressionBombError) as error:
                result["manifest"].append(self._media_error(asset_id, error))
                continue
            manifest["block_index"] = len(result["blocks"])
            result["manifest"].append(manifest)
            result["blocks"].append(image_block(prepared))
        return result

    async def read_media(self, asset_id: str, scene_id: str) -> ToolResult:
        """Read and validate the asset; the active model receives its pixels later."""
        if not self.runtime.config.media_enabled:
            return ToolResult(status="unsupported", content="媒体能力已停用", error_code="media_disabled")
        try:
            _, manifest = await self._prepare_asset(asset_id, scene_id)
        except (ValueError, OSError, httpx.HTTPError, Image.DecompressionBombError) as error:
            return ToolResult.failure(self._media_error(asset_id, error)["reason"], "media_unavailable")
        return ToolResult(content=json.dumps(manifest, ensure_ascii=False), attachments=[asset_id],
            sources=[ToolSource(event_id=manifest["source_event_id"], title="原始图片")],
            coverage=manifest["coverage"], evidence_kind="retrieval")

    async def read_web_media(self, url: str, page: int | None = None):
        """Prepare public pixels; the toolkit commits files and observation together."""
        if not self.runtime.config.media_enabled:
            return ToolResult(status='unsupported', content='媒体能力已停用', error_code='media_disabled'), []
        final_url, headers, data = await fetch_public(self._client, url, max_bytes=MAX_PDF_BYTES)
        media_type = headers.get('content-type', '').split(';')[0].lower()
        if media_type == 'application/pdf' or data.startswith(b'%PDF-'):
            rendered = await read_pdf(data, page=1 if page is None else page)
            data = base64.b64decode(rendered['png_base64'], validate=True)
            description = f"PDF第{rendered['page']}页，共{rendered['page_count']}页"
            source_url, coverage = f"{final_url}#page={rendered['page']}", 'pdf_page'
        else:
            if page is not None:
                raise ValueError('仅PDF支持page页码参数')
            description, source_url, coverage = '网页原始图片', final_url, 'web_image'
        mime, path = await self._store_bytes(data)
        result = ToolResult(content=description+'；像素将进入当前模型，未识别或未覆盖的细节不能当作已核实。',
            sources=[ToolSource(url=source_url, title=description)], evidence_kind='external', coverage=coverage)
        files = [{'mime_type': mime, 'path': path, 'locator': source_url, 'description': description}]
        return result, files

    async def prepare_palette(self, scene_id: str) -> PreparedMediaContext:
        """The operator's fixed, scoped catalog with stable asset references."""
        if not self.runtime.config.media_enabled:
            return {"blocks": [], "manifest": []}
        assets = await self.runtime.event_store.list_palette(scene_id,
            limit=self.runtime.config.media_palette_limit)
        manifest = [
            {"asset_id": asset["id"], "ref": f"P{index+1:02d}",
             "name": asset["description"][:40], "description": asset["description"][:40],
             "tags": list(asset["tags"]), "source_event_id": asset["source_event_id"],
             "status": "catalog_only"}
            for index, asset in enumerate(assets)
        ]
        return {"blocks": [], "manifest": manifest}

    async def prepare_action(self, action):
        segments = action.segments
        images = {}
        sticker_ids = set()
        for segment in segments:
            if segment.type == "image":
                if not self.runtime.config.media_enabled:
                    raise ValueError("媒体能力已停用")
                asset, data = await self.get_bytes(segment.asset_id, action.scene_id)
                images[segment.asset_id] = "base64://" + base64.b64encode(data).decode()
                if asset["curated"] and "表情包" in asset["tags"]:
                    sticker_ids.add(segment.asset_id)
        return action.model_copy(update={"segments": segments, "resolved_images": images,
                                         "resolved_sticker_ids": sticker_ids})
