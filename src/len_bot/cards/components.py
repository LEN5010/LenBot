"""Small drawing helpers; business templates choose their own layout."""
from __future__ import annotations

from .tokens import LightThemeV1, THEME


def card(draw, box, *, fill=THEME.card, outline=THEME.border, radius=THEME.radius_inner, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def header(draw, fonts, *, margin: int, title: str, subtitle: str = "", theme: LightThemeV1 = THEME):
    draw.text((margin, 48), title, font=fonts["title"], fill=theme.ink)
    if subtitle:
        draw.text((margin, 112), subtitle, font=fonts["meta"], fill=theme.muted)
