"""Map one source record onto the shared Bilibili card contract.

The dynamics site already carries pictures, engagement counters and the repost
block, so the card is filled from the record we were given; only the footer's
UP counters need the public profile endpoint.
"""
from __future__ import annotations

from datetime import datetime

from len_bot.cards.bilibili import (AuthorProfile, CardNotification, EngagementStats,
                                    ForwardedContent)


def _count(value) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _urls(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _published(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0.0


def _forwarded(value) -> ForwardedContent | None:
    if not isinstance(value, dict):
        return None
    member = value.get('member') or {}
    return ForwardedContent(
        author_name=str(member.get('name') or ''),
        avatar_url=str(member.get('avatarUrl') or ''),
        text=str(value.get('contentText') or ''),
        image_urls=_urls(value.get('images')),
        title=str(value.get('title') or ''))


def notification_from_record(data: dict, profile: AuthorProfile) -> CardNotification:
    """One source record plus a fetched profile becomes one renderable card."""
    member = data.get('member') if isinstance(data.get('member'), dict) else {}
    uid = str(member.get('bilibiliUid') or '')
    # The record's own identity is the fallback when the profile lookup failed,
    # so a card still shows a name and a face instead of a bare UID.
    merged = profile.model_copy(update={
        'uid': profile.uid or uid,
        'name': profile.name or str(member.get('name') or ''),
        'avatar_url': profile.avatar_url or str(member.get('avatarUrl') or ''),
    })
    return CardNotification(
        kind='dynamic',
        uid=uid,
        author_name=str(member.get('name') or ''),
        text=str(data.get('contentText') or ''),
        url=str(data.get('url') or data.get('sourceDynamicUrl') or ''),
        image_urls=_urls(data.get('images')),
        content_id=str(data.get('dynamicId') or data.get('sourceDynamicId') or ''),
        published_at=_published(data.get('publishedAt')),
        author_profile=merged,
        stats=EngagementStats(
            like_count=_count(data.get('likeCount')),
            comment_count=_count(data.get('commentCount')),
            forward_count=_count(data.get('forwardCount'))),
        forwarded=_forwarded(data.get('orig')))
