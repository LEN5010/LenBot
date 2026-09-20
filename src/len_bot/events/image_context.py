"""Read exact image discussion links from immutable, scene-scoped chat records."""
from dataclasses import dataclass, field
import json
from typing import Literal

from len_bot.events.models import Event


@dataclass(frozen=True)
class ImageDiscussionLink:
    source_event_id: str
    target_event_id: str
    kind: Literal['reply', 'response_source', 'covered_response_source']


@dataclass
class ImageDiscussion:
    events: list[Event] = field(default_factory=list)
    links: list[ImageDiscussionLink] = field(default_factory=list)
    images: dict[str, list[str]] = field(default_factory=dict)
    limited: bool = False


def _real_chat(alias: str) -> str:
    # Only this module's fixed SQL aliases enter the expression. Values remain
    # bound parameters, and every join separately keeps the scene and row order.
    human = (f"{alias}.event_type IN ('GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED') "
             f"AND {alias}.actor_id LIKE 'user:%' AND {alias}.actor_id!=:bot "
             f"AND substr({alias}.actor_id,6) GLOB '[1-9]*' "
             f"AND substr({alias}.actor_id,6) NOT GLOB '*[^0-9]*'")
    receipt = (f"{alias}.event_type='MESSAGE_SENT' AND {alias}.actor_id=:bot "
               f"AND json_extract({alias}.payload,'$.origin_mode')='live' "
               f"AND COALESCE(json_extract({alias}.payload,'$.delivery_status'),'sent')='sent' "
               f"AND COALESCE(json_extract({alias}.payload,'$.delivery_unknown'),0)=0 "
               f"AND json_type({alias}.payload,'$.message_id') IN ('text','integer') "
               f"AND trim(CAST(json_extract({alias}.payload,'$.message_id') AS TEXT))!=''")
    return (f"COALESCE(json_extract({alias}.metadata,'$.simulated'),0)=0 "
            f"AND COALESCE(json_extract({alias}.metadata,'$.conversation_excluded'),0)=0 "
            f"AND COALESCE(json_extract({alias}.payload,'$.origin_mode'),'live') NOT IN ('simulated','shadow') "
            f"AND (({human}) OR ({receipt}))")


def _columns(alias: str) -> str:
    return ','.join(f'{alias}.{name}' for name in
                    ('id', 'event_type', 'scene_id', 'actor_id', 'timestamp', 'payload', 'rowid', 'metadata'))


def _quoted_target(child: str, target: str) -> str:
    return f"""{target}.rowid=(SELECT q.rowid FROM events q
        WHERE q.scene_id={child}.scene_id AND q.rowid<{child}.rowid AND {_real_chat('q')}
          AND CAST(json_extract(q.payload,'$.message_id') AS TEXT)=CAST(
            CASE WHEN {child}.event_type='MESSAGE_SENT' THEN json_extract({child}.payload,'$.reply_to')
                 ELSE json_extract({child}.payload,'$.reply_to_message_id') END AS TEXT)
        ORDER BY q.rowid DESC LIMIT 1)"""


async def read_image_discussion(store, scene_id: str, source_event_ids: list[str], through_rowid: int,
                                *, bot_actor_id: str, limit: int) -> ImageDiscussion:
    """Locate raw discussion, not statements classified as true corrections.

    The existing retrieval limit bounds ancestor events and later human replies
    separately. Each selected reply may bring its quoted receipt and source;
    final message and pixel capacity still belongs to the normal assembler.
    """
    if type(limit) is not int or limit < 1 or type(through_rowid) is not int or through_rowid < 0:
        raise ValueError('Image discussion requires a positive retrieval limit and a read cutoff')
    result = ImageDiscussion()
    if not source_event_ids:
        return result
    params = {'scene': scene_id, 'cutoff': through_rowid, 'bot': bot_actor_id,
              'ids': json.dumps(list(dict.fromkeys(source_event_ids)))}
    async def rows(sql, **values):
        return await (await store._db.execute(sql, {**params, **values})).fetchall()

    seeds = await rows(f"""SELECT {_columns('e')} FROM events e
        WHERE e.scene_id=:scene AND e.rowid<=:cutoff AND {_real_chat('e')}
          AND e.event_type IN ('GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED')
          AND e.id IN (SELECT value FROM json_each(:ids))
          AND (json_extract(e.payload,'$.reply_to_message_id') IS NOT NULL OR
               EXISTS (SELECT 1 FROM json_each(e.metadata,'$.media') m WHERE json_extract(m.value,'$.type')='image'))
        ORDER BY e.rowid""")
    events = {row[0]: Event.model_validate(store._retrieval_event(row)) for row in seeds}
    if not events:
        return result
    links: list[ImageDiscussionLink] = []

    async def ancestors(frontier: list[str], allowance: int):
        added = 0
        visited = set()
        while frontier:
            requested = [ident for ident in frontier if ident not in visited]
            if not requested:
                return
            visited.update(requested)
            found = await rows(f"""WITH requested AS (
                SELECT * , rowid AS source_rowid FROM events WHERE scene_id=:scene AND rowid<=:cutoff
                  AND id IN (SELECT value FROM json_each(:requested)))
                SELECT c.id,'reply',{_columns('t')} FROM events c JOIN events t ON {_quoted_target('c', 't')}
                  WHERE c.id IN (SELECT id FROM requested)
                UNION ALL
                SELECT c.id,'response_source',{_columns('t')} FROM requested c JOIN events t
                  ON t.id=json_extract(c.payload,'$.origin_event_id') AND t.scene_id=c.scene_id
                  AND t.rowid<c.source_rowid AND {_real_chat('t')}
                  WHERE c.event_type='MESSAGE_SENT'
                UNION ALL
                SELECT c.id,'covered_response_source',{_columns('t')} FROM requested c
                  JOIN json_each(c.payload,'$.covered_source_event_ids') s
                  JOIN events t ON t.id=s.value AND t.scene_id=c.scene_id
                    AND t.rowid<c.source_rowid AND {_real_chat('t')}
                  WHERE c.event_type='MESSAGE_SENT'""", requested=json.dumps(requested))
            frontier = []
            # Reply and primary-source edges precede merged-response sources.
            found.sort(key=lambda row: ({'reply':0,'response_source':1,'covered_response_source':2}[row[1]], -row[8]))
            for row in found:
                target = Event.model_validate(store._retrieval_event(row[2:]))
                if target.id not in events:
                    if added >= allowance:
                        result.limited = True
                        continue
                    events[target.id] = target
                    frontier.append(target.id)
                    added += 1
                link = ImageDiscussionLink(row[0], target.id, row[1])
                if link not in links:
                    links.append(link)

    async def image_locations():
        candidates = {item['asset_id'] for event in events.values() for item in event.metadata.get('media', [])
                      if item.get('type') == 'image' and item.get('asset_id')}
        if not candidates:
            return {}
        assets = await rows("""SELECT id FROM media_assets WHERE scope IN (:scene,'global-safe')
            AND id IN (SELECT value FROM json_each(:assets))""", assets=json.dumps(sorted(candidates)))
        allowed = {row[0] for row in assets}
        return {event.id: list(dict.fromkeys(item['asset_id'] for item in event.metadata.get('media', [])
                    if item.get('type') == 'image' and item.get('asset_id') in allowed))
                for event in events.values() if any(item.get('type') == 'image' and item.get('asset_id') in allowed
                    for item in event.metadata.get('media', []))}

    def keep_image_paths(images):
        kept = set(images)
        while True:
            linked = {link.source_event_id for link in links if link.target_event_id in kept}
            if linked <= kept:
                return kept
            kept.update(linked)

    await ancestors(list(events), limit)
    images = await image_locations()
    if not images:
        return result
    connected = keep_image_paths(images)
    events = {ident: event for ident, event in events.items() if ident in connected}
    links = [link for link in links if link.source_event_id in connected and link.target_event_id in connected]
    asset_ids = sorted({asset for assets in images.values() for asset in assets})

    def same_image(alias):
        return f"""EXISTS (SELECT 1 FROM json_each({alias}.metadata,'$.media') m
            WHERE json_extract(m.value,'$.type')='image'
              AND json_extract(m.value,'$.asset_id') IN (SELECT value FROM json_each(:assets)))"""

    # Select human replies first, not an arbitrary page of Bot receipts. A busy
    # image thread must not spend its whole reply allowance on old Bot output.
    seen = set(events)
    remaining = limit
    while remaining:
        found = await rows(f"""WITH image_origins AS MATERIALIZED (
                SELECT e.id,e.rowid AS source_rowid FROM events e WHERE e.scene_id=:scene AND e.rowid<=:cutoff AND {_real_chat('e')}
                  AND (e.id IN (SELECT value FROM json_each(:known)) OR {same_image('e')})),
            targets AS MATERIALIZED (
                SELECT t.*,t.rowid AS target_rowid,CAST(json_extract(t.payload,'$.message_id') AS TEXT) AS message_key
                FROM events t WHERE t.scene_id=:scene AND t.rowid<=:cutoff AND {_real_chat('t')}
                  AND (t.id IN (SELECT id FROM image_origins) OR
                    (t.event_type='MESSAGE_SENT' AND (
                      json_extract(t.payload,'$.origin_event_id') IN (SELECT id FROM image_origins WHERE source_rowid<t.rowid) OR
                      EXISTS (SELECT 1 FROM json_each(t.payload,'$.covered_source_event_ids') s
                        WHERE s.value IN (SELECT id FROM image_origins WHERE source_rowid<t.rowid))))))
            SELECT {_columns('c')} FROM targets target JOIN events c
              ON c.scene_id=:scene AND CAST(json_extract(c.payload,'$.reply_to_message_id') AS TEXT)=target.message_key
            JOIN events t ON t.rowid=target.target_rowid AND {_quoted_target('c', 't')}
            WHERE c.scene_id=:scene AND c.rowid<=:cutoff AND {_real_chat('c')}
              AND c.event_type IN ('GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED')
              AND c.id NOT IN (SELECT value FROM json_each(:seen))
            ORDER BY c.rowid DESC LIMIT :count""", seen=json.dumps(sorted(seen)),
            known=json.dumps(sorted(connected)), assets=json.dumps(asset_ids), count=remaining + 1)
        if not found:
            break
        if len(found) > remaining:
            result.limited = True
        selected = [Event.model_validate(store._retrieval_event(row)) for row in found[:remaining]]
        remaining -= len(selected)
        seen.update(event.id for event in selected)
        events.update((event.id, event) for event in selected)
        await ancestors([event.id for event in selected], 3 * len(selected))
        # New reply branches are relevant only if their stored links reach one
        # of the original image identities; unrelated quoted images stay out.
        locations = await image_locations()
        images = {ident: [asset for asset in assets if asset in asset_ids]
                  for ident, assets in locations.items() if any(asset in asset_ids for asset in assets)}
        connected = keep_image_paths(images)
        events = {ident: event for ident, event in events.items() if ident in connected}
        links = [link for link in links if link.source_event_id in connected and link.target_event_id in connected]
        seen.update(events)
    if remaining == 0:
        # There may also be a later link through the last selected reply. This
        # is a bounded related-context read, not an exhaustive-history claim.
        result.limited = True
    result.events = sorted(events.values(), key=lambda event: event.metadata['_rowid'])
    result.links = links
    result.images = images
    return result
