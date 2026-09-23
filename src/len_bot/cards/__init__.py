"""Public card drawing and offline rendering helpers for trusted plugins."""

from pathlib import Path

from .layout import split_pages, text_lines, measure_text
from .tokens import LightThemeV1, THEME
from .components import card
from .html_render import HtmlCardRenderer
from .models import CardRenderMetadata, CardSource

__all__ = ["LightThemeV1", "split_pages", "text_lines", "measure_text", "CardSource", "CardRenderMetadata"]

# Keep the existing packaged font without depending on a plugin's directory.
BUNDLED_CARD_FONT = Path(__file__).resolve().parent.parent / 'plugins' / 'builtin' / 'asoul_calendar' / 'resources' / 'font.ttf'

__all__ += ['THEME', 'card', 'HtmlCardRenderer', 'BUNDLED_CARD_FONT']
