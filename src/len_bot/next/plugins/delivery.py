"""Prepare ordered plugin text, mentions and original images before any send is attempted."""
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
import re
from typing import assert_never

import regex

from ..image_assets import OriginalImage, inspect_image
from ..platform.messages import Segment
from ..plugin import Content, Image, Mention, Text


@dataclass(frozen=True)
class Part:
    segments: list[Segment]
    image: OriginalImage | None
    description: str | None


async def prepare_parts(plugin: str, content: Sequence[Content], max_chars: int,
                        reply_to: str | None) -> list[Part]:
    if not content:
        raise ValueError("插件发送内容不能为空")
    result: list[Part] = []
    segments: list[Segment] = []
    image = None
    description = None
    length = 0

    def flush():
        nonlocal segments, image, description, length
        if segments:
            prefix = [Segment("reply", {"id": reply_to})] if not result and reply_to is not None else []
            result.append(Part(prefix + segments, image, description))
            segments, image, description, length = [], None, None, 0

    for item in content:
        if isinstance(item, Text):
            if not item.text:
                raise ValueError("插件Text内容不能为空")
            clusters = regex.findall(r"\X", item.text)
            offset = 0
            while offset < len(clusters):
                end = min(offset + max_chars - length, len(clusters))
                segments.append(Segment("text", {"text": "".join(clusters[offset:end])}))
                length += end - offset
                offset = end
                if length == max_chars:
                    flush()
        elif isinstance(item, Mention):
            if item.user != "all" and re.fullmatch(r"[a-z][a-z0-9_-]*:[^:\s/\\]+", item.user) is None:
                raise ValueError(f"插件Mention须为实际账号或all：{item.user!r}")
            segments.append(Segment("mention", {"user": item.user}))
        elif isinstance(item, Image):
            if not item.description.strip():
                raise ValueError("插件图片须提供非空内容说明")
            metadata = await asyncio.to_thread(inspect_image, item.data)
            if image is not None:
                flush()
            image = OriginalImage(item.data, *metadata)
            description = f"插件 {plugin} 提供的图片说明：{item.description}"
            segments.append(Segment("image", {"summary": description}))
        else:
            assert_never(item)
    flush()
    return result
