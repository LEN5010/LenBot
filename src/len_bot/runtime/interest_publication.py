"""Interest sends retain public source identity in the existing delivery ledger."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from len_bot.actions.models import InterestPublication
from len_bot.runtime.capabilities import Capability, CapabilitySubject
from len_bot.memory.interests import InterestStore
from len_bot.tools.results import ToolResult


async def publication_for(store, interest_id, revision):
    interests = InterestStore(store)
    item = await interests.get(interest_id)
    if (item is None or item.status != 'active' or item.revision != revision
            or item.valid_until is not None and item.valid_until <= store.clock()
            or item.record_type == 'research_intent'):
        raise ValueError('候选兴趣已改变、过期、撤回或仅为研究意向')
    if set(item.source_observation_ids) != await interests.public_observation_ids(item.source_observation_ids):
        raise ValueError('候选没有可核对的匿名公共来源')
    urls = set()
    for ident in item.source_observation_ids:
        row = await (await store._db.execute('SELECT result_json FROM tool_observations WHERE id=?', (ident,))).fetchone()
        result = ToolResult.model_validate_json(row[0])
        urls.update(source.url for source in result.sources if source.url)
    return item, InterestPublication(interest_id=item.id, revision=item.revision,
        source_result_ids=item.source_observation_ids, resource_urls=sorted(urls))


def scene_permission(store, scene_id):
    authority = store.capability_authority
    if authority is None:
        raise ValueError('能力授权服务不可用')
    subject = CapabilitySubject('plugin', 'interest_share', scene_id, None)
    permission = authority.check(Capability.INTEREST_SHARE, subject, now=store.clock())
    if not permission.allowed:
        raise ValueError(permission.reason)
    root = authority.config_store.current
    scene = root.scenes.get(scene_id)
    entry = scene.plugins.get('interest_share') if scene else None
    plugin = root.plugins.get('interest_share')
    if not (scene and scene.enabled and entry and entry.enabled and plugin and plugin.enabled and root.time):
        raise ValueError('本群兴趣分享或业务时区未配置')
    return entry.parsed_config, root.time


async def check_publication(store, scene_id, publication, *, action_id=None):
    """Called again inside begin_delivery_attempt's write transaction."""
    config, settings = scene_permission(store, scene_id)
    item, current = await publication_for(store, publication.interest_id, publication.revision)
    if current != publication:
        raise ValueError('分享来源关系已改变')
    if config.topics and item.topic not in config.topics:
        raise ValueError('候选不在本群配置的分享主题中')
    now = store.clock()
    day = datetime.fromtimestamp(now, ZoneInfo(settings.timezone)).replace(hour=0, minute=0, second=0, microsecond=0)
    used, last = 0, None
    # Exact stored resource URLs and interest identities are business relations;
    # no content hashes or simulated sends are used as delivery evidence.
    rows = await (await store._db.execute("""SELECT payload,timestamp FROM events WHERE scene_id=?
        AND event_type='DELIVERY_ATTEMPTED' AND json_extract(payload,'$.interest_publication') IS NOT NULL""",
        (scene_id,))).fetchall()
    for raw, at in rows:
        attempt = json.loads(raw)
        if attempt['action_id'] == action_id:
            continue
        fact = await store.delivery_fact(attempt['action_id'], scene_id)
        if fact and fact[0] not in {'sent', 'unknown'}:
            continue
        old = InterestPublication.model_validate(attempt['interest_publication'])
        if old.interest_id == publication.interest_id and old.revision == publication.revision:
            raise ValueError('本群已送达或尚未确认这条兴趣的发送结果')
        if set(old.resource_urls) & set(publication.resource_urls):
            raise ValueError('本群已分享该公共资源或发送结果尚未确认')
        if day.timestamp() <= at < (day + timedelta(days=1)).timestamp():
            used += 1
        last = max(last or at, at)
    if used >= config.daily_limit:
        raise ValueError('本群今日主动分享额度已用完')
    if last is not None and now - last < config.cooldown_seconds:
        raise ValueError('本群仍在主动分享冷却期')
