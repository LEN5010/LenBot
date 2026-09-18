"""One-page group configuration: assemble a projection and apply one root save."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from len_bot.config_edit import ConfigEditConflict
from len_bot.media.files import file_delivery_facts
from len_bot.runtime.attention_config import effective_attention, effective_sticker_preference, raise_attention_two_steps
from len_bot.runtime.capabilities import Capability, CapabilityGrant
from len_bot.runtime.sleep_policy import in_sleep_window
from len_bot.web.capability_status import CARDS


class GroupQuickValues(BaseModel):
    model_config = ConfigDict(extra='forbid')
    settings: dict
    send_file_principals: list[str] | None = None

    @field_validator('send_file_principals')
    @classmethod
    def real_accounts(cls, value):
        if value is None:
            return value
        cleaned = []
        for item in value:
            text = str(item).strip()
            if not text.isdigit() or text.startswith('0'):
                raise ValueError('文件申请者必须是真实 QQ 号')
            cleaned.append(text)
        if len(cleaned) != len(set(cleaned)):
            raise ValueError('文件申请者不能重复')
        return cleaned


class GroupQuickRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scene_id: str = Field(pattern=r'^group:[1-9][0-9]*$')
    baseline: dict
    values: GroupQuickValues


def _scene_send_file_grants(grants, scene_id):
    return [grant for grant in grants
            if grant.scene_id == scene_id and grant.principal_type == 'human'
            and Capability.SEND_FILE in grant.capabilities]


def _grant_public(grant):
    return {'grant_id': grant.grant_id, 'revision': grant.revision, 'principal_id': grant.principal_id,
            'enabled': grant.enabled, 'expires_at': grant.expires_at,
            'capabilities': [item.value for item in grant.capabilities]}


async def assemble(query, scene_id, *, joined=None):
    record = query.scene_settings(scene_id)
    root = query.runtime.config_store.current
    attention = effective_attention(root, scene_id)
    raised = raise_attention_two_steps(attention)
    grants = _scene_send_file_grants(root.access.capability_grants, scene_id)
    sleep = None
    if root.time:
        now = query.current_time()
        sleep = {'configured': root.time.sleep_start is not None,
                 'in_window': in_sleep_window(root.time, now),
                 'sleep_start': root.time.sleep_start, 'sleep_end': root.time.sleep_end}
    cards = []
    plugins = {item['id']: item for item in record['plugins']}
    settings = record['settings'] or {}
    scene_plugins = (settings.get('plugins') or {})
    for ident, title, implementations, _actions, entry in CARDS:
        owners = []
        for plugin_id, _tools in implementations:
            plugin = plugins.get(plugin_id)
            scene_row = scene_plugins.get(plugin_id)
            owners.append({
                'id': plugin_id,
                'name': plugin['name'] if plugin else plugin_id,
                'configured': bool(plugin and plugin['configured']),
                'globally_enabled': bool(plugin and plugin['enabled']),
                'in_scene': plugin_id in scene_plugins,
                'scene_enabled': bool(scene_row and scene_row.get('enabled')),
                'scene_config_schema': plugin['scene_config_schema'] if plugin else {},
                'missing': plugin is None,
            })
        cards.append({'id': ident, 'title': title, 'entry': entry, 'plugins': owners})
    delivery = file_delivery_facts(query.runtime, scene_id, None)
    participants = []
    raw = await query.runtime.event_store.load_scene_session(scene_id)
    if raw:
        from len_bot.scenes.models import SceneSession
        session = SceneSession.model_validate(raw)
        for actor_id, facts in session.participants.items():
            if actor_id.startswith('user:'):
                participants.append({'qq_uid': actor_id.removeprefix('user:'),
                                     'name': facts.display_name, 'role': facts.role})
    group_name = None
    joined_flag = None
    if joined:
        ident = scene_id.partition(':')[2]
        match = next((item for item in joined.get('items') or [] if str(item.get('group_id')) == ident), None)
        joined_flag = bool(match) if joined.get('complete') else None
        group_name = match.get('group_name') if match else None
    return {
        **record,
        'display_name': group_name or query.scene_label(scene_id)['display_name'],
        'joined': joined_flag,
        'discovery': None if joined is None else {
            'sampled_at': joined.get('sampled_at'), 'error': joined.get('error'),
            'complete': bool(joined.get('complete'))},
        'attention': {
            'global': {
                'observation_enabled': root.runtime.attention_observation_enabled,
                'observation_interval_seconds': root.runtime.attention_observation_interval_seconds,
                'keyword_cooldown_seconds': root.runtime.attention_keyword_cooldown_seconds,
            },
            'effective': attention.model_dump(),
            'raise_two_steps': raised,
        },
        # An exhausted allowance stops turns silently now, so the number it
        # stopped them on has to be readable somewhere.
        'allowance': await query.runtime.rate_limiter.status(scene_id),
        'expression': {'effective': effective_sticker_preference(root, scene_id)},
        'file_delivery': delivery,
        'send_file_grants': [_grant_public(grant) for grant in grants],
        'participants': participants,
        'sleep': sleep,
        'capability_cards': cards,
        'whitelist': list(root.access.qq_reply_whitelist),
        'shadow_mode': query.runtime.shadow_mode,
    }


def apply_send_file_grants(grants, scene_id, principals, baseline, operator_id):
    current = _scene_send_file_grants(grants, scene_id)
    public = [_grant_public(grant) for grant in current]
    if public != (baseline or []):
        raise ConfigEditConflict(('access', 'capability_grants', scene_id, 'send_file'))
    edited_ids = {grant.grant_id for grant in current}
    remaining = [grant for grant in grants if grant.grant_id not in edited_ids]
    by_principal = {grant.principal_id: grant for grant in current}
    wanted = list(dict.fromkeys(principals or []))
    for principal in wanted:
        stored = by_principal.pop(principal, None)
        if stored is None:
            remaining.append(CapabilityGrant(
                grant_id='g' + uuid.uuid4().hex, revision=1, operator_id=operator_id,
                principal_type='human', principal_id=principal, scene_id=scene_id,
                system_scope=None, capabilities=[Capability.SEND_FILE], expires_at=None,
                resource_policy=None, concurrency=None, enabled=True))
            continue
        capabilities = list(stored.capabilities)
        if Capability.SEND_FILE not in capabilities:
            capabilities.append(Capability.SEND_FILE)
        changed = (not stored.enabled) or capabilities != list(stored.capabilities)
        remaining.append(stored.model_copy(update={
            'capabilities': capabilities, 'enabled': True,
            'revision': stored.revision + (1 if changed else 0),
            'operator_id': operator_id if changed else stored.operator_id}))
    for stored in by_principal.values():
        capabilities = [item for item in stored.capabilities if item != Capability.SEND_FILE]
        if capabilities:
            remaining.append(stored.model_copy(update={
                'capabilities': capabilities, 'revision': stored.revision + 1, 'operator_id': operator_id}))
        elif stored.enabled:
            remaining.append(stored.model_copy(update={
                'enabled': False, 'revision': stored.revision + 1, 'operator_id': operator_id}))
        else:
            remaining.append(stored)
    return remaining
