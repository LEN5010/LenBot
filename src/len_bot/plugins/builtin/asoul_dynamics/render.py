"""Deterministic light card for one previously obtained source record."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from len_bot.cards.layout import text_lines
from len_bot.cards.tokens import THEME


def render_dynamic_card(record: dict, font_path: Path, *, kind: str = "动态") -> bytes:
    width, margin = 1080, THEME.margin
    fonts = {"title": ImageFont.truetype(str(font_path), 46),
             "body": ImageFont.truetype(str(font_path), 30),
             "meta": ImageFont.truetype(str(font_path), 23),
             "small": ImageFont.truetype(str(font_path), 19)}
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    member = (record.get("member") or {}).get("name") or ("A-SOUL 二创" if record.get("sourceDynamicId") else "A-SOUL")
    published = str(record.get("publishedAt") or "时间未知").replace("T", " ")
    text = str(record.get("contentText") or
               (f"类型：{record.get('contentType')}\n来源动态：{record.get('sourceDynamicId')}"
                if record.get("sourceDynamicId") else "（该动态没有可展示的正文）"))
    lines = text_lines(measure, text, fonts["body"], width - margin * 2 - 48)
    line_height = 46
    height = 270 + max(line_height, len(lines) * line_height) + 90
    image = Image.new("RGB", (width, height), THEME.canvas)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 16), fill=THEME.accent)
    draw.rounded_rectangle((margin, 42, width - margin, height - 34), radius=THEME.radius_outer,
                           fill=THEME.card, outline=THEME.border, width=2)
    draw.text((margin + 30, 74), member, font=fonts["title"], fill=THEME.ink)
    draw.text((margin + 32, 140), f"{kind} · {published}", font=fonts["meta"], fill=THEME.muted)
    y = 205
    for line in lines:
        draw.text((margin + 32, y), line, font=fonts["body"], fill=THEME.ink)
        y += line_height
    draw.line((margin + 30, height - 105, width - margin - 30, height - 105), fill=THEME.border, width=2)
    draw.text((margin + 32, height - 82), f"来源：{record.get('url') or record.get('sourceDynamicUrl') or '已配置动态源'}",
              font=fonts["small"], fill=THEME.muted)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
