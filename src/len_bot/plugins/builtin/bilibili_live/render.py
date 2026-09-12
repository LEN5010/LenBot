"""Deterministic source-bound live card; no network or model calls."""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from len_bot.cards.components import card
from len_bot.cards.layout import text_lines
from len_bot.cards.tokens import THEME

from .client import LiveSample


def render_live(sample: LiveSample, *, font_path: Path, timezone: str = "Asia/Shanghai", width: int = 1080) -> bytes:
    unit = lambda value: round(value * width / 1080)
    fonts = {name: ImageFont.truetype(str(font_path), unit(size)) for name, size in
             (("title", 52), ("body", 32), ("meta", 24), ("footer", 20))}
    zone = ZoneInfo(timezone)
    height = unit(520)
    image = Image.new("RGB", (width, height), THEME.canvas)
    draw = ImageDraw.Draw(image)
    margin = unit(THEME.margin)
    card(draw, (margin, margin, width - margin, height - margin), fill=THEME.card,
         outline=THEME.border, radius=unit(THEME.radius_outer), width=unit(2))
    draw.rounded_rectangle((margin, margin, width - margin, unit(190)), radius=unit(THEME.radius_outer), fill=THEME.soft)
    draw.text((margin + unit(28), unit(58)), "LIVE NOW", font=fonts["meta"], fill=THEME.accent_deep)
    draw.text((margin + unit(28), unit(103)), sample.member, font=fonts["title"], fill=THEME.ink)
    title = text_lines(draw, sample.title or "正在直播", fonts["body"], width - margin * 2 - unit(56))
    y = unit(235)
    title_lines = title[:4]
    if len(title) > 4:
        title_lines[-1] = title_lines[-1].rstrip() + "…"
    for line in title_lines:
        draw.text((margin + unit(28), y), line, font=fonts["body"], fill=THEME.ink)
        y += unit(42)
    started = sample.started_at or "未提供"
    if sample.started_at:
        try:
            started = datetime.fromisoformat(sample.started_at.replace("Z", "+00:00")).astimezone(zone).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    draw.text((margin + unit(28), height - unit(132)), f"开播：{started} · 采样：{datetime.fromtimestamp(sample.sampled_at, zone):%H:%M:%S}",
              font=fonts["footer"], fill=THEME.muted)
    draw.text((margin + unit(28), height - unit(92)), sample.url, font=fonts["footer"], fill=THEME.accent_deep)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
