"""Read one saved OneBot image position and cache its prepared pixels and description."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..configuration.chat import ImageSettings
from ..tools.http_read import fetch_public
from ..storage.store import ImageAsset, Store
from ..trials.replay_images import RecordedImages


class LookArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    message: str = Field(min_length=1)
    image: int = Field(default=1, gt=0)
    refresh: bool = False

    @field_validator("message")
    @classmethod
    def nonblank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must be a nonblank platform message ID")
        return value


LOOK_TOOL = {"type": "function", "function": {
    "name": "look",
    "description": "描述当前场景已保存消息里的第几张图片。message 是原平台消息 ID，image 从1开始按原段顺序计数；"
                   "refresh 仅重做已保存像素的视觉描述，不重新下载。",
    "parameters": LookArguments.model_json_schema(),
}}


def image_url(data: dict[str, object]) -> str:
    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"image has no readable HTTP(S) url; original data={repr(data)[:300]}")
    return url


async def prepare_pixels(data: bytes, settings: ImageSettings) -> tuple[bytes, bool, int, int]:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", __name__, "prepare",
        str(settings.max_bytes), str(settings.max_pixels), str(settings.max_dimension),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await process.communicate(data)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise ValueError(f"image preparation failed: {stderr.decode(errors='replace')[-500:]}")
    header, jpeg = stdout.split(b"\n", 1)
    metadata = json.loads(header)
    return jpeg, metadata["animated"], metadata["width"], metadata["height"]


def _display(scene: str, arguments: LookArguments, asset: ImageAsset, *,
             pixels_reused: bool, description_reused: bool, pixels_source: str) -> str:
    result = {
        "scene": scene, "platform_message_id": arguments.message, "image": arguments.image,
        "jpeg_bytes": len(asset.jpeg), "width": asset.width, "height": asset.height,
        "animated_first_frame_only": asset.animated,
        "fetched_at": datetime.fromtimestamp(asset.fetched_at, timezone.utc).isoformat(),
        "description": asset.description, "description_model": asset.description_model,
        "described_at": (None if asset.described_at is None else
                         datetime.fromtimestamp(asset.described_at, timezone.utc).isoformat()),
        "pixels_reused": pixels_reused, "description_reused": description_reused,
        "pixels_source": pixels_source,
    }
    return json.dumps(result, ensure_ascii=False) + "\n描述来自准备后的 JPEG；动图只分析第一帧。"


async def execute_look(store: Store, scene: str, arguments: LookArguments,
                       settings: ImageSettings, *, model_name: str,
                       describe: Callable[[ImageAsset], Awaitable[str]],
                       recording: RecordedImages | None = None, fake_ip_networks: tuple) -> str:
    message = store.find_message(scene, arguments.message)
    if message is None:
        raise ValueError(f"当前场景没有平台消息 {arguments.message}")
    pictures = [segment for segment in message.segments if segment.type == "image"]
    if arguments.image > len(pictures):
        raise ValueError(f"消息 {arguments.message} 只有 {len(pictures)} 张图片，不能读取第 {arguments.image} 张")
    asset = store.image(scene, arguments.message, arguments.image)
    pixels_reused = asset is not None
    pixels_source = 'cache'
    if asset is None:
        if arguments.refresh:
            raise ValueError("该图片尚未保存像素；先用 refresh=false 读取，再重新描述")
        original = store.platform_image(scene, arguments.message, arguments.image)
        deadline = asyncio.timeout(settings.timeout_seconds)
        try:
            async with deadline:
                if original is not None:
                    body = original[1]
                    fetched_at = store.now()
                    pixels_source = 'saved_platform_original'
                elif recording is not None:
                    body, fetched_at = recording.image(arguments.message, arguments.image)
                    pixels_source = 'frozen_original'
                else:
                    url = image_url(pictures[arguments.image - 1].data)
                    _, _, body = await fetch_public(
                        url, settings.timeout_seconds, lambda _type, _prefix: settings.max_bytes,
                        fake_ip_networks=fake_ip_networks,
                    )
                    fetched_at = store.now()
                    pixels_source = 'http_response'
                jpeg, animated, width, height = await prepare_pixels(body, settings)
                asset = ImageAsset(jpeg=jpeg, width=width, height=height, animated=animated,
                                   fetched_at=fetched_at)
                store.save_image(scene, arguments.message, arguments.image, asset)
        except TimeoutError as error:
            if deadline.expired():
                raise TimeoutError(f"look image loading and preparation exceeded {settings.timeout_seconds} seconds") from error
            raise
    description_reused = asset.description is not None and not arguments.refresh
    if not description_reused:
        description = await describe(asset)
        described_at = store.now()
        store.save_image_description(scene, arguments.message, arguments.image,
                                     description, model_name, described_at)
        asset = store.image(scene, arguments.message, arguments.image)
    return _display(scene, arguments, asset, pixels_reused=pixels_reused,
                    description_reused=description_reused, pixels_source=pixels_source)


def _worker() -> None:
    from len_bot.media.images import prepare_image

    max_bytes, max_pixels, max_dimension = map(int, sys.argv[2:5])
    jpeg, animated, width, height = prepare_image(
        sys.stdin.buffer.read(max_bytes + 1),
        max_bytes=max_bytes, max_pixels=max_pixels, max_dimension=max_dimension,
    )
    metadata = {"animated": animated, "width": width, "height": height}
    sys.stdout.buffer.write(json.dumps(metadata).encode("ascii") + b"\n" + jpeg)


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "prepare":
        raise SystemExit("look image worker expects prepare and three limits")
    try:
        _worker()
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
