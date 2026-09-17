"""What one Bilibili push card is made of.

These are the template's data contract, not a Bilibili API mirror: a plugin
fills them from whatever endpoint it already polls, and `context.py` turns
them into the substitution dict `template.html` expects.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CardKind = Literal['dynamic', 'video', 'live', 'comment']


class RichTextNode(BaseModel):
    """One run of a dynamic's body: plain text, an inline emoji, or a link."""
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['text', 'emoji', 'link'] = 'text'
    text: str = ''
    image_url: str = ''
    url: str = ''


class AuthorProfile(BaseModel):
    """The footer's UP block.

    `x/web-interface/card` answers all of this in one unsigned request, so
    None here means "not fetched", which the card prints as `--` rather than
    as a zero it cannot stand behind.
    """
    model_config = ConfigDict(extra='forbid', strict=True)
    uid: str = ''
    name: str = ''
    avatar_url: str = ''
    pendant_url: str = ''
    total_likes: int | None = None
    following: int | None = None
    follower: int | None = None
    fetched_at: float = 0.0


class EngagementStats(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    like_count: int = Field(default=0, ge=0)
    comment_count: int = Field(default=0, ge=0)
    forward_count: int = Field(default=0, ge=0)


class AdditionalCard(BaseModel):
    """The embedded link tile: a reservation, a video, or a live recommendation."""
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: str = ''
    title: str = ''
    subtitle: str = ''
    status: str = ''
    badge: str = ''
    cover_url: str = ''
    url: str = ''


class ForwardedContent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    author_name: str = ''
    avatar_url: str = ''
    text: str = ''
    rich_nodes: list[RichTextNode] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)
    title: str = ''


class CardNotification(BaseModel):
    """One renderable push, whatever produced it."""
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: CardKind
    uid: str
    author_name: str = ''
    title: str = ''
    url: str = ''
    text: str = ''
    rich_nodes: list[RichTextNode] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)
    cover_url: str = ''
    content_id: str = ''
    published_at: float = 0.0
    author_profile: AuthorProfile = Field(default_factory=AuthorProfile)
    stats: EngagementStats = Field(default_factory=EngagementStats)
    additional_card: AdditionalCard = Field(default_factory=AdditionalCard)
    forwarded: ForwardedContent | None = None
    # A push whose counters could not be refreshed still renders; the card
    # says so instead of showing a stale number as current.
    stats_are_fallback: bool = False
    # Comment-only fields; the card hides its QR, brand and footer for these.
    comment_created_at: float = 0.0
    comment_resource_owner_name: str = ''
    comment_resource_kind: str = ''
    comment_resource_title: str = ''
    comment_action_text: str = ''
