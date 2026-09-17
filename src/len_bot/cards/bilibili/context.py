"""Turn one CardNotification into the substitution dict template.html wants.

Two contracts worth keeping straight:

* **Escaping.** The template marks each field either `| e` or `| safe`, and
  Jinja runs with autoescape off, so every `| safe` value must arrive already
  escaped. That escaping happens here and nowhere else.
* **Offline.** The renderer aborts http/https, so a remote URL left in the
  context renders as a broken image. Callers resolve pictures to data URIs
  first (`collect_image_urls` lists what to fetch) and pass the map in; a URL
  that could not be fetched becomes '' and the template simply omits it.
"""
from __future__ import annotations

import base64
import html
import io
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from .models import CardNotification, RichTextNode

CARD_WIDTH_PX = 1200
MAX_CARD_IMAGES = 9
DISPLAY_TZ = timezone(timedelta(hours=8))
TEMPLATE_PATH = Path(__file__).resolve().parent / 'template.html'
BRAND_LOGO_PATH = Path(__file__).resolve().parent / 'logo.png'
BRAND_NAME = '爱驼推送'

KIND_LABELS = {'dynamic': '动态', 'video': '新视频', 'live': '正在直播'}


def load_template() -> str:
    return TEMPLATE_PATH.read_text(encoding='utf-8')


def safe_http_url(raw_value) -> str:
    value = str(raw_value or '').strip()
    if value.startswith('//'):
        value = f'https:{value}'
    if not value.startswith(('http://', 'https://')):
        return ''
    return value


def _compact_decimal(value: float) -> str:
    return f'{value:.{1 if value >= 10 else 2}f}'.rstrip('0').rstrip('.')


def format_card_number(raw_value) -> str:
    """None reads as `--`; a number we do have reads as 1.2万 / 3.3亿."""
    if raw_value is None:
        return '--'
    try:
        value = max(0, int(raw_value))
    except (TypeError, ValueError):
        return '--'
    if value >= 100_000_000:
        return f'{_compact_decimal(value / 100_000_000)}亿'
    if value >= 10_000:
        return f'{_compact_decimal(value / 10_000)}万'
    return str(value)


def _escaped_text_with_breaks(value) -> str:
    return html.escape(str(value or ''), quote=True).replace('\n', '<br>')


def render_rich_text_html(nodes: list[RichTextNode], fallback_text: str, images: dict) -> str:
    """The body: inline emoji and links survive, everything else is escaped."""
    if not nodes:
        return _escaped_text_with_breaks(fallback_text)
    parts = []
    for node in nodes:
        text = str(node.text or '')
        if node.kind == 'emoji':
            source = images.get(safe_http_url(node.image_url), '')
            if source:
                parts.append('<img class="inline-emoji" src="{}" alt="{}">'.format(
                    html.escape(source, quote=True), html.escape(text, quote=True)))
                continue
        if node.kind == 'link':
            url = safe_http_url(node.url)
            if url:
                parts.append('<a href="{}">{}</a>'.format(
                    html.escape(url, quote=True), _escaped_text_with_breaks(text)))
                continue
        parts.append(_escaped_text_with_breaks(text))
    return ''.join(parts)


def build_qr_data_uri(url: str) -> str:
    safe_url = safe_http_url(url)
    if not safe_url:
        return ''
    import qrcode

    code = qrcode.QRCode(version=None, box_size=8, border=2)
    code.add_data(safe_url)
    code.make(fit=True)
    output = io.BytesIO()
    code.make_image(fill_color='#242424', back_color='white').save(output, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')


@lru_cache(maxsize=1)
def build_brand_logo_data_uri() -> str:
    try:
        payload = BRAND_LOGO_PATH.read_bytes()
    except OSError:
        return ''
    if not payload:
        return ''
    kind = 'image/jpeg' if payload.startswith(b'\xff\xd8') else 'image/png'
    return f'data:{kind};base64,' + base64.b64encode(payload).decode('ascii')


def _format_timestamp(value, *, include_seconds: bool = False) -> str:
    seconds = int(value or 0)
    if seconds <= 0:
        return '--'
    pattern = '%Y-%m-%d %H:%M:%S' if include_seconds else '%Y-%m-%d %H:%M'
    return datetime.fromtimestamp(seconds, DISPLAY_TZ).strftime(pattern)


def collect_image_urls(notification: CardNotification) -> list[str]:
    """Every remote picture this card would show, for the caller to prefetch."""
    profile = notification.author_profile
    urls = [*notification.image_urls, notification.cover_url,
            profile.avatar_url, profile.pendant_url,
            notification.additional_card.cover_url]
    urls.extend(node.image_url for node in notification.rich_nodes if node.kind == 'emoji')
    if notification.forwarded is not None:
        urls.append(notification.forwarded.avatar_url)
        urls.extend(notification.forwarded.image_urls)
        urls.extend(node.image_url for node in notification.forwarded.rich_nodes
                    if node.kind == 'emoji')
    seen, result = set(), []
    for item in urls:
        url = safe_http_url(item)
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def build_card_context(notification: CardNotification, *, generated_at: float | None = None,
                       images: dict | None = None) -> dict:
    resolved = images or {}
    source = lambda raw: resolved.get(safe_http_url(raw), '')
    generated = generated_at if generated_at is not None else time.time()
    profile = notification.author_profile
    author_name = profile.name or notification.author_name or notification.uid
    profile_uid = profile.uid or notification.uid

    pictures = [item for item in (source(url) for url in notification.image_urls) if item][:MAX_CARD_IMAGES]
    cover = source(notification.cover_url)
    if notification.kind in {'video', 'live'} and cover and not pictures:
        pictures = [cover]

    additional = notification.additional_card.model_dump()
    for key in ('kind', 'title', 'subtitle', 'status', 'badge'):
        additional[key] = html.escape(str(additional.get(key) or ''), quote=True)
    additional['badge'] = additional['badge'] or additional['status']
    additional['cover_url'] = source(additional.get('cover_url'))
    additional['url'] = safe_http_url(additional.get('url'))

    forwarded = None
    if notification.forwarded is not None:
        repost = notification.forwarded
        forwarded_images = [item for item in (source(url) for url in repost.image_urls) if item][:MAX_CARD_IMAGES]
        forwarded = {
            'author_name': html.escape(repost.author_name, quote=True),
            'avatar_url': source(repost.avatar_url),
            'title': html.escape(repost.title, quote=True),
            'body_html': render_rich_text_html(repost.rich_nodes, repost.text, resolved),
            'images': forwarded_images,
            'image_count': len(forwarded_images),
        }
        # A repost's pictures belong to the quoted block, not to both grids.
        carried = set(forwarded_images)
        pictures = [item for item in pictures if item not in carried]

    is_comment = notification.kind == 'comment'
    comment_context = None
    if is_comment:
        owner = notification.comment_resource_owner_name
        source_text = f'在{owner + "的" if owner else "该"}{notification.comment_resource_kind or "内容"}'
        if notification.comment_resource_title:
            source_text += f'《{notification.comment_resource_title}》'
        comment_context = {
            'action': html.escape(notification.comment_action_text or '发表了评论', quote=True),
            'source': html.escape(source_text + '下', quote=True),
        }
        kind_label = '回复评论' if notification.comment_action_text == '回复了评论' else '发表评论'
    else:
        kind_label = KIND_LABELS.get(notification.kind, 'B站通知')

    body_fallback = notification.text
    if not body_fallback and notification.kind == 'dynamic':
        body_fallback = '发布了新动态'

    return {
        'card_width': CARD_WIDTH_PX,
        'brand_name': BRAND_NAME,
        'brand_logo_data_uri': build_brand_logo_data_uri(),
        'kind': notification.kind,
        'is_comment': is_comment,
        'kind_label': kind_label,
        'title': html.escape(notification.title or '', quote=True),
        'body_html': render_rich_text_html(notification.rich_nodes, body_fallback, resolved),
        'published_at': _format_timestamp(
            notification.comment_created_at if is_comment else notification.published_at),
        'generated_at': _format_timestamp(generated, include_seconds=True),
        'url': safe_http_url(notification.url),
        'qr_data_uri': '' if is_comment else build_qr_data_uri(notification.url),
        'images': pictures,
        'image_count': len(pictures),
        'author': {
            'uid': html.escape(profile_uid, quote=True),
            'name': html.escape(author_name, quote=True),
            'avatar_url': source(profile.avatar_url),
            'pendant_url': source(profile.pendant_url),
            'likes': format_card_number(profile.total_likes),
            'following': format_card_number(profile.following),
            'follower': format_card_number(profile.follower),
        },
        'stats': {
            'like': format_card_number(notification.stats.like_count),
            'comment': format_card_number(notification.stats.comment_count),
            'forward': format_card_number(notification.stats.forward_count),
        },
        'stats_note': '数据暂未刷新' if notification.stats_are_fallback else '',
        'comment_context': comment_context,
        'content_heading': '评论内容' if is_comment else '本条内容',
        'profile_heading': '评论者资料' if is_comment else 'UP资料',
        'show_engagement': not is_comment,
        'additional': additional if additional.get('kind') else None,
        'forwarded': forwarded,
    }
