"""Scoped image IO and native model input preparation. Never calls a model."""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import io
import json
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

from len_bot.media.models import MessageSegment, PreparedMediaContext, segment_text
from len_bot.media.store import PALETTE_UNCHANGED
from len_bot.tools.http import fetch_public
from len_bot.tools.results import ToolResult, ToolSource

MAX_IMAGE_BYTES = 10_000_000
MAX_IMAGE_PIXELS = 20_000_000
FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}


def validate_image(data: bytes):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片为空或超过10MB上限")
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in FORMATS:
            raise ValueError("仅支持PNG、JPEG、WEBP和GIF")
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("图片像素超过上限")
        mime_type = FORMATS[image.format]
        image.verify()
    return mime_type


def prepare_image(data: bytes):
    validate_image(data)
    with Image.open(io.BytesIO(data)) as image:
        animated = getattr(image, "n_frames", 1) > 1
        image.seek(0)
        frame = ImageOps.exif_transpose(image).convert("RGBA")
        frame.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        background = Image.new("RGBA", frame.size, "white")
        background.alpha_composite(frame)
        output = io.BytesIO()
        background.convert("RGB").save(output, format="PNG")
        return output.getvalue(), animated, frame.width, frame.height


def image_block(data: bytes):
    return {"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(data).decode(), "detail": "high"}}


def render_palette(images):
    """A numbered contact sheet; the actual assets stay separate and sendable."""
    sheet = Image.new("RGB", (1600, 1400), "#eeeeee")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=24)
    for index, data in enumerate(images):
        x, y = index % 5 * 320, index // 5 * 350
        draw.rectangle((x+8, y+8, x+312, y+342), fill="white")
        draw.text((x+20, y+16), f"P{index+1:02d}", fill="#222222", font=font)
        if data is None:
            draw.text((x+60, y+180), "unavailable", fill="#777777", font=font)
            continue
        with Image.open(io.BytesIO(data)) as image:
            image.thumbnail((288, 282), Image.Resampling.LANCZOS)
            sheet.paste(image, (x+(320-image.width)//2, y+56+(282-image.height)//2))
    output = io.BytesIO()
    sheet.save(output, format="PNG")
    return output.getvalue()


class MediaService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.root = (Path(runtime.config.db_path).resolve().parent / "media").resolve()
        self._client = httpx.AsyncClient(timeout=15.0, follow_redirects=False)
        self._locks: dict[str, asyncio.Lock] = {}
        self._palette_cache = {}
        self._palette_locks: dict[str, asyncio.Lock] = {}
        self._io_slots = asyncio.Semaphore(3)

    async def close(self):
        await self._client.aclose()

    async def reset_cache(self):
        self._palette_cache.clear()
        self._palette_locks.clear()
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
            mime = await asyncio.to_thread(validate_image, data)
        except (OSError, Image.DecompressionBombError) as error:
            raise ValueError("图片内容无法验证") from error
        digest = hashlib.sha256(data).hexdigest()
        suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}[mime]
        path = self.root / f"{digest}.{suffix}"
        self.root.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temporary = self.root / f".{uuid.uuid4().hex}.tmp"
            await asyncio.to_thread(temporary.write_bytes, data)
            await asyncio.to_thread(temporary.replace, path)
        return digest, mime, str(path)

    async def get_bytes(self, asset_id, scene_id, *, include_disabled=False):
        async with self._locks.setdefault(asset_id, asyncio.Lock()), self._io_slots:
            asset = await self.runtime.event_store.get_media(asset_id, [scene_id, "global-safe"], include_disabled=include_disabled)
            if not asset:
                raise ValueError("图片不存在、已停用或不在本场景中")
            if asset["path"]:
                path = Path(asset["path"]).resolve()
                if not path.is_relative_to(self.root) or not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
                    raise ValueError("图片缓存不可用")
                data = await asyncio.to_thread(path.read_bytes)
                if hashlib.sha256(data).hexdigest() != asset["sha256"]:
                    raise ValueError("图片缓存完整性检查失败")
                return asset, data
            locator = asset["locator"]
            if locator.startswith("base64://"):
                if len(locator) > MAX_IMAGE_BYTES * 4 // 3 + 100:
                    raise ValueError("图片超过上限")
                data = base64.b64decode(locator.removeprefix("base64://"), validate=True)
            elif locator.startswith(("https://", "http://")):
                _, _, data = await fetch_public(self._client, locator, max_bytes=MAX_IMAGE_BYTES)
            else:
                raise ValueError("图片只有平台文件标识，没有可读取地址；不会读取任意本地路径")
            digest, mime, path = await self._store_bytes(data)
            event = await self.runtime.event_store.save_media_file(asset_id, asset["scope"], digest, mime, path)
            await self.runtime.commit_tool_observation(event)
            asset.update(sha256=digest, mime_type=mime, path=path)
            return asset, data

    async def upload(self, data, scope, description, tags):
        if scope != "global-safe" and not scope.startswith(("group:", "private:")):
            raise ValueError("请选择群聊、私聊或global-safe素材范围")
        digest, mime, path = await self._store_bytes(data)
        asset_id = "image_" + uuid.uuid4().hex
        event = await self.runtime.event_store.save_media_file(asset_id, scope, digest, mime, path,
            description=description, tags=tags, curated=True)
        await self.runtime.commit_tool_observation(event)
        return await self.runtime.event_store.get_media(asset_id, [scope])

    async def edit(self, asset_id, scope, description, tags, enabled, *, palette_order=PALETTE_UNCHANGED):
        event = await self.runtime.event_store.edit_media(asset_id, scope, description, tags, enabled,
            palette_order=palette_order)
        await self.runtime.commit_tool_observation(event)
        return await self.runtime.event_store.get_media(asset_id, [scope], include_disabled=True)

    async def _prepare_asset(self, asset_id, scene_id):
        asset, data = await self.get_bytes(asset_id, scene_id)
        prepared, animated, width, height = await asyncio.to_thread(prepare_image, data)
        return prepared, {"asset_id": asset_id, "status": "included", "source_event_id": asset["source_event_id"],
            "sha256": asset["sha256"], "coverage": "first_frame" if animated else "image", "width": width, "height": height}

    @staticmethod
    def _media_error(asset_id, error):
        return {"asset_id": asset_id, "status": "error",
            "reason": str(error) if isinstance(error, ValueError) else f"图片读取失败：{type(error).__name__}"}

    async def prepare_context_images(self, scene_id: str, asset_ids: Sequence[str], limit: int = 6) -> PreparedMediaContext:
        """Native image blocks, with explicit omissions and no hidden model call."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0 or limit > 6:
            raise ValueError("Image limit must be between 0 and 6")
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

    async def prepare_palette(self, scene_id: str) -> PreparedMediaContext:
        """A stable, scoped operator palette. Selection never depends on a message."""
        if not self.runtime.config.media_enabled:
            return {"blocks": [], "manifest": []}
        async with self._palette_locks.setdefault(scene_id, asyncio.Lock()):
            assets = await self.runtime.event_store.list_palette(scene_id)
            fingerprint = json.dumps([{key: asset[key] for key in (
                "id", "scope", "source_event_id", "sha256", "description", "tags", "enabled", "palette_order")}
                for asset in assets], ensure_ascii=False, sort_keys=True)
            cached = self._palette_cache.get(scene_id)
            if cached and cached[0] == fingerprint:
                return {"blocks": [image_block(cached[1])] if cached[1] else [],
                    "manifest": copy.deepcopy(cached[2])}
            manifest, images = [], []
            for index, asset in enumerate(assets):
                entry = {"asset_id": asset["id"], "ref": f"P{index+1:02d}",
                    "name": asset["description"][:40], "description": asset["description"][:40],
                    "tags": list(asset["tags"]), "source_event_id": asset["source_event_id"], "sha256": asset["sha256"]}
                try:
                    prepared, details = await self._prepare_asset(asset["id"], scene_id)
                except (ValueError, OSError, httpx.HTTPError, Image.DecompressionBombError) as error:
                    entry.update(self._media_error(asset["id"], error))
                    images.append(None)
                else:
                    entry.update(details, block_index=0)
                    images.append(prepared)
                manifest.append(entry)
            sheet = await asyncio.to_thread(render_palette, images) if any(images) else None
            if not any(item["status"] == "error" for item in manifest):
                self._palette_cache[scene_id] = (fingerprint, sheet, manifest)
            return {"blocks": [image_block(sheet)] if sheet else [], "manifest": copy.deepcopy(manifest)}

    async def prepare_action(self, action):
        segments = action.segments or [MessageSegment(type="text", text=action.content)]
        if action.segments and action.content != segment_text(action.segments):
            if any(segment.type == "image" for segment in segments):
                raise ValueError("含图片消息的拦截器必须同步修改segments")
            segments = [MessageSegment(type="text", text=action.content)]
        images = {}
        for segment in segments:
            if segment.type == "image":
                if not self.runtime.config.media_enabled:
                    raise ValueError("媒体能力已停用")
                _, data = await self.get_bytes(segment.asset_id, action.scene_id)
                images[segment.asset_id] = "base64://" + base64.b64encode(data).decode()
        return action.model_copy(update={"segments": segments, "resolved_images": images})
