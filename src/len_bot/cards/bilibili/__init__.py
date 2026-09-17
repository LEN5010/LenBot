"""The rich Bilibili push card: fetch, inline, fill, screenshot."""
from __future__ import annotations

from .context import build_card_context, collect_image_urls, load_template
from .models import (AdditionalCard, AuthorProfile, CardNotification, EngagementStats,
                     ForwardedContent, RichTextNode)
from .source import fetch_profile, inline_images

__all__ = ['AdditionalCard', 'AuthorProfile', 'CardNotification', 'EngagementStats',
           'ForwardedContent', 'RichTextNode', 'build_card_context', 'collect_image_urls',
           'fetch_profile', 'inline_images', 'load_template', 'render_notification']


async def render_notification(notification: CardNotification, renderer, *,
                              generated_at: float | None = None) -> bytes:
    """One notification in, one PNG out; pictures are inlined on the way."""
    images = await inline_images(collect_image_urls(notification))
    context = build_card_context(notification, generated_at=generated_at, images=images)
    return await renderer.render_template(load_template(), context)
