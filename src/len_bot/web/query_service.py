"""RuntimeQueryService (ADR-0022): the single read facade over the runtime.

Control Plane routes NEVER reach into runtime internals (`event_store._db`,
`scene_manager._actors`, `plugin_host._plugins`) — every read goes through
here, so the Dashboard is decoupled from Runtime implementation details.
Mutating interventions use explicit operator events and proposal submission.
"""

import json
import copy
import time
import re
from len_bot.runtime.platform_actions import actions_for
from len_bot.cognition.budget import ReservationPolicy
from len_bot.cognition.models import AnswerBasis
from len_bot.memory.history import HISTORY_SOURCES_AVAILABLE_SQL
from len_bot.events.models import Event, EventType
from len_bot.tools.results import ToolResult, DisplayedRange, error_message
from typing import Optional
from len_bot.scenes.models import SceneSession
from len_bot.memory.store import MEMORY_COLUMNS, memory_from_row
from len_bot.memory.interests import InterestStore
from len_bot.execution.protocol import OCCUPYING_STATES
from len_bot.actions.models import receipt_delivery_status


class RuntimeQueryService:
    def __init__(self, runtime):
        self.runtime = runtime
        self._joined_groups = {'sampled_at': None, 'items': [], 'error': None, 'complete': False}

    async def joined_groups(self, *, refresh=False):
        """OneBot get_group_list overlay; failure keeps the last successful sample."""
        cached = self._joined_groups
        if not refresh and cached['sampled_at'] and self.current_time() - cached['sampled_at'] < 30:
            return cached
        adapter = getattr(self.runtime, '_onebot_adapter', None)
        if adapter is None:
            return {**cached, 'error': cached['error'] or 'OneBot 适配器尚未启动'}
        try:
            payload = await adapter.call_api('get_group_list')
            rows = payload.get('data') if isinstance(payload, dict) else None
            if payload.get('status') != 'ok' or payload.get('retcode') not in (0, None) or not isinstance(rows, list):
                raise ValueError(str(payload.get('wording') or payload.get('message') or 'get_group_list 未返回群列表'))
            items = []
            for row in rows:
                if not isinstance(row, dict) or row.get('group_id') in (None, ''):
                    continue
                items.append({'group_id': str(row['group_id']), 'group_name': row.get('group_name') or str(row['group_id'])})
            self._joined_groups = {'sampled_at': self.current_time(), 'items': items, 'error': None, 'complete': True}
        except Exception as error:
            self._joined_groups = {**cached, 'sampled_at': cached['sampled_at'] or self.current_time(),
                                   'error': str(error)[:300], 'complete': cached['complete']}
        return self._joined_groups

    def current_time(self) -> float:
        return self.runtime.event_store.clock()

    async def capability_status(self, scene_id=None, requester=None):
        from len_bot.web.capability_status import capability_status
        return await capability_status(self, scene_id, requester)

    def settings_draft(self, domain):
        root = self.runtime.config_store.current
        runtime_fields = {
            'persona': ('character_context', 'identity_name', 'identity_core', 'identity_persona',
                        'conversation_style', 'address_names'),
            'character_references': ('character_reference_assets',),
            'attention': tuple(self.attention_settings()),
            'runtime': tuple(self.runtime_settings()['settings']),
        }
        if domain in runtime_fields:
            keys = runtime_fields[domain]
            saved = {key: root.runtime.model_dump()[key] for key in keys}
            effective = {key: self.runtime.config.model_dump()[key] for key in keys}
            apply = 'live' if domain in {'persona', 'attention', 'character_references'} else 'field_dependent'
        elif domain in {'access', 'resources', 'time', 'members'}:
            saved = root.model_dump()[domain]
            effective = saved if domain in {'access', 'resources'} else None
            apply = 'live' if domain in {'access', 'resources'} else 'restart_consumers'
        else:
            raise KeyError(domain)
        return {'saved': saved, 'baseline': copy.deepcopy(saved), 'effective': effective,
                'apply': apply, 'requires_restart': self.runtime.restart_required}

    async def list_voice_examples(self, scene_id=None):
        return await self.runtime.event_store.list_voice_examples(scene_id)

    async def _rows(self, sql, params=()):
        cursor = await self.runtime.event_store._db.execute(sql, params)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row)) for row in await cursor.fetchall()]

    async def _page(self, select, source, params, order, page, page_size):
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("page must be positive; page_size must be 1..100")
        total = (await self._rows("SELECT COUNT(*) AS total " + source, params))[0]["total"]
        items = await self._rows(select + " " + source + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                                 [*params, page_size, (page-1)*page_size])
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def _public(value):
        # Public diagnostics never expose provider continuation or storage paths.
        private = {"api_key", "password", "password_hash", "authorization", "access_token", "refresh_token",
                   "token", "path", "locator", "base64", "checkpoint_data", "checkpoint_json", "messages_json",
                   "continuation", "extra_content", "provider_private", "reasoning_content", "read_result_ranges"}
        if isinstance(value, dict):
            return {key: RuntimeQueryService._public(item) for key,item in value.items()
                    if key.lower() not in private and "signature" not in key.lower()}
        if isinstance(value, list):
            return [RuntimeQueryService._public(item) for item in value]
        if isinstance(value, str):
            return re.sub(r"data:[^\s]+;base64,[A-Za-z0-9+/=]+", "[媒体正文省略]", value)
        return value

    @staticmethod
    def scene_label(scene_id):
        kind, _, ident = scene_id.partition(":")
        label = {"group":"群聊", "private":"私聊"}.get(kind, "场景")
        return {"display_name": f"{label} {ident or scene_id}", "scene_type": kind if kind in {"group","private"} else "other"}

    @staticmethod
    def _observation_view(observation):
        excluded = set() if observation.status in {'error', 'unsupported'} else {'content'}
        return {**RuntimeQueryService._public(observation.model_dump(exclude=excluded)),
                'content_length': len(observation.content),
                'coverage_details': RuntimeQueryService._observation_coverage(observation)}

    @staticmethod
    def _observation_coverage(observation):
        """Read saved acquisition metadata, independently of a displayed text page."""
        known = {'browser_dom_text', 'browser_pixels', 'sampled_video_frames_and_optional_audio', 'audio_asr_selected_segment'}
        if observation.coverage not in known or observation.status not in {'ok', 'partial'}:
            return None
        try:
            data = json.loads(observation.content)
            if not isinstance(data, dict):
                raise ValueError('资料正文不是对象')
            if observation.coverage == 'browser_dom_text':
                from len_bot.browser.protocol import BrowserSnapshot
                snapshot = BrowserSnapshot.model_validate(data)
                return {'kind': 'browser_dom', 'page_ref': snapshot.page_ref,
                    'snapshot_revision': snapshot.snapshot_revision,
                    'text_offset': snapshot.text_offset, 'text_page_chars': len(snapshot.text),
                    'text_total_chars': snapshot.text_total_chars, 'text_next_offset': snapshot.text_next_offset,
                    'collection_truncated': snapshot.collection_truncated,
                    'collection_limit_chars': snapshot.collection_limit_chars,
                    'collected_text_saved': snapshot.collected_text is not None,
                    'saved_text_chars': len(snapshot.collected_text) if snapshot.collected_text is not None else len(snapshot.text)}
            if observation.coverage == 'browser_pixels':
                return {'kind': 'browser_pixels', 'page_ref': data['page_ref'], 'asset_id': data['asset_id'],
                    'area': data['area'], 'requested_snapshot_revision': data.get('requested_snapshot_revision')}
            if observation.coverage == 'sampled_video_frames_and_optional_audio':
                return {'kind': 'video_segment', **{key: data[key] for key in (
                    'execution_id', 'job_id', 'job_revision', 'bvid', 'cid', 'requested_start_ms',
                    'requested_end_ms', 'source_duration_ms', 'audio_start_ms', 'audio_end_ms')},
                    'frames': [{key: frame[key] for key in ('asset_id', 'source_time_ms', 'timestamp_basis')}
                               for frame in data['frames']], 'audio_asset_id': data.get('audio_asset_id')}
            return {'kind': 'audio_transcript', **{key: data[key] for key in (
                'execution_id', 'audio_asset_id', 'coverage_start_ms', 'coverage_end_ms', 'qualification')},
                'segment_count': len(data['segments'])}
        except (ValueError, KeyError, TypeError):
            return {'kind': 'unavailable', 'error': '此记录的覆盖元数据不符合对应工具的保存格式；原文仍可回读，不补造采集或已读范围。'}

    async def tool_results(self, scene_id, page=1, page_size=30):
        result = await self._page("SELECT id,event_id,tool_name,result_json,created_at", "FROM tool_observations WHERE scene_id=?",
                                  [scene_id], "created_at DESC,id DESC", page, page_size)
        for item in result["items"]:
            observation = ToolResult.model_validate_json(item.pop("result_json"))
            item["result"] = self._observation_view(observation)
            item["content_length"] = len(observation.content)
        return result

    async def tool_result(self, scene_id, result_id, offset=0, *, end=None, coordinate_unit='characters'):
        result = await self.runtime.event_store.read_tool_observation(result_id, [scene_id])
        if result is None:
            return None
        if offset < 0 or end is not None and end < offset:
            raise ValueError('资料范围必须满足 0 <= offset <= end')
        budget = self.runtime.config.tool_result_page_chars
        if coordinate_unit == 'characters' and end is None:
            shown = result.page(offset, budget)
        else:
            if coordinate_unit == 'characters':
                total = len(result.content)
                target = end
                if target > total:
                    raise ValueError('字符范围超出已保存正文')
                stop = min(target, offset + budget)
                content = result.content[offset:stop]
            elif coordinate_unit == 'records':
                try:
                    records = json.loads(result.content)
                except ValueError as error:
                    raise ValueError('此资料未保存为记录数组，不能按记录坐标读取；可明确选择查看正文') from error
                if result.tool_name == 'read_pending_wakes' and isinstance(records, dict):
                    records = records.get('items')
                if not isinstance(records, list):
                    raise ValueError('此资料没有可读取的记录数组，不能将记录坐标当作字符偏移')
                total = len(records)
                target = total if end is None else end
                if not offset <= target <= total:
                    raise ValueError('记录范围超出已保存数组')
                stop = offset
                while stop < target:
                    if len(json.dumps(records[offset:stop + 1], ensure_ascii=False)) > budget:
                        break
                    stop += 1
                if stop == offset and stop < target:
                    raise ValueError('单条保存记录超过当前页面字符上限；可明确选择查看正文，不自动更换坐标')
                content = json.dumps(self._public(records[offset:stop]), ensure_ascii=False)
            else:
                raise ValueError('资料坐标只接受 characters 或 records')
            source_truncated = result.source_truncated or (result.truncated and result.displayed_range is None)
            # This is an operator view over saved data, not a model read or a
            # replay of the original presentation. No tool is invoked here.
            shown = result.model_copy(update={'content':content, 'coordinate_unit':coordinate_unit,
                'displayed_range':DisplayedRange(start=offset, end=stop, total=total),
                'next_offset':stop if stop < target else None, 'next_call':None,
                'truncated':source_truncated or stop < total, 'source_truncated':source_truncated})
        page = self._public(shown.model_dump())
        page["content_length"] = len(result.content)
        page['coverage_details'] = self._observation_coverage(result)
        if result.source_next_call:
            page["source_next_call"] = self._public(result.source_next_call.model_dump())
            page["source_next_call_note"] = "源端下一批，仅位置未取得"
        return page

    async def workspace_artifact(self, scene_id, job_id, path, offset=0, limit=12000,
                                 execution_id=None):
        if offset < 0 or limit < 1 or limit > 100000:
            raise ValueError('invalid workspace artifact range')
        return await self.runtime.plugin_host.read_workspace_artifact(
            scene_id, job_id, path, offset, limit, execution_id=execution_id)

    async def workspace_artifacts(self, scene_id, job_id):
        return await self.runtime.plugin_host.list_workspace_artifacts(scene_id, job_id)

    async def workspace_artifact_bytes(self, scene_id, job_id, path, execution_id=None):
        return await self.runtime.plugin_host.read_workspace_artifact_bytes(
            scene_id, job_id, path, execution_id=execution_id)

    def attention_settings(self):
        return {key: getattr(self.runtime.config, key) for key in (
            "attention_keywords", "attention_observation_interval_seconds", "attention_observation_enabled",
            "attention_keyword_cooldown_seconds", "attention_focus_seconds", "conversation_recent_tokens",
            "addressed_debounce_idle_ms", "addressed_debounce_max_ms",
            "observing_debounce_idle_ms", "observing_debounce_max_ms",
            "scene_hourly_message_limit", "user_hourly_message_limit")}

    def access_settings(self):
        return self.runtime.config_store.current.access.model_dump()

    def time_settings(self):
        settings = self.runtime.config_store.current.time
        return settings.model_dump() if settings is not None else None

    def resource_settings(self):
        return self.runtime.config_store.current.resources.model_dump()

    def member_settings(self):
        return [member.model_dump() for member in self.runtime.config_store.current.members]

    def scene_settings(self, scene_id):
        if not re.fullmatch(r"group:[1-9][0-9]*", scene_id):
            raise ValueError("本群设置只接受 group:实际QQ群号")
        settings = self.runtime.config_store.current.scenes.get(scene_id)
        if settings is None:
            effect = "本群未配置，不产生新认知与发送；已有原话保留。"
        elif not settings.enabled:
            effect = "本群已停用，不产生新认知、命令回复或公告；已有原话保留。"
        elif settings.chat:
            effect = "普通成员可正常互动，命令与公告按本群选项执行。"
        elif settings.listen:
            effect = ("本群只跟读：普通成员闲聊不回话，但持续总结成历史与记忆；"
                      "QQ 白名单仍可正常提问，命令与公告按本群选项执行。")
        else:
            effect = "普通成员闲聊仅保存原话，不总结也不形成记忆；QQ 白名单仍可正常提问，命令与公告按本群选项执行。"
        if settings and settings.semantic_retrieval:
            effect += " 本群已允许向已配置的语义检索供应方发送认识与摘要文本。"
        if self.runtime.shadow_mode:
            effect += " 当前全局 Shadow 开启，不实际发送。"
        return {"scene_id": scene_id, "configured": settings is not None,
                "settings": settings.model_dump() if settings is not None else None,
                "effect": effect, "members": self.member_settings(),
                "plugins": [{key: item[key] for key in ("id", "name", "configured", "enabled", "scene_config_schema")}
                            for item in self.plugins()]}

    def maintenance_readiness(self, scene_id=None):
        snapshot = self.providers()
        profile = snapshot["routing"]["maintenance"] if snapshot["routing"] else None
        if profile is None:
            return {"configured": False, "ready": False, "reason": "未配置维护模型"}
        provider = next((item for item in snapshot["providers"] if item["id"] == profile["provider_id"]), None)
        ready = bool(provider and provider["enabled"] and provider["api_key_masked"])
        if ready and not self.runtime._running:
            ready = False
            reason = "运行时未启动"
        elif ready and scene_id and not self.runtime.scene_policy.maintenance_allowed(scene_id):
            ready = False
            reason = "本群未开放历史维护"
        else:
            reason = "已就绪" if ready else "维护供应商未启用或未设置密钥"
        return {"configured": True, "ready": ready, "running": self.runtime._running,
                "reason": reason}

    @staticmethod
    def _call(item):
        item = dict(item)
        usage = item.pop("usage_json")
        item["usage"] = json.loads(usage) if usage else None
        item["estimate"] = json.loads(item.pop("estimate_json"))
        if 'transport_json' in item:
            transport = item.pop('transport_json')
            item['transport'] = json.loads(transport) if transport is not None else None
        if 'request_record_json' in item:
            request_record = item.pop('request_record_json')
            item['request_record'] = json.loads(request_record) if request_record is not None else None
        return RuntimeQueryService._public(item)

    async def model_reservations(self, scene_id=None, *, subject=None):
        """Holds and settled usage for the current billing day, per account.

        Read-only: the numbers come from the same rows the reservation
        transaction wrote, so what the panel shows is what the next work will
        actually be checked against.

        Two different questions are kept apart.  How much of the day is already
        spoken for is an *account* fact, and it is what the held/used columns
        report.  What a new work may still be granted is an *admission* result:
        it depends on the subject, the capability, the scope and the policy in
        force for that request, not on the account alone.  So a day that ran
        under one named policy shows that policy's numbers as a resolved
        admission; a day with several cannot be reduced to one free balance,
        and the panel says which scopes to choose instead of showing a single
        number — which, with an unlimited default, would read as "unlimited"
        for an account that is in fact bounded by a finite grant.
        """
        store = self.runtime.event_store
        policy = store.reservation_policy or ReservationPolicy()
        timezone = store.billing_timezone
        day_key = policy.day_key(store.clock(), timezone)
        items = await store.list_job_reservations(scene_id, subject=subject, day_key=day_key, limit=100)
        authority = getattr(store, 'capability_authority', None)

        def named_policy(grant_reference):
            """Resolve a stored grant reference to the policy it points at."""
            if not grant_reference or authority is None:
                return None, False
            grant = next((item for item in authority.grants() if item.grant_id == grant_reference), None)
            try:
                return authority.policy_for_grant(grant), grant is not None
            except ValueError:
                # A reference whose policy no longer resolves is a refusal,
                # never a quiet fall back to the default's numbers.
                return None, True

        named_by_subject: dict[str, set] = {}
        rows = await self._rows(
            "SELECT DISTINCT subject, policy_name FROM usage_reservations WHERE day_key=?"
            " AND status IN ('held','settling','settled')"
            + (" AND subject=?" if subject is not None else ""),
            [day_key, subject] if subject is not None else [day_key])
        for row in rows:
            named_by_subject.setdefault(row['subject'], set()).add(row['policy_name'])
        accounts = {}
        for row in await store.account_reservation_totals(day_key, subject=subject):
            names = named_by_subject.get(row['subject'], set())
            distinct = {name for name in names if name}
            unresolvable = False
            resolved = None
            if len(distinct) == 1 and names == distinct:
                resolved, known = named_policy(next(iter(distinct)))
                unresolvable = known and resolved is None
            admission = {'state': 'mixed_scope_required' if len(distinct) > 1
                         else 'policy_unresolved' if unresolvable
                         else 'resolved' if resolved is not None
                         else 'default_policy',
                         'scope_required': sorted(distinct),
                         'detail': None}
            if admission['state'] == 'mixed_scope_required':
                admission['detail'] = ('本账户当日跨多个授予／策略，单一可用余量不能代表准入结果；'
                                       '请按主体、能力与范围分别查看，或明确选择要核对的范围')
            elif admission['state'] == 'policy_unresolved':
                admission['detail'] = '本账户引用的具名策略已不存在或失效，不能改用默认策略的数字'
            limit = None
            available = None
            if admission['state'] in {'resolved', 'default_policy'}:
                limit = (resolved.daily_user_token_limit if resolved is not None
                         else policy.daily_user_token_limit)
                available = None if limit is None else max(0, limit - row['held'] - row['used'])
            accounts[row['subject']] = {
                # Account facts: how much of the day is already spoken for.
                'subject': row['subject'], 'held': row['held'], 'used': row['used'],
                # Admission facts: which policy applies and what it leaves.
                'admission': admission,
                'daily_limit': limit,
                'grant_reference': next(iter(distinct)) if len(distinct) == 1 else None,
                'policy_reference_names': sorted(distinct),
                'available': available,
            }
        scene_subtotals = None
        if scene_id is not None:
            scene_subtotals = await store.account_reservation_totals(
                day_key, scene_id=scene_id, subject=subject)
        return {
            "day_key": day_key, "timezone": timezone,
            "limits": {"work_token_limit": policy.work_token_limit,
                       "daily_user_token_limit": policy.daily_user_token_limit,
                       "daily_scene_token_limit": policy.daily_scene_token_limit},
            "items": items, "accounts": sorted(accounts.values(), key=lambda row: row['subject']),
            "scene_subtotals": scene_subtotals,
        }

    async def model_usage(self, scene_id=None, *, since=None, until=None, purpose=None, status=None, job_id=None, page=1, page_size=30):
        source, params = "FROM model_calls WHERE 1=1", []
        for column,value in (("scene_id",scene_id),("purpose",purpose),("status",status),("job_id",job_id)):
            if value is not None:
                source += f" AND {column}=?"; params.append(value)
        for op,value in ((">=",since),("<=",until)):
            if value is not None:
                source += f" AND started_at{op}?"; params.append(value)
        select = """SELECT model_calls.*,
            (SELECT payload FROM traces WHERE id='trc_model_transport_' || model_calls.id
             AND kind='model_call_transport' AND ref_id=model_calls.id AND scene_id=model_calls.scene_id) AS transport_json"""
        result = await self._page(select, source, params, "started_at DESC,id DESC", page, page_size)
        result["items"] = [self._call(item) for item in result["items"]]
        known = "json_type(usage_json,'$.prompt_tokens') IN ('integer','real') AND json_type(usage_json,'$.completion_tokens') IN ('integer','real')"
        fields = ["purpose", "disposition", "COUNT(*) AS calls"]
        fields += [f"SUM(status='{name}') AS {name}" for name in ("completed","failed","cancelled","unconfirmed")]
        fields += [f"SUM(CASE WHEN {known} THEN 0 ELSE 1 END) AS unknown_usage"]
        for name,path in (("prompt_tokens","prompt_tokens"),("completion_tokens","completion_tokens"),
                          ("cached_tokens","prompt_tokens_details.cached_tokens"),("reasoning_tokens","completion_tokens_details.reasoning_tokens")):
            fields.append(f"SUM(CASE WHEN json_type(usage_json,'$.{path}') IN ('integer','real') THEN json_extract(usage_json,'$.{path}') ELSE 0 END) AS {name}")
        fields.append("SUM(json_extract(estimate_json,'$.input_tokens')) AS estimated_input_tokens")
        fields.append("SUM(CASE WHEN json_extract(usage_json,'$.type')='duration' AND json_type(usage_json,'$.seconds') IN ('integer','real') THEN json_extract(usage_json,'$.seconds') ELSE 0 END) AS audio_seconds")
        prompt = "json_extract(usage_json,'$.prompt_tokens')"
        cached = "json_extract(usage_json,'$.prompt_tokens_details.cached_tokens')"
        cache_type = "json_type(usage_json,'$.prompt_tokens_details.cached_tokens')"
        known_input = f"json_type(usage_json,'$.prompt_tokens') IN ('integer','real') AND {prompt}>=0"
        reported_cache = f"({known_input}) AND {cache_type} IN ('integer','real') AND {cached}>=0 AND {cached}<={prompt}"
        missing_cache = f"COALESCE({cache_type},'null')='null'"
        fields.extend([
            f"SUM(CASE WHEN {known_input} THEN 1 ELSE 0 END) AS known_input_calls",
            f"SUM(CASE WHEN {known_input} THEN {prompt} ELSE 0 END) AS known_input_tokens",
            f"SUM(CASE WHEN {reported_cache} THEN 1 ELSE 0 END) AS cache_reported_calls",
            f"SUM(CASE WHEN {missing_cache} THEN 1 ELSE 0 END) AS cache_missing_calls",
            f"SUM(CASE WHEN {reported_cache} THEN 0 WHEN {missing_cache} THEN 0 ELSE 1 END) AS cache_unusable_calls",
            f"SUM(CASE WHEN {reported_cache} THEN {prompt} ELSE 0 END) AS cache_reported_input_tokens",
            f"SUM(CASE WHEN {reported_cache} THEN {cached} ELSE 0 END) AS cache_reported_tokens",
        ])
        result["totals"] = await self._rows("SELECT " + ",".join(fields) + " " + source + " GROUP BY purpose,disposition ORDER BY purpose,disposition", params)
        # Use the same complete filtered ledger as totals, never just this page
        # or an average of per-call percentages. Missing cache usage is not zero.
        cache = {key: sum(row[key] for row in result['totals']) for key in (
            'calls', 'known_input_calls', 'known_input_tokens', 'cache_reported_calls',
            'cache_missing_calls', 'cache_unusable_calls', 'cache_reported_input_tokens', 'cache_reported_tokens')}
        cache['hit_rate'] = (cache['cache_reported_tokens'] / cache['cache_reported_input_tokens']
                             if cache['cache_reported_input_tokens'] > 0 else None)
        cache['input_coverage'] = (cache['cache_reported_input_tokens'] / cache['known_input_tokens']
                                   if cache['known_input_tokens'] > 0 else None)
        cache['call_coverage'] = cache['cache_reported_calls'] / cache['calls'] if cache['calls'] else None
        result['cache'] = cache
        order = "json_extract(request_order,'$.sequence')"
        classified = ("json_extract(request_order,'$.scope')='binding_instance_preparation' "
            f"AND json_type(request_order,'$.sequence')='integer' AND {order}>=1")
        phase_fields = [f"CASE WHEN {classified} THEN CASE WHEN {order}=1 THEN 'first' ELSE 'followup' END ELSE 'unknown' END AS phase",
            'COUNT(*) AS calls',
            f"SUM(CASE WHEN {known_input} THEN 1 ELSE 0 END) AS known_input_calls",
            f"SUM(CASE WHEN {known_input} THEN {prompt} ELSE 0 END) AS input_tokens",
            f"SUM(CASE WHEN {reported_cache} THEN 1 ELSE 0 END) AS cache_reported_calls",
            f"SUM(CASE WHEN {reported_cache} THEN {prompt} ELSE 0 END) AS cache_input_tokens",
            f"SUM(CASE WHEN {reported_cache} THEN {cached} ELSE 0 END) AS cached_tokens"]
        result['request_phases'] = await self._rows(
            "WITH filtered AS (SELECT usage_json,(SELECT json_extract(payload,'$.request_order') FROM traces "
            "WHERE id='trc_model_request_' || model_calls.id AND kind='model_call_request' "
            "AND ref_id=model_calls.id AND scene_id=model_calls.scene_id) AS request_order " + source + ") "
            "SELECT " + ",".join(phase_fields) + " FROM filtered GROUP BY phase ORDER BY phase", params)
        for phase in result['request_phases']:
            phase['mean_input_tokens'] = phase['input_tokens'] / phase['known_input_calls'] if phase['known_input_calls'] else None
            phase['cache_hit_rate'] = phase['cached_tokens'] / phase['cache_input_tokens'] if phase['cache_input_tokens'] else None
        result["filters"] = {"scene_id":scene_id,"since":since,"until":until,"purpose":purpose,"status":status,"job_id":job_id}
        result["cost"] = {"status":"unverified","amount":None,"reason":"尚未提供可核实的供应商价格或账单；未知 usage 不按零成本计入"}
        return result

    async def model_call(self, call_id, scene_id=None):
        rows = await self._rows("SELECT * FROM model_calls WHERE id=? AND (? IS NULL OR scene_id=?)", [call_id,scene_id,scene_id])
        if not rows:
            return None
        traces = await self._rows("""SELECT kind,payload FROM traces WHERE
            ((id=? AND kind='model_call_transport') OR (id=? AND kind='model_call_request'))
            AND ref_id=? AND scene_id=?""",
            ['trc_model_transport_' + call_id, 'trc_model_request_' + call_id, call_id, rows[0]['scene_id']])
        records = {trace['kind']: trace['payload'] for trace in traces}
        return self._call({**rows[0], 'transport_json': records.get('model_call_transport'),
                           'request_record_json': records.get('model_call_request')})

    async def history_batches(self, scene_id, page=1, page_size=30):
        result = await self._page(f"SELECT h.*,({HISTORY_SOURCES_AVAILABLE_SQL}) AS sources_available", "FROM history_batches h WHERE scene_id=?", [scene_id], "end_rowid DESC,end_offset DESC,id DESC", page,page_size)
        items = []
        for item in result["items"]:
            item = self.runtime.event_store._history_row(item)
            item['candidate_review'] = await self.history_candidate_review(item['id'], scene_id)
            if item.get("status") == "failed":
                traces = await self._rows("SELECT payload FROM traces WHERE ref_id=? AND kind='history_maintenance_error' ORDER BY created_at DESC LIMIT 1", [item["id"]])
                if traces:
                    try:
                        item["failure_detail"] = json.loads(traces[0]["payload"]).get("error")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
            items.append(item)
        result["items"] = items
        return result

    async def history_batch(self, batch_id):
        rows = await self._rows(f"SELECT h.*,({HISTORY_SOURCES_AVAILABLE_SQL}) AS sources_available FROM history_batches h WHERE id=?", [batch_id])
        if not rows:
            return None
        item = self.runtime.event_store._history_row(rows[0])
        item['candidate_review'] = await self.history_candidate_review(batch_id, item['scene_id'])
        if item.get("status") == "failed":
            traces = await self._rows("SELECT payload FROM traces WHERE ref_id=? AND kind='history_maintenance_error' ORDER BY created_at DESC LIMIT 1", [item["id"]])
            if traces:
                try:
                    item["failure_detail"] = json.loads(traces[0]["payload"]).get("error")
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
        return item

    async def history_candidate_review(self, batch_id, scene_id):
        rows = await self._rows("""SELECT id,payload FROM events WHERE scene_id=?
            AND event_type='REFLECTION_RECORDED' AND json_extract(payload,'$.batch_id')=?
            ORDER BY rowid DESC LIMIT 1""", [scene_id, batch_id])
        if not rows:
            return None
        payload = json.loads(rows[0]['payload'])
        candidates = payload.get('stale_memory_candidates')
        return {'event_id': rows[0]['id'], 'candidates': candidates,
                'adopted_operations': payload.get('memory_receipts'),
                'status': 'not_recorded' if candidates is None else 'needs_review' if candidates else 'none'}

    async def skills(self, scene_id=None, *, query="", page=1, page_size=30):
        source = """FROM skills s JOIN skill_versions v ON v.skill_id=s.id AND v.version=(
            SELECT MAX(p.version) FROM skill_versions p WHERE p.skill_id=s.id AND (? IS NULL OR s.scene_id=? OR p.scope='global-safe'))
            WHERE (?='' OR instr(lower(v.body_json),lower(?))>0)"""
        result = await self._page("SELECT s.id,s.scene_id,v.scope,v.version,s.author,s.updated_at,v.created_at,v.body_json,v.source_json", source,
                                  [scene_id,scene_id,query,query], "v.created_at DESC,s.id DESC",page,page_size)
        for item in result["items"]:
            body=json.loads(item.pop("body_json")); item["source"]=self._public(json.loads(item.pop("source_json")))
            item.update(name=body["name"],applicability=body["applicability"])
        return result

    async def skill_candidates(self, scene_id=None, *, status=None, page=1, page_size=30):
        result = await self._page("SELECT *", "FROM skill_candidates WHERE (? IS NULL OR scene_id=?) AND (? IS NULL OR status=?)",
                                  [scene_id,scene_id,status,status], "created_at DESC,id DESC",page,page_size)
        for item in result["items"]:
            item["candidate"]=self._public(json.loads(item.pop("candidate_json")))
            item["skip_reason"] = item["error"] if item["status"] == "skipped" else None
            if item["status"] == "skipped":
                item["error"] = None
            item['saved_versions'] = await self._rows('''SELECT v.version,v.scope,v.created_at
                FROM skill_versions v JOIN skills s ON s.id=v.skill_id
                WHERE v.skill_id=? AND s.scene_id=? AND json_extract(v.source_json,'$.candidate_id')=?
                AND json_extract(v.source_json,'$.job_id')=? AND json_extract(v.source_json,'$.job_revision')=?
                ORDER BY v.version''', [item['skill_id'], item['scene_id'], item['id'], item['job_id'], item['job_revision']])
        return result

    async def skill(self, skill_id, scene_id, version=None):
        result = await self.runtime.event_store.read_skill(skill_id,scene_id,version=version)
        if result is None:return None
        result["versions"] = await self._rows("""SELECT v.version,v.scope,v.created_at FROM skill_versions v JOIN skills s ON s.id=v.skill_id
            WHERE v.skill_id=? AND (s.scene_id=? OR v.scope='global-safe') ORDER BY v.version DESC""",[skill_id,scene_id])
        return self._public(result)

    def job_budget(self, job=None):
        """The run's published limits, plus what this work was actually granted.

        The published values describe a work created now; they are not what an
        older work was admitted under.  The work's own frozen snapshot is
        reported beside them, and a work with no recorded ceiling is shown as
        "未记录" rather than as "不设上限" — the two are different facts and
        only one of them is a grant.
        """
        config = self.runtime.config
        published = {"max_model_steps": config.job_max_steps, "max_tool_calls": config.job_max_tool_calls,
                "max_seconds": config.job_max_seconds, "context_tokens": config.job_context_tokens,
                "output_tokens": config.work_output_tokens,
                "effective_input_tokens": config.job_context_tokens - config.work_output_tokens,
                "compression_trigger": config.job_compress_trigger, "compression_target": config.job_compress_target}
        snapshot = (job or {}).get('budget') if job else None
        if snapshot is None:
            published['work_snapshot'] = None
            published['work_snapshot_note'] = (
                '该工作没有记录创建时的执行快照，其原上限无法还原；当前发布值另列为参考，不构成该工作的既有额度')
            return published
        has_ceiling = 'token_limit' in snapshot
        fields = {'token_limit': 'token_limit', 'max_model_steps': 'job_max_steps',
                  'max_tool_calls': 'job_max_tool_calls', 'max_seconds': 'job_max_seconds',
                  'context_tokens': 'job_context_tokens', 'output_tokens': 'work_output_tokens',
                  'maintenance_context_tokens': 'maintenance_context_tokens',
                  'maintenance_output_tokens': 'maintenance_output_tokens', 'deadline_at': 'deadline_at'}
        recorded = {key: snapshot[source] for key, source in fields.items() if source in snapshot}
        recorded['token_limit_state'] = 'recorded' if has_ceiling else 'unrecorded'
        if 'context_tokens' in recorded and 'output_tokens' in recorded:
            recorded['effective_input_tokens'] = recorded['context_tokens'] - recorded['output_tokens']
        published['work_snapshot'] = recorded
        return published

    @staticmethod
    def job_delivery(job):
        from len_bot.runtime.public_research import has_public_context
        return {'delivery_required': not has_public_context(job)}

    async def job_method_reads(self, job):
        """Pinned versions and saved presentations, never an assertion of use."""
        methods = {ident: {'skill_id': ident, 'version': version, 'observations': []}
                   for ident, version in job['skill_versions'].items()}
        if not methods:
            return []
        rows = await self._rows('''SELECT o.id,o.arguments_json,o.result_json FROM tool_observations o
            WHERE o.scene_id=? AND o.tool_name='read_skill'
            AND o.id IN (SELECT value FROM json_each(?)) ORDER BY o.created_at,o.id''',
            [job['scene_id'], json.dumps(job['result_ids'])])
        for row in rows:
            result = ToolResult.model_validate_json(row['result_json'])
            if result.status != 'ok':
                continue
            body = json.loads(result.content)
            arguments = json.loads(row['arguments_json'])
            method = methods.get(body['id'])
            if method is None or arguments['skill_id'] != body['id'] or body['version'] != method['version']:
                continue
            method['observations'].append({'result_id': row['id'],
                'provided_ranges': job['observation_reads'].get(row['id'], {})})
        return list(methods.values())

    async def jobs(self, scene_id=None, *, status=None, execution_status=None, query="", page=1, page_size=30):
        source = """FROM agent_jobs j JOIN tasks t ON j.id=t.id AND j.scene_id=t.scene_id WHERE (? IS NULL OR j.scene_id=?)
            AND (? IS NULL OR t.status=?) AND (?='' OR instr(lower(j.goal),lower(?))>0)"""
        params=[scene_id,scene_id,status,status,query,query]
        if execution_status:
            source += """ AND COALESCE(json_extract(j.result_json,'$.status'),CASE t.status WHEN 'pending' THEN 'pending'
                WHEN 'claimed' THEN 'pending' WHEN 'processing' THEN 'running' WHEN 'review_required' THEN 'interrupted'
                WHEN 'failed' THEN 'failed' WHEN 'cancelled' THEN 'cancelled' ELSE 'unknown' END)=?"""
            params.append(execution_status)
        result=await self._page("SELECT j.id,j.scene_id",source,params,"t.created_at DESC,j.id DESC",page,page_size)
        items=[]
        for item in result["items"]:
            job=await self.runtime.event_store.get_job(item["id"],item["scene_id"])
            if job['can_resume']:
                job['resume_issue']=self.runtime.job_resume_issue(job)
                job['can_resume']=job['resume_issue'] is None
            items.append({**self._public(job),**self.runtime.plugin_host.work_details(job),
                          **self.job_delivery(job),"budget":self.job_budget(job)})
        result["items"]=items
        return result

    async def job(self, job_id, scene_id=None):
        task=await self.get_task(job_id,scene_id)
        job=await self.runtime.event_store.get_job(job_id,task["scene_id"]) if task else None
        if job and job['can_resume']:
            job['resume_issue']=self.runtime.job_resume_issue(job)
            job['can_resume']=job['resume_issue'] is None
        if not job:
            return None
        executions = []
        for record in await self.runtime.event_store.executions_for_job(job['scene_id'], job_id):
            view = record.model_dump(mode='json', include={
                'execution_id', 'job_revision', 'worker_type', 'network_policy', 'state',
                'accepted_at', 'deadline_at', 'started_at', 'ended_at', 'returncode', 'last_sequence'})
            view['occupies_capacity'] = record.state in OCCUPYING_STATES
            view['termination_status'] = record.termination.status if record.termination else None
            executions.append(view)
        reservations = await self._rows(
            'SELECT subject,day_key,status,reserved_tokens,usage_tokens,estimated_tokens FROM usage_reservations WHERE job_id=? AND scene_id=?',
            [job_id, job['scene_id']])
        followups=await self._page(
            "SELECT j.id,j.goal,j.revision,t.status,t.created_at,json_extract(t.payload,'$.reused_work.revision') AS source_revision",
            "FROM agent_jobs j JOIN tasks t ON j.id=t.id AND j.scene_id=t.scene_id "
            "WHERE j.scene_id=? AND json_extract(t.payload,'$.reused_work.job_id')=?",
            [job['scene_id'],job_id],"t.created_at DESC,j.id DESC",1,20)
        return {**self._public(job),**self.runtime.plugin_host.work_details(job),"budget":self.job_budget(job),
                **self.job_delivery(job), "method_reads": await self.job_method_reads(job),
                'followup_work':followups,
                "executions": executions, "reservation": reservations[0] if reservations else None,
                "file_assets": await self.runtime.file_assets.for_job(job["scene_id"], job["id"]),
                "platform_actions": await actions_for(self.runtime.event_store, scene_id=job["scene_id"], job_id=job["id"])}

    async def job_execution(self, scene_id, job_id, execution_id):
        store = self.runtime.event_store
        record = await store.get_execution(execution_id)
        if (record is None or record.scene_id != scene_id or record.job_id != job_id
                or await store.get_job(job_id, scene_id) is None):
            return None
        # On-demand local reads only: no Gateway query or execution reconciliation.
        view = record.model_dump(mode='json', exclude={'stdout', 'stderr'})
        view['error'] = error_message(record.error) if record.error else None
        if view['termination']:
            view['termination']['detail'] = error_message(view['termination']['detail'])
        view['occupies_capacity'] = record.state in OCCUPYING_STATES
        view['events'] = [{**event.model_dump(mode='json'), 'detail': error_message(event.detail)}
                          for event in await store.read_execution_events(execution_id)
                          if event.sequence <= record.last_sequence]
        view['sampled_at'] = self.current_time()
        view['input_manifest'] = None
        view['input_manifest_state'] = 'not_applicable' if record.worker_type != 'python' else 'unrecorded'
        if record.worker_type == 'python':
            from len_bot.execution.inputs import InputManifestView
            try:
                request = await store.execution_request_of(execution_id)
                files = [item for item in request.input_files if item.name == 'manifest.json'] if request else []
                if files:
                    if len(files) != 1 or files[0].text is None:
                        raise ValueError('输入清单没有唯一文本载体')
                    manifest = InputManifestView.model_validate_json(files[0].text)
                    if (manifest.scene_id != scene_id or manifest.job_id != job_id
                            or manifest.job_revision is not None and manifest.job_revision != record.job_revision):
                        raise ValueError('输入清单与原执行身份不一致')
                    view['input_manifest'] = manifest.model_dump(mode='json')
                    view['input_manifest_state'] = 'recorded'
            except ValueError:
                # Validation errors may contain input bytes. Preserve the saved
                # request, but do not send its invalid payload to the panel.
                view['input_manifest_state'] = 'invalid'
                view['input_manifest_error'] = '原执行输入清单格式或身份不符，无法投影；原记录保留，未补造输入。'
        return self._public(view)

    async def file_asset_bytes(self, scene_id, job_id, asset_id):
        return await self.runtime.file_assets.bytes_for_job(scene_id, job_id, asset_id)

    @staticmethod
    def public_asset(asset, *, in_current_limit=None):
        from len_bot.media.models import media_purpose
        item={key:asset[key] for key in ("id","scope","source_event_id","mime_type","description","tags","enabled","curated","created_at","palette_order")}
        item['purpose']=media_purpose(item['tags'])
        item["in_initial_catalog"]=item["palette_order"] is not None and item['purpose']!='character_reference'
        item["in_current_limit"]=bool(in_current_limit) if in_current_limit is not None else None
        item["description_sufficient"]=bool((item.get("description") or "").strip() or item.get("tags"))
        return item

    async def media_assets(self, scene_id, query="", *, curated=None, enabled=None, palette_only=False, purpose=None, page=1, page_size=48):
        source="FROM media_assets WHERE scope IN (?, 'global-safe')";params=[scene_id]
        for field,value in (("curated",curated),("enabled",enabled)):
            if value is not None:source+=f" AND {field}=?";params.append(int(value))
        if purpose is not None:
            from len_bot.media.models import CHARACTER_REFERENCE_TAG
            if purpose not in {'character_reference','sticker','media'}:
                raise ValueError('未知素材用途')
            reference="EXISTS (SELECT 1 FROM json_each(tags_json) WHERE value=?)"
            source+=f" AND {'' if purpose=='character_reference' else 'NOT '}{reference}"
            params.append(CHARACTER_REFERENCE_TAG)
            if purpose!='character_reference':
                source+=f" AND {'' if purpose=='sticker' else 'NOT '}EXISTS (SELECT 1 FROM json_each(tags_json) WHERE value=?)"
                params.append('表情包')
        if palette_only:
            from len_bot.media.models import CHARACTER_REFERENCE_TAG
            source+=" AND palette_order IS NOT NULL AND curated=1 AND enabled=1 AND NOT EXISTS (SELECT 1 FROM json_each(tags_json) WHERE value=?)"
            params.append(CHARACTER_REFERENCE_TAG)
        terms=list(dict.fromkeys(query.split()))
        if terms:
            source+=" AND ("+" OR ".join("(instr(lower(description),lower(?))>0 OR instr(lower(tags_json),lower(?))>0)" for _ in terms)+")"
            params.extend(value for term in terms for value in (term,term))
        result=await self._page("SELECT *",source,params,"created_at DESC,id DESC",page,page_size)
        for item in result["items"]:
            item["tags"]=json.loads(item.pop("tags_json"));item["enabled"]=bool(item["enabled"]);item["curated"]=bool(item["curated"])
        palette_ids={asset["id"] for asset in await self.runtime.event_store.list_palette(scene_id, limit=self.runtime.config.media_palette_limit)}
        result["items"]=[self.public_asset(item, in_current_limit=item["id"] in palette_ids) for item in result["items"]]
        result['upload_max_bytes'] = self.runtime.config.media_max_image_bytes
        return result

    async def media_asset(self, asset_id, scene_id):
        asset=await self.runtime.event_store.get_media(asset_id,[scene_id,"global-safe"],include_disabled=True)
        return self.public_asset(asset) if asset else None

    async def media_palette(self, scene_id):
        items=[self.public_asset(asset, in_current_limit=True) for asset in await self.runtime.event_store.list_palette(scene_id, limit=self.runtime.config.media_palette_limit)]
        return {"items":items,"total":len(items),"complete":True,"limit":self.runtime.config.media_palette_limit}

    async def media_file(self, asset_id, scene_id):
        asset = await self.runtime.event_store.get_media(asset_id, [scene_id, 'global-safe'], include_disabled=True)
        if asset is None:
            raise ValueError('媒体不存在或不属于当前场景')
        if (asset['mime_type'] or '').startswith(('audio/', 'video/')):
            return await self.runtime.media_service.get_file_bytes(asset_id, scene_id, include_disabled=True)
        # The original image-registration path has no MIME until its first
        # validated read. It keeps the same image reader, not an A/V guess.
        return await self.runtime.media_service.get_bytes(asset_id, scene_id, include_disabled=True)

    def persona_settings(self):
        return {key:getattr(self.runtime.config,key) for key in (
            "character_context","identity_name","identity_core","identity_persona","conversation_style","address_names","bot_qq")}

    async def character_reference_settings(self):
        snapshot=self.settings_draft('character_references')
        configured=self.runtime.config_store.current.runtime.character_reference_assets
        media_enabled=self.runtime.config.media_enabled
        assets=await self.runtime.event_store.reference_assets_for_operator([item.asset_id for item in configured])
        return {**snapshot,'configured':[{**item.model_dump(mode='json'),
                    'asset':self.public_asset(assets[item.asset_id]) if item.asset_id in assets else None,
                    'issue':self.runtime.media_service.character_reference_issue(assets.get(item.asset_id))}
                for item in configured],
            'media_enabled':media_enabled}

    async def preview_diana_persona(self) -> dict:
        """Fill an operator draft; reading a template never changes saved values."""
        from len_bot.cognition.diana import PERSONA, MEDIA_REF_TAGS, build_examples
        palette = await self.runtime.event_store.list_palette("global-safe", limit=self.runtime.config.media_palette_limit)
        media_refs = {
            name: asset["id"]
            for name, tag in MEDIA_REF_TAGS.items()
            if (asset := next((item for item in palette if tag in item["tags"]), None)) is not None
        }
        examples = build_examples(media_refs)
        return {
            "fields": dict(PERSONA),
            "examples": examples,
            "missing_media": sorted({MEDIA_REF_TAGS[name] for item in examples
                                     for name in item["missing_media_refs"]}),
        }

    # ---------- Overview ----------

    async def overview(self) -> dict:
        rt = self.runtime
        stats = await rt.event_store.get_stats()

        memory_count = 0
        if rt.memory_store:
            cursor = await rt.memory_store._db.execute(
                "SELECT COUNT(*) FROM memories WHERE status='active' AND (expires_at IS NULL OR expires_at>?)", (self.current_time(),))
            (memory_count,) = await cursor.fetchone()

        scenes = await self.list_scenes()
        routing = rt.provider_registry.snapshot()
        social = rt.metrics.snapshot()["social"]

        return {
            "stats": {
                **stats,
                "active_scenes": len(scenes),
                "memory_beliefs_count": memory_count,
                "websocket_connected": self.websocket_connected(),
                "onebot_connection_mode": rt.config.onebot_connection_mode,
                "conversation_model": routing["routing"]["conversation"]["model"] if routing["routing"] and routing["routing"]["conversation"] else None,
                "work_model": routing["routing"]["work"]["model"] if routing["routing"] and routing["routing"]["work"] else None,
                "maintenance_model": routing["routing"]["maintenance"]["model"] if routing["routing"] and routing["routing"]["maintenance"] else None,
                "maintenance": self.maintenance_readiness(),
                "identity_name": rt.config.identity_name,
                "bot_qq": rt.config.bot_qq,
                "uptime_seconds": time.time() - getattr(rt, "_started_at", time.time()),
                "shadow_mode": rt.shadow_mode,
            },
            "scenes": scenes,
            "social_metrics": social,
            "sampled_at": self.current_time(),
            "runtime_interval": {"since":getattr(rt,"_started_at",None),"until":self.current_time()},
            "persistent_interval": (await self._rows("SELECT MIN(timestamp) AS since,MAX(timestamp) AS until FROM events"))[0],
        }

    def websocket_connected(self) -> bool:
        adapter = getattr(self.runtime, "_onebot_adapter", None)
        return bool(adapter and adapter.connected)

    def onebot_status(self) -> dict:
        adapter = self.runtime._onebot_adapter
        configured = self.runtime.config_store.current.runtime
        status = adapter.status() if adapter else {
            "connected": False, "remote_address": None, "server_status": "stopped",
            "connector_status": "stopped", "last_error": None, "echo_counter": 0,
        }
        status["active_connection"] = {key:status.get(key) for key in
            ("connection_mode", "action_transport", "ws_url", "http_url", "host", "port")}
        status.update(connection_mode=configured.onebot_connection_mode,
            action_transport=configured.onebot_action_transport, ws_url=configured.onebot_ws_url,
            http_url=configured.onebot_http_url, host=configured.ws_host, port=configured.ws_port,
            access_token_set=bool(configured.onebot_access_token),
            credential_revision=int(configured.onebot_credential_revision or 1),
            requires_restart=self.runtime.restart_required)
        return status

    def runtime_settings(self):
        from len_bot.config import EXECUTION_BUDGET_FIELDS
        excluded = {"ws_host", "ws_port", "address_names", "character_context", "character_reference_assets",
                    "conversation_style", "dashboard_secret_key", "dashboard_default_admin_password"}
        values = self.runtime.config_store.current.runtime.model_dump()
        settings = {key:value for key,value in values.items()
                    if key not in excluded and (key == "onebot_file_upload" or not key.startswith(("identity_", "onebot_")))}
        return {"settings":settings, "requires_restart":self.runtime.restart_required,
                "effective_budgets":{key:getattr(self.runtime.config,key) for key in sorted(EXECUTION_BUDGET_FIELDS)}}

    # ---------- Scenes ----------

    async def list_scenes(self) -> list[dict]:
        rows=await self._rows("SELECT state_json FROM scene_sessions WHERE scene_id LIKE 'group:%' OR scene_id LIKE 'private:%' ORDER BY updated_at DESC,scene_id")
        counts=await self._rows("""SELECT scene_id,COUNT(*) AS total FROM tasks WHERE json_extract(payload,'$.kind')='agent_job'
            AND status IN ('pending','claimed','processing','review_required','result_ready','awaiting_delivery') GROUP BY scene_id""")
        counts={item["scene_id"]:item["total"] for item in counts}
        sessions=[SceneSession.model_validate_json(row["state_json"]) for row in rows]
        items = [{"scene_id":session.scene_id,**self.scene_label(session.scene_id),"version":session.version,
            "participant_count":len(session.participants),"last_event_at":session.last_event_at,"last_bot_message_at":session.last_bot_message_at,
            "active_job_count":counts.get(session.scene_id,0),"pending_wake_count":len(session.pending_wakes),
            "has_history": True} for session in sessions]
        known = {item["scene_id"] for item in items}
        for scene_id in self.runtime.config_store.current.scenes:
            if scene_id not in known:
                items.append({"scene_id": scene_id, **self.scene_label(scene_id), "version": None,
                              "participant_count": 0, "last_event_at": None, "last_bot_message_at": None,
                              "active_job_count": 0, "pending_wake_count": 0, "has_history": False})
        for item in items:
            settings = self.runtime.config_store.current.scenes.get(item["scene_id"])
            item["settings"] = settings.model_dump() if settings is not None else None
            item["joined"] = None
        joined = await self.joined_groups()
        known = {item["scene_id"] for item in items}
        for row in joined.get("items") or []:
            scene_id = f"group:{row['group_id']}"
            if scene_id in known:
                continue
            items.append({"scene_id": scene_id, "display_name": row.get("group_name") or f"群聊 {row['group_id']}",
                          "scene_type": "group", "version": None, "participant_count": 0, "last_event_at": None,
                          "last_bot_message_at": None, "active_job_count": 0, "pending_wake_count": 0,
                          "has_history": False, "settings": None, "joined": True})
            known.add(scene_id)
        names = {f"group:{row['group_id']}": row.get("group_name") for row in joined.get("items") or []}
        joined_ids = set(names)
        for item in items:
            if item["scene_id"] in names and names[item["scene_id"]]:
                item["display_name"] = names[item["scene_id"]]
            if item["scene_id"].startswith("group:"):
                item["joined"] = item["scene_id"] in joined_ids if joined.get("complete") else None
        return items

    async def scene_detail(self, scene_id: str) -> Optional[dict]:
        raw=await self.runtime.event_store.load_scene_session(scene_id)
        if raw is None:return None
        session=SceneSession.model_validate(raw)
        preferences=await self.runtime.memory_store.interaction_preferences(scene_id,list(session.participants)) if self.runtime.memory_store else []
        public_session=session.model_dump(mode="json",exclude={"pending_wakes"})
        public_session.update(self.scene_label(scene_id),pending_wake_count=len(session.pending_wakes))
        status=await self.runtime.event_store.list_history_status(scene_id)
        status["unsuccessful_count"]=len(status.pop("unsuccessful"))
        return {"session":public_session,"preferences":[item.model_dump(mode="json") for item in preferences],
            "jobs":await self.jobs(scene_id),"history_batches":await self.history_batches(scene_id),
            "history_status":status,"maintenance":self.maintenance_readiness(scene_id),"sampled_at":self.current_time()}

    async def pending_wakes(self, scene_id, page=1, page_size=30):
        if not await self.runtime.event_store.load_scene_session(scene_id):return None
        result = await self._page("""SELECT json_extract(w.value,'$.event_id') AS event_id,json_extract(w.value,'$.rowid') AS rowid,
            json_extract(w.value,'$.actor_id') AS actor_id,json_extract(w.value,'$.reasons') AS reasons_json,
            json_extract(w.value,'$.certain') AS certain""",
            "FROM scene_sessions s,json_each(s.state_json,'$.pending_wakes') w WHERE s.scene_id=?",
            [scene_id],"rowid DESC,event_id DESC",page,page_size)
        for item in result["items"]:
            item["reasons"]=json.loads(item.pop("reasons_json"));item["certain"]=bool(item["certain"])
        return result


    # ---------- Events / Tasks / Loops / Memories ----------

    async def _event_views(self, rows, cutoff):
        groups={}
        for row in rows:
            metadata=json.loads(row["metadata"]);metadata["_rowid"]=row["rowid"]
            event=Event(id=row["id"],event_type=row["event_type"],scene_id=row["scene_id"],actor_id=row["actor_id"],
                        timestamp=row["timestamp"],payload=json.loads(row["payload"]),metadata=metadata)
            groups.setdefault(event.scene_id,[]).append(event)
        views={}
        for scene,events in groups.items():
            for event in events:
                payload=self._public(event.payload);metadata=event.metadata
                delivery = receipt_delivery_status(event.event_type, event.payload, metadata)
                sender=payload.get("sender") or {}
                quote=None
                quote_rows=[]
                reply_field="reply_to" if event.event_type in {EventType.MESSAGE_SENT,EventType.MESSAGE_SEND_FAILED,EventType.ACTION_SHADOWED} else "reply_to_message_id"
                reference=payload.get(reply_field)
                if reference is not None:
                    quote_rows=await self._rows("SELECT *,rowid FROM events WHERE scene_id=? AND rowid<? AND CAST(json_extract(payload,'$.message_id') AS TEXT)=? ORDER BY rowid DESC LIMIT 1",
                                                [scene,metadata["_rowid"],str(reference)])
                    quote={"missing":True}
                if quote_rows:
                    original=quote_rows[0];original_payload=json.loads(original["payload"]);original_sender=original_payload.get("sender") or {}
                    quote={"event_id":original["id"],"rowid":original["rowid"],"actor_id":original["actor_id"],
                           "display_name":original_sender.get("card") or original_sender.get("nickname") or original["actor_id"],
                           "text":original_payload.get("raw_text") or original_payload.get("content", ""),
                           "media":json.loads(original["metadata"]).get("media",[])}
                media=[]
                for item in metadata.get("media",[]):
                    asset=await self.media_asset(item["asset_id"],scene)
                    if asset:media.append(asset)
                if quote and not quote.get("missing"):
                    quote_media=[]
                    for item in quote.get("media",[]):
                        asset=await self.media_asset(item["asset_id"],scene)
                        if asset:quote_media.append(asset)
                    quote={key:quote[key] for key in ("event_id","rowid","actor_id","display_name","text") if key in quote}
                    quote["media"]=quote_media
                human=event.event_type in {EventType.GROUP_MESSAGE_RECEIVED,EventType.PRIVATE_MESSAGE_RECEIVED}
                member_mentions=list(dict.fromkeys(re.findall(r'\[CQ:at,qq=(\d+)(?:,[^\]]*)?\]',event.raw_text)))
                if not human:
                    member_mentions=list(dict.fromkeys(part['qq_uid'] for part in payload.get('segments',[])
                        if part.get('type')=='at'))
                views[event.id]={"id":event.id,"rowid":metadata["_rowid"],"event_type":event.event_type.value,"scene_id":scene,
                    "actor_id":event.actor_id,"timestamp":event.timestamp,"payload":payload,
                    "attention":{key:metadata[key] for key in ("attention_reasons","attention_certain","attention_lane","attention_due_at") if key in metadata},
                    "participation": self._participation(event, metadata, delivery),
                    "interaction": {key:metadata[key] for key in ("interaction", "interaction_reason", "requester_qq_uid",
                                       "command_id", "calendar_parent_event_id", "conversation_excluded",
                                       'plugin_routes','plugin_consumed','plugin_work_issue') if key in metadata}
                                   if "interaction" in metadata else None,
                    'plugin_origin':payload.get('plugin_origin'),
                    "display_name":sender.get("card") or sender.get("nickname") or event.actor_id,
                    "member_mentions":member_mentions,"addressed_to":payload.get('response_actor_ids',[]),
                    "scene_type":self.scene_label(scene)["scene_type"],
                    "display_kind":"human" if human else "bot" if event.event_type==EventType.MESSAGE_SENT else "system",
                    "delivery_status":delivery,"simulated":bool(metadata.get("simulated") or payload.get("origin_mode")=="simulated"),
                    "origin_mode":payload.get("origin_mode") or metadata.get("mode"),"quote":quote,"media":media}
        return [views[row["id"]] for row in rows]

    async def query_events(self, scene_id=None, actor_id=None, event_type=None, since=None, until=None,
                           limit=50, *, before=None, snapshot_rowid=None, messages_only=False, event_id=None):
        if not 1 <= limit <= 100:raise ValueError("limit must be 1..100")
        if messages_only and not await self.runtime.event_store.load_scene_session(scene_id):return None
        if snapshot_rowid is None:
            snapshot_rowid=(await self._rows("SELECT COALESCE(MAX(rowid),0) AS cutoff FROM events WHERE (? IS NULL OR scene_id=?)",[scene_id,scene_id]))[0]["cutoff"]
        source="FROM events WHERE rowid<=?";params=[snapshot_rowid]
        for field,value in (("scene_id",scene_id),("actor_id",actor_id),("event_type",event_type)):
            if value is not None:source+=f" AND {field}=?";params.append(value)
        for op,value in ((">=",since),("<=",until)):
            if value is not None:source+=f" AND timestamp{op}?";params.append(value)
        if messages_only:source+=" AND event_type IN ('GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED','MESSAGE_SENT')"
        if event_id is not None:
            locate_sql="SELECT rowid FROM events WHERE id=? AND scene_id=? AND rowid<=?"
            if messages_only:locate_sql+=" AND event_type IN ('GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED','MESSAGE_SENT')"
            located=await self._rows(locate_sql,[event_id,scene_id,snapshot_rowid])
            if not located:return None
            source+=" AND rowid<=?";params.append(located[0]["rowid"])
        if before is not None:source+=" AND rowid<?";params.append(before)
        rows=await self._rows("SELECT *,rowid "+source+" ORDER BY rowid DESC LIMIT ?",[*params,limit+1])
        more=len(rows)>limit;rows=rows[:limit]
        return {"items":await self._event_views(rows,snapshot_rowid),"next_before":rows[-1]["rowid"] if more else None,
                "snapshot_rowid":snapshot_rowid,"has_more":more}

    async def event(self, event_id, scene_id):
        rows=await self._rows("SELECT *,rowid FROM events WHERE id=? AND scene_id=?",[event_id,scene_id])
        return (await self._event_views(rows,rows[0]["rowid"]))[0] if rows else None

    @staticmethod
    def _task(item):
        item["payload"]=RuntimeQueryService._public(json.loads(item["payload"]))
        wake_match=item.pop("wake_match_json")
        item["wake_match"]=json.loads(wake_match) if wake_match else None
        return item

    async def list_tasks(self, status=None, *, scene_id=None, kind="reminder", page=1, page_size=30):
        source="FROM tasks WHERE (? IS NULL OR status=?) AND (? IS NULL OR scene_id=?)";params=[status,status,scene_id,scene_id]
        if kind=="reminder":source+=" AND COALESCE(json_extract(payload,'$.kind'),'reminder') NOT IN ('agent_job','heartbeat','heartbeat_occupancy','interest_share','deferred_delivery')"
        elif kind=="agent_job":source+=" AND json_extract(payload,'$.kind')='agent_job'"
        elif kind=="system":source+=" AND json_extract(payload,'$.kind') IN ('heartbeat','heartbeat_occupancy','interest_share')"
        elif kind=="deferred":source+=" AND json_extract(payload,'$.kind')='deferred_delivery'"
        else:raise ValueError('未知任务分类')
        result=await self._page("SELECT *",source,params,"created_at DESC,id DESC",page,page_size)
        result["items"]=[self._task(item) for item in result["items"]]
        return result

    async def get_task(self, task_id, scene_id=None):
        rows=await self._rows("SELECT * FROM tasks WHERE id=? AND (? IS NULL OR scene_id=?)",[task_id,scene_id,scene_id])
        if not rows:
            return None
        task = self._task(rows[0])
        if task['payload'].get('kind') == 'agent_job':
            job = await self.runtime.event_store.get_job(task_id, task['scene_id'])
            if job is not None:
                task.update(self.job_delivery(job))
        if task['payload'].get('kind') in {'heartbeat', 'heartbeat_occupancy', 'interest_share'}:
            traces = await self._rows("SELECT * FROM traces WHERE scene_id=? AND ref_id=? AND kind IN ('heartbeat','interest_share') ORDER BY created_at DESC LIMIT 30",
                                     [task['scene_id'], task_id])
            task['scheduler_observations'] = [self._trace(row, detail=True) for row in traces]
        return task

    async def public_interests(self, *, query='', page=1, page_size=30):
        result = await self._page('SELECT *',
            "FROM public_interests WHERE visibility='public' AND (?='' OR instr(lower(topic || ' ' || statement),lower(?))>0)",
            [query, query], 'observed_at DESC,id DESC', page, page_size)
        now = self.current_time()
        for item in result['items']:
            item['source_observation_ids'] = json.loads(item.pop('source_json'))
            item['evidence_spans'] = json.loads(item.pop('evidence_json'))
            item['expired'] = item['valid_until'] is not None and item['valid_until'] <= now
        result['read_at'] = now
        return result

    async def public_interest(self, ident):
        store = InterestStore(self.runtime.event_store)
        item = await store.get(ident)
        if item is None:
            return None
        sources = []
        for source_id in item.source_observation_ids:
            rows = await self._rows('SELECT id,scene_id,event_id,result_json FROM tool_observations WHERE id=?', [source_id])
            if rows:
                row = rows[0]
                row['result'] = self._observation_view(ToolResult.model_validate_json(row.pop('result_json')))
                sources.append(row)
        changes = await self._rows("""SELECT id,scene_id,timestamp,payload FROM events WHERE event_type='PUBLIC_INTEREST_CHANGED'
            AND (json_extract(payload,'$.after.id')=? OR json_extract(payload,'$.before.id')=?)
            ORDER BY timestamp DESC,id DESC LIMIT 51""", [ident, ident])
        for change in changes:
            change['payload'] = self._public(json.loads(change['payload']))
        proven = await store.public_observation_ids(item.source_observation_ids)
        publications = await self.public_interest_publications(ident)
        return {**item.model_dump(mode='json'), 'sources': sources,
                'public_sources_confirmed': set(item.source_observation_ids) == proven,
                'expired': item.valid_until is not None and item.valid_until <= self.current_time(),
                'changes': changes[:50], 'more_changes': len(changes) > 50,
                **publications, 'read_at': self.current_time()}

    async def public_interest_publications(self, ident):
        # A cross-scene publication index contains identities and receipt facts,
        # never the conversation text used to phrase a scene's expression.
        actions = await self._rows("""SELECT scene_id,json_extract(payload,'$.action_id') AS action_id,
            MAX(rowid) AS last_rowid FROM events
            WHERE event_type IN ('DELIVERY_ATTEMPTED','MESSAGE_SENT','MESSAGE_SEND_FAILED','ACTION_SHADOWED')
              AND json_extract(payload,'$.interest_publication.interest_id')=?
              AND json_extract(payload,'$.action_id') IS NOT NULL
            GROUP BY scene_id,json_extract(payload,'$.action_id') ORDER BY last_rowid DESC LIMIT 51""", [ident])
        publications = []
        for action in actions[:50]:
            rows = await self._rows("""SELECT id,event_type,timestamp,payload,metadata FROM events
                WHERE scene_id=? AND json_extract(payload,'$.action_id')=?
                  AND json_extract(payload,'$.interest_publication.interest_id')=?
                  AND event_type IN ('DELIVERY_ATTEMPTED','MESSAGE_SENT','MESSAGE_SEND_FAILED','ACTION_SHADOWED')
                  AND rowid<=? ORDER BY rowid""",
                [action['scene_id'], action['action_id'], ident, action['last_rowid']])
            if not rows:
                continue
            attempts, receipts, latest = [], [], None
            for row in rows:
                payload, metadata = json.loads(row['payload']), json.loads(row['metadata'])
                if row['event_type'] == 'DELIVERY_ATTEMPTED':
                    attempts.append(row['id'])
                else:
                    receipts.append(row['id'])
                    latest = {'status': receipt_delivery_status(row['event_type'], payload, metadata),
                              'receipt_at': row['timestamp'], 'message_id': payload.get('message_id'),
                              'error': error_message(payload.get('error') or '')}
                revision = payload['interest_publication'].get('revision')
            publications.append({'scene_id': action['scene_id'], 'action_id': action['action_id'],
                'interest_revision': revision, 'attempt_event_ids': attempts, 'receipt_event_ids': receipts,
                **(latest or {'status': 'unknown', 'receipt_at': None, 'message_id': None,
                             'error': '已有发送尝试，尚无可靠终态回执；本页不确认上游是否仍在处理'})})
        return {'publications': publications, 'more_publications': len(actions) > 50}

    async def open_loop(self, loop_id, scene_id=None):
        rows=await self._rows("SELECT * FROM open_loops WHERE id=? AND (? IS NULL OR scene_id=?)",[loop_id,scene_id,scene_id])
        if not rows:return None
        item=rows[0]
        sources=await self._rows("SELECT metadata FROM events WHERE id=? AND scene_id=?",[item['source_event_id'],item['scene_id']])
        item['resume_state']=self._public((json.loads(sources[0]['metadata']).get('associated_open_loop') or {}).get('resume_state')) if sources else None
        return item

    async def list_open_loops(self, status=None, *, scene_id=None, page=1, page_size=30):
        return await self._page("SELECT *","FROM open_loops WHERE (? IS NULL OR status=?) AND (? IS NULL OR scene_id=?)",
                                [status,status,scene_id,scene_id],"created_at DESC,id DESC",page,page_size)

    async def list_memories(self, status=None, scope=None, subject=None, *, kind=None, query="", validity=None, page=1, page_size=30):
        source="FROM memories WHERE 1=1";params=[]
        for field,value in (("status",status),("scope",scope),("subject",subject),("kind",kind)):
            if value is not None:source+=f" AND {field}=?";params.append(value)
        now = self.current_time()
        if validity == 'current':
            source += " AND status='active' AND (expires_at IS NULL OR expires_at>?)"; params.append(now)
        elif validity == 'expired':
            source += " AND expires_at IS NOT NULL AND expires_at<=?"; params.append(now)
        elif validity is not None:
            raise ValueError('Unknown memory validity filter')
        if query:source+=" AND instr(lower(statement),lower(?))>0";params.append(query)
        result=await self._page("SELECT "+','.join(MEMORY_COLUMNS),source,params,"created_at DESC,id DESC",page,page_size)
        result["items"]=[memory_from_row(tuple(item[column] for column in MEMORY_COLUMNS)).model_dump(mode="json") for item in result["items"]]
        result['sampled_at'] = now
        return result

    async def memory(self, memory_id, scope=None):
        rows=await self._rows("SELECT "+','.join(MEMORY_COLUMNS)+" FROM memories WHERE id=? AND (? IS NULL OR scope=?)",[memory_id,scope,scope])
        return memory_from_row(tuple(rows[0][column] for column in MEMORY_COLUMNS)).model_dump(mode="json") if rows else None

    async def memory_chain(self, memory_id: str, scope=None) -> list[dict]:
        """Include every merged predecessor, not just the first old semantic key."""
        if not self.runtime.memory_store or await self.memory(memory_id,scope) is None:
            return []
        cursor = await self.runtime.memory_store._db.execute(
            f"""WITH RECURSIVE chain(id, scope, superseded_by) AS (
                   SELECT id,scope,superseded_by FROM memories WHERE id=?
                   UNION
                   SELECT m.id,m.scope,m.superseded_by FROM memories m JOIN chain c
                     ON m.scope=c.scope AND (m.id=c.superseded_by OR m.superseded_by=c.id)
               )
               SELECT {','.join('m.' + column for column in MEMORY_COLUMNS)} FROM memories m JOIN chain c ON m.id=c.id
               ORDER BY m.created_at,m.id""", (memory_id,))
        return [memory_from_row(row).model_dump(mode="json") for row in await cursor.fetchall()]

    # ---------- Trace / Metrics / Plugins / Shadow ----------

    @staticmethod
    def _trace_runs(kind, payload):
        def collect(record):
            return [record, *(child for key in ('runs','agents','model_slot_waits') for run in record.get(key,[]) for child in collect(run))]
        return collect(payload.get('conversation') or payload.get('cognition') or payload)

    def _trace(self, item, detail=False, *, identities=False):
        item=dict(item);payload=RuntimeQueryService._public(json.loads(item.pop("payload")))
        conversation=payload.get("conversation") or {}
        result = payload.get("result") or {}
        gate = payload.get('gate') or {}
        publication = gate.get('publication') or {}
        item['committed'] = gate.get('committed')
        item['publication_status'] = publication.get('status')
        item["summary"]=(payload.get("error") or result.get("decision_reason") or result.get("reason")
                         or conversation.get("failure_reason") or result.get("summary") or payload.get("kind") or item["kind"])[:300]
        if item['kind'] == 'conversation_wait':
            item['wait_state'] = payload.get('state')
            item['summary'] = {'acquired':'已取得对话执行槽位', 'cancelled':'等待对话执行槽位时取消',
                'failed':'等待对话执行槽位失败'}.get(payload.get('state'),'对话执行槽位等待记录')
        item["result_status"] = result.get("status")
        origin=payload.get('plugin_origin')
        plugin_id=origin['plugin_id'] if origin else payload.get('plugin_id')
        entry=self.runtime.config_store.catalog.entries.get(plugin_id)
        item['plugin_origin']=origin
        item['plugin_name']=entry.spec.name if entry else plugin_id
        if item['kind'].startswith('plugin_'):
            item['summary']=' · '.join(str(value) for value in (item['plugin_name'],origin.get('entry_id') if origin else None,
                payload.get('state') or payload.get('operation'),payload.get('error')) if value)[:300]
            item['result_status']=payload.get('state')
        item['relation']=({'job_id':payload['job_id']} if payload.get('job_id') else
            {'episode_id':origin['run_id']} if origin else
            {'job_id':payload.get('job_id') or item['ref_id']} if item['kind'].startswith('agent_job') else
            {'batch_id':item['ref_id']} if item['kind'].startswith('history_maintenance') else
            {'episode_id':item['ref_id']} if item['kind'] in {'conversation','conversation_error'} else
            {'event_id':payload['receipt_event_id']} if payload.get('receipt_event_id') else
            {'event_id':payload.get('source_event_id') or item['ref_id']} if item['kind'] in {'calendar_command','live_announcement'} else None)
        if gate.get('committed') and publication.get('status') in {'failed', 'interrupted'}:
            state = '发布失败' if publication['status'] == 'failed' else '发布中断'
            item['summary'] = f"已提交，{state} · {publication.get('phase')} · {publication.get('error')}"[:300]
        elif gate.get('committed') and item['kind'] == 'conversation_error':
            item['summary'] = '已提交，后续处理异常 · ' + item['summary']
        runs=self._trace_runs(item['kind'],payload)
        if detail or identities:
            item['timings'] = {'elapsed_ms': payload.get('elapsed_ms'), 'runs': [
                {'index': index + 1, 'job_revision': run.get('job_revision'),
                 'cognition_slot_wait_ms': run.get('cognition_slot_wait_ms'),
                 'cognition_slot_wait_state': run.get('state') if 'cognition_slot_wait_ms' in run else None,
                 'initial_source_reads_ms': run.get('initial_source_reads_ms'),
                 'initial_context_ms': run.get('initial_context_ms'),
                 'commit_ms': (run.get('timings_ms') or {}).get('commit'),
                 'publication_ms': (run.get('timings_ms') or {}).get('publication'),
                 'steps': [{'index': step.get('step'), 'call_id': step.get('call_id'),
                            'request_preparation_ms': step.get('request_preparation_ms'),
                            'model_ms': step.get('latency_ms'),
                            'tool_presentation_ms': step.get('tool_presentation_ms'),
                            'tools': [{'id': tool.get('id'), 'name': tool.get('name'),
                                       'execution_ms': tool.get('execution_ms')}
                                      for tool in step.get('tool_calls', [])]}
                           for step in run.get('steps', [])]}
                for index, run in enumerate(runs)
                if any(key in run for key in ('steps', 'cognition_slot_wait_ms', 'initial_source_reads_ms', 'initial_context_ms', 'timings_ms'))]}
        if (detail or identities) and item['kind'] in {'conversation', 'conversation_error', 'conversation_wait'}:
            # These are stored identities, not inferred from the trace time.
            # A source may belong to an attempt that failed before any commit.
            item['source_event_ids'] = sorted(set(payload.get('source_event_ids', []))
                | set((payload.get('burst') or {}).get('source_event_ids', [])))
            item['read_source_event_ids'] = sorted({ident for run in runs
                for ident in (run.get('references') or {}).get('read_messages', [])})
            gates = [gate, *(checkpoint['gate'] for run in runs for checkpoint in run.get('checkpoints', []))]
            item['commit_event_ids'] = sorted({record['commit_event_id'] for record in gates
                if record.get('committed') and record.get('commit_event_id')})
            item['error'] = payload.get('error') or conversation.get('failure_reason')
            item['error_type'] = payload.get('error_type')
            item['gate_accepted'] = gate.get('accepted')
            item['gate_reason'] = gate.get('reason')
            item['error_phase'] = payload.get('error_phase')
            item['publication_error'] = publication.get('error')
            item['publication_phase'] = publication.get('phase')
            # Only project a saved per-call request; the latest run plan cannot
            # stand in for an earlier call or a failed transport without an ID.
            item['requests'] = []
            for run in runs:
                for step in run.get('steps', []):
                    request = (step.get('context_plan') or {}).get('request')
                    if not step.get('call_id') or request is None:
                        continue
                    messages = request.get('messages')
                    pixels = request.get('current_pixel_assets')
                    tools = step.get('available_tools')
                    item['requests'].append({
                        'call_id': step['call_id'],
                        'message_count': len(messages) if messages is not None else None,
                        'omitted_message_count': sum(bool(message.get('omitted')) for message in messages)
                            if messages is not None else None,
                        'pixel_asset_count': len(pixels) if pixels is not None else None,
                        'tool_count': len(tools) if tools is not None else None,
                    })
        calls = [call for run in runs
                 for step in run.get("steps", []) for call in step.get("tool_calls", [])]
        direct=[call for run in runs for call in run.get('tool_results',[])]
        observations = [call.get("observation_status") or (call.get("observation") or {}).get("status") for call in calls]
        item["tool_outcomes"] = {"returned": sum(call.get("status") == "completed" for call in calls)+len(direct),
                                 "errors": sum(status in {"error", "unsupported"} or call.get("status") in {"error", "invalid_arguments"}
                                               for call, status in zip(calls, observations))+sum(call['status'] in {'error','unsupported'} for call in direct),
                                 "no_results": observations.count("no_results")+sum(call['status']=='no_results' for call in direct),
                                 'direct':len(direct)}
        if detail:item["payload"]=payload
        return item

    async def query_traces(self, scene_id=None, kind=None, ref_id=None, *, episode_id=None, since=None, until=None, page=1, page_size=30):
        source="FROM traces WHERE 1=1";params=[]
        for field,value in (("scene_id",scene_id),("kind",kind),("ref_id",ref_id)):
            if value is not None:source+=f" AND {field}=?";params.append(value)
        if episode_id is not None:
            source += " AND (ref_id=? OR json_extract(payload,'$.episode_id')=? OR json_extract(payload,'$.plugin_origin.run_id')=? OR json_extract(payload,'$.plugin_origin.parent_run_id')=?)"
            params.extend([episode_id]*4)
        for op,value in ((">=",since),("<=",until)):
            if value is not None:source+=f" AND created_at{op}?";params.append(value)
        result=await self._page("SELECT *",source,params,"created_at DESC,id DESC",page,page_size)
        result["items"]=[self._trace(item) for item in result["items"]]
        return result

    async def trace(self, trace_id, scene_id=None):
        rows=await self._rows("SELECT * FROM traces WHERE id=? AND (? IS NULL OR scene_id=?)",[trace_id,scene_id,scene_id])
        if not rows:
            return None
        result = self._trace(rows[0], True)
        if result['kind'] in {'conversation', 'conversation_error'} and result.get('ref_id'):
            commits = await self._rows("SELECT id,timestamp,payload FROM events WHERE (id=? OR json_extract(payload,'$.episode_id')=?) AND scene_id=? AND event_type='CONVERSATION_COMMITTED' ORDER BY rowid",
                ['turn:' + result['ref_id'],result['ref_id'], result['scene_id']])
            if commits:
                result['operation_receipts'] = [{**receipt, 'proposal_ref': reference,
                    'commit_event_id': commit['id'], 'committed_at': commit['timestamp'], 'episode_id': result['ref_id']}
                    for commit in commits for reference,receipt in self._public(json.loads(commit['payload']).get('operation_receipts',{})).items()]
        observed = {}
        for run in self._trace_runs(result["kind"], result["payload"]):
            for direct in run.get('tool_results',[]):
                observation=await self.runtime.event_store.read_tool_observation(direct['result_id'],[result['scene_id']])
                direct['stored_observation']=self._observation_view(observation) if observation else None
            for step in run.get("steps", []):
                for call in step.get("tool_calls", []):
                    result_id = (call.get("observation") or {}).get("result_id")
                    if not result_id:
                        continue
                    if result_id not in observed:
                        observation = await self.runtime.event_store.read_tool_observation(result_id, [result["scene_id"]])
                        observed[result_id] = self._observation_view(observation) if observation else None
                    # Stored source metadata is distinct from the exact range
                    # presented to the model in the recorded exchange.
                    call["stored_observation"] = observed[result_id]
        return result

    def status(self):
        snapshot=self.providers();profiles=snapshot["routing"] or {};roles={}
        for role in ("conversation","work","maintenance"):
            profile=profiles.get(role)
            provider=next((item for item in snapshot["providers"] if profile and item["id"]==profile["provider_id"]),None)
            ready=bool(provider and provider["enabled"] and provider["api_key_masked"])
            roles[role]={"configured":profile is not None,"ready":ready,"profile":profile,
                         "reason":"已就绪" if ready else "未配置模型" if profile is None else "供应商未启用或未设置密钥"}
        scenes = self.runtime.config_store.current.scenes
        return {"sampled_at":self.current_time(),"running":bool(getattr(self.runtime,"_running",False)),
                "onebot":self.onebot_status(),"shadow_mode":self.runtime.shadow_mode,
                "scene_counts": {"configured": len(scenes),
                                 "enabled": sum(scene.enabled for scene in scenes.values()),
                                 "chat_enabled": sum(scene.enabled and scene.chat for scene in scenes.values())},
                "business_timezone": self.runtime.config_store.current.time.timezone if self.runtime.config_store.current.time else None,
                "roles":roles}

    @staticmethod
    def event_types():
        return {"items":[kind.value for kind in EventType],"complete":True}

    async def relations(self, scene_id, *, event_id=None, job_id=None, episode_id=None, action_id=None, batch_id=None):
        """Follow stored identifiers only; timestamps never establish a relation."""
        limit=50
        event_ids={event_id} if event_id else set()
        job_ids={job_id} if job_id else set()
        episode_ids={episode_id} if episode_id else set()
        action_ids={action_id} if action_id else set()
        result_ids=set()
        truncated={}

        def plugin_origin(encoded):
            if not encoded:return
            event_ids.add(encoded['source_event_id'])
            episode_ids.add(encoded['run_id'])
            if encoded.get('parent_run_id'):
                # The store resolves this identifier as an episode or a job;
                # no timestamp or naming convention is used to invent a link.
                episode_ids.add(encoded['parent_run_id'])
                job_ids.add(encoded['parent_run_id'])
            plugin_origin(encoded.get('handler_origin'))

        def answer_basis(encoded):
            if encoded is None:
                return
            basis = AnswerBasis.model_validate(encoded)
            event_ids.update(basis.event_ids)
            result_ids.update(span.result_id for span in basis.result_spans)
            if basis.work_result is not None:
                work = basis.work_result
                job_ids.add(work.job_id)
                result_ids.update(work.result_ids)
                result_ids.update(span.result_id for span in work.evidence_spans)

        def membership(column, values):
            values=sorted(values)
            return (column+" IN ("+','.join('?' for _ in values)+")",values) if values else ("0",[])

        async def linked(select, source, clauses, order, label):
            sql=" OR ".join(clause for clause,_ in clauses)
            params=[scene_id,*(value for _,values in clauses for value in values)]
            rows=await self._rows(select+" "+source+" AND ("+sql+") ORDER BY "+order+" LIMIT ?",[*params,limit+1])
            truncated[label]=truncated.get(label,False) or len(rows)>limit
            return rows[:limit]

        if batch_id is not None:
            batch=await self.history_batch(batch_id)
            if batch is None or batch["scene_id"] != scene_id:return None
            sources=set(batch["source_event_ids"])|set(batch["key_event_ids"])
            events=await linked("SELECT *,rowid","FROM events WHERE scene_id=?",[membership("id",sources)],"rowid DESC","events")
            calls=await linked("SELECT *","FROM model_calls WHERE scene_id=?",[membership("batch_id",{batch_id})],"started_at DESC,id DESC","calls")
            traces=await linked("SELECT *","FROM traces WHERE scene_id=? AND kind IN ('history_maintenance','history_maintenance_error')",
                                [membership("ref_id",{batch_id})],"created_at DESC,id DESC","traces")
            located={key:batch[key] for key in ("id","scene_id","status","start_rowid","start_offset","end_rowid","end_offset","key_event_ids","generation_version")}
            located["offset_basis"]="history_source_text"
            return {"events":await self._event_views(events,batch["end_rowid"]),"calls":[self._call(row) for row in calls],
                    "traces":[self._trace(row) for row in traces],"jobs":[],"actions":[],"tool_results":[],"batches":[located],
                    "limits":{name:limit for name in ("events","traces","calls","jobs","actions","tool_results","batches")},
                    "truncated":{**truncated,"jobs":False,"actions":False,"tool_results":False,"batches":False}}

        if event_id:
            original=await self.event(event_id,scene_id)
            if original is None:return None
            plugin_origin(original.get('plugin_origin'))
            for route in (original.get('interaction') or {}).get('plugin_routes',[]):plugin_origin(route['origin'])
        if job_id:
            original=await self.job(job_id,scene_id)
            if original is None:return None
            plugin_origin(original.get('plugin_origin'))
        if episode_id:
            exists=await self._rows("""SELECT id FROM traces WHERE scene_id=? AND ref_id=? UNION ALL
                SELECT id FROM model_calls WHERE scene_id=? AND episode_id=? UNION ALL
                SELECT id FROM events WHERE scene_id=? AND (id=? OR json_extract(payload,'$.batch_id')=? OR json_extract(payload,'$.episode_id')=?) LIMIT 1""",
                [scene_id,episode_id,scene_id,episode_id,scene_id,'turn:'+episode_id,episode_id,episode_id])
            if not exists:return None
        if action_id:
            receipts=await self._rows("SELECT id FROM events WHERE scene_id=? AND json_extract(payload,'$.action_id')=? LIMIT 1",[scene_id,action_id])
            deliveries=await self._rows("""SELECT id FROM tasks WHERE scene_id=? AND
                (json_extract(payload,'$.delivery_action_id')=? OR json_extract(payload,'$.ack_action_id')=?) LIMIT 1""",
                [scene_id,action_id,action_id])
            approvals=await self._rows("""SELECT ref_id,kind,payload FROM traces WHERE scene_id=? AND (
                EXISTS(SELECT 1 FROM json_each(payload,'$.gate.action_ids') WHERE value=?)
                OR (kind IN ('calendar_command','live_announcement') AND EXISTS(
                    SELECT 1 FROM json_each(payload,'$.action_ids') WHERE value=?)))
                ORDER BY created_at DESC,id DESC LIMIT 1""",[scene_id,action_id,action_id])
            committed_actions=await self._rows("""SELECT id,payload FROM events WHERE scene_id=?
                AND event_type='CONVERSATION_COMMITTED'
                AND EXISTS(SELECT 1 FROM json_each(payload,'$.action_ids') WHERE value=?)""",[scene_id,action_id])
            if not receipts and not approvals and not deliveries and not committed_actions:return None
            event_ids.update(item['id'] for item in committed_actions)
            episode_ids.update(json.loads(item['payload']).get('episode_id',item['id'][5:]) for item in committed_actions)
            job_ids.update(item["id"] for item in deliveries)
            for item in approvals:
                if item["kind"] in {"calendar_command", "live_announcement"}:
                    event_ids.add(item["ref_id"])
                    audit = json.loads(item["payload"])
                    if audit.get("episode_id"):
                        episode_ids.add(audit["episode_id"])
                else:
                    episode_ids.add(item["ref_id"])

        source_clause, source_values = membership('value', event_ids)
        conversation_sources = [("kind IN ('conversation','conversation_error','conversation_wait') AND EXISTS("
            "SELECT 1 FROM json_each(payload,'" + path + "') WHERE " + source_clause + ")", source_values)
            for path in ('$.source_event_ids', '$.burst.source_event_ids', '$.conversation.references.read_messages')]
        seed_traces=await linked('SELECT *','FROM traces WHERE scene_id=?',[
            membership('ref_id',episode_ids|job_ids),membership("json_extract(payload,'$.plugin_origin.source_event_id')",event_ids),
            *conversation_sources],
            'created_at DESC,id DESC','traces')
        for row in seed_traces:
            if row['kind'] in {'conversation', 'conversation_error'}:
                episode_ids.add(row['ref_id'])
            for run in self._trace_runs(row['kind'],json.loads(row['payload'])):plugin_origin(run.get('plugin_origin'))

        # A committed turn explicitly records all originals read in that turn.
        source_clause,source_values=membership("value",event_ids)
        route_clause,route_values=membership("json_extract(value,'$.origin.run_id')",episode_ids)
        commits=await linked("SELECT *,rowid","FROM events WHERE scene_id=?",[
            membership("id",event_ids|{'turn:'+ident for ident in episode_ids}),
            ("event_type='CONVERSATION_COMMITTED' AND EXISTS(SELECT 1 FROM json_each(payload,'$.source_event_ids') WHERE "+source_clause+")",source_values),
            membership("json_extract(payload,'$.origin_event_id')", event_ids),
            membership("json_extract(payload,'$.batch_id')",episode_ids),
            membership("json_extract(payload,'$.episode_id')",episode_ids),
            membership("json_extract(payload,'$.plugin_origin.run_id')",episode_ids),
            ('EXISTS(SELECT 1 FROM json_each(metadata,\'$.plugin_routes\') WHERE '+route_clause+')',route_values),
            membership("json_extract(payload,'$.action_id')",action_ids)],"rowid DESC","events")
        for event in commits:
            event_ids.add(event["id"]);payload=json.loads(event["payload"])
            plugin_origin(payload.get('plugin_origin'))
            for route in json.loads(event['metadata']).get('plugin_routes',[]):plugin_origin(route['origin'])
            for message in payload.get('outcome',{}).get('message_proposals',[]):
                plugin_origin(message.get('plugin_origin'))
                answer_basis(message.get('answer_basis'))
            answer_basis(payload.get('answer_basis'))
            if event['event_type'] == 'CONVERSATION_COMMITTED':
                action_ids.update(payload.get('action_ids', []))
            if event["event_type"]=="CONVERSATION_COMMITTED" and event["id"].startswith('turn:'):
                episode_ids.add(payload.get('episode_id',event["id"][5:]));event_ids.update(payload.get("source_event_ids",[]))
                event_ids.update(payload.get("handled_source_event_ids", []))
                event_ids.update(source['source_event_id'] for source in payload.get('source_outcomes',[]))
            if payload.get("episode_id") or payload.get("batch_id"):episode_ids.add(payload.get('episode_id') or payload['batch_id'])
            if payload.get("job_id"):job_ids.add(payload["job_id"])
            for task_id in (payload.get("task_id"), payload.get("fulfils_task_id"), payload.get("acknowledges_task_id")):
                if task_id and await self.job(task_id, scene_id):job_ids.add(task_id)
            if payload.get("action_id"):action_ids.add(payload["action_id"])
            if payload.get("origin_event_id"):event_ids.add(payload["origin_event_id"])
            if payload.get("result_id"):result_ids.add(payload["result_id"])
            for receipt in payload.get('operation_receipts', {}).values():
                event_ids.update(receipt['source_event_ids'])
                if receipt['kind'] == 'work':job_ids.add(receipt['target_id'])
                if receipt['action_id']:action_ids.add(receipt['action_id'])
            reply_field="reply_to" if event["event_type"] in {"MESSAGE_SENT","MESSAGE_SEND_FAILED","ACTION_SHADOWED"} else "reply_to_message_id"
            if payload.get(reply_field) is not None:
                quoted=await self._rows("SELECT id FROM events WHERE scene_id=? AND rowid<? AND CAST(json_extract(payload,'$.message_id') AS TEXT)=? ORDER BY rowid DESC LIMIT 1",
                                        [scene_id,event["rowid"],str(payload[reply_field])])
                if quoted:event_ids.add(quoted[0]["id"])
        source_clause,source_values=membership("value",event_ids)
        job_rows=await linked("SELECT j.id,j.scene_id,j.source_event_ids_json,j.result_ids_json,t.origin_episode_id",
            "FROM agent_jobs j JOIN tasks t ON j.id=t.id AND j.scene_id=t.scene_id WHERE j.scene_id=?",[
                membership("j.id",job_ids),membership("t.origin_episode_id",episode_ids),
                membership("json_extract(t.payload,'$.delivery_action_id')", action_ids),
                membership("json_extract(t.payload,'$.ack_action_id')", action_ids),
                membership("json_extract(t.payload,'$.delivery_event_id')", event_ids),
                ("EXISTS(SELECT 1 FROM json_each(j.source_event_ids_json) WHERE "+source_clause+")",source_values)],"t.created_at DESC,j.id DESC","jobs")
        jobs = []
        for job in job_rows:
            job_ids.add(job["id"]);event_ids.update(json.loads(job["source_event_ids_json"]));result_ids.update(json.loads(job["result_ids_json"]))
            if job["origin_episode_id"]:episode_ids.add(job["origin_episode_id"])
            detail = await self.job(job["id"], scene_id)
            if detail:
                jobs.append(detail)
                plugin_origin(detail.get('plugin_origin'))
                for field in ("request_source_event_id", "delivery_event_id"):
                    if detail.get(field):
                        event_ids.add(detail[field])
                for field in ("delivery_action_id", "ack_action_id"):
                    if detail.get(field):
                        action_ids.add(detail[field])
        native_clause, native_values = membership("ref_id", event_ids)
        native_episode_clause, native_episode_values = membership("json_extract(payload,'$.episode_id')", episode_ids)
        trace_rows=await linked("SELECT *","FROM traces WHERE scene_id=?",[
            membership("ref_id",episode_ids|job_ids),
            membership("json_extract(payload,'$.job_id')",job_ids),
            membership("json_extract(payload,'$.plugin_origin.source_event_id')",event_ids),
            membership("json_extract(payload,'$.plugin_origin.parent_run_id')",episode_ids|job_ids),
            ("kind IN ('calendar_command','live_announcement') AND " + native_clause, native_values),
            ("kind IN ('calendar_command','live_announcement') AND " + native_episode_clause, native_episode_values)],
            "created_at DESC,id DESC","traces")
        for row in trace_rows:
            payload=json.loads(row["payload"]);conversation=payload.get("conversation") or {}
            refs=conversation.get("references") or {}
            result_ids.update((refs.get("results") or {}).values())
            action_ids.update((payload.get("gate") or {}).get("action_ids",[]))
            for checkpoint in conversation.get('checkpoints',[]):
                action_ids.update(checkpoint['gate'].get('action_ids',[]))
            for run in self._trace_runs(row["kind"], payload):
                plugin_origin(run.get('plugin_origin'))
                result_ids.update(item['result_id'] for item in run.get('tool_results',[]))
                for commit in run.get('commits',[]):action_ids.update(commit.get('action_ids',[]))
                for step in run.get("steps", []):
                    for call in step.get("tool_calls", []):
                        observed_id = (call.get("observation") or {}).get("result_id")
                        if observed_id:
                            result_ids.add(observed_id)
            if row["kind"] in {"calendar_command", "live_announcement"}:
                action_ids.update(payload.get("action_ids", []))
                if payload.get("source_event_id"):
                    event_ids.add(payload["source_event_id"])
                if payload.get("episode_id"):
                    episode_ids.add(payload["episode_id"])
        observations=await linked("SELECT *","FROM tool_observations WHERE scene_id=?",[
            membership("id",result_ids),membership("event_id",event_ids)],"created_at DESC,id DESC","tool_results")
        for row in observations:plugin_origin(json.loads(row['result_json']).get('plugin_origin'))
        event_ids.update(row["event_id"] for row in observations)
        event_ids.update('turn:'+ident for ident in episode_ids)
        events=await linked("SELECT *,rowid","FROM events WHERE scene_id=?",[
            membership("id",event_ids),membership("json_extract(payload,'$.job_id')",job_ids),
            membership("json_extract(payload,'$.episode_id')",episode_ids),
            membership("json_extract(payload,'$.batch_id')",episode_ids),membership("json_extract(payload,'$.action_id')",action_ids)],"rowid DESC","events")
        calls=await linked("SELECT *","FROM model_calls WHERE scene_id=?",[
            membership("episode_id",episode_ids),membership("job_id",job_ids)],"started_at DESC,id DESC","calls")
        cutoff=max((row["rowid"] for row in events),default=0)
        event_views=await self._event_views(events,cutoff)
        actions={ident:{"id":ident,"scene_id":scene_id,"episode_id":None,"job_id":None,"origin_mode":None,
                        "job_revision":None,"origin_event_id":None,"request_source_event_id":None,"requester_qq_uid":None,
                        "acknowledges_task_id":None,"fulfils_task_id":None,"answer_basis":None,
                        "file_asset_id":None,"file_id":None,"attempt_event_ids":[],
                        "publication_status":None,"delivery_status":None,"simulated":False,"receipt_event_ids":[]} for ident in sorted(action_ids)}
        for event in event_views:
            if event['event_type'] != 'CONVERSATION_COMMITTED':
                continue
            payload = event['payload']
            for ident, message in zip(payload.get('action_ids', []), payload.get('outcome', {}).get('message_proposals', [])):
                if ident in actions:
                    actions[ident].update(episode_id=payload.get('episode_id',event['id'][5:]), commit_event_id=event['id'],
                        plugin_origin=message.get('plugin_origin') or payload.get('plugin_origin'),
                        checkpoint_index=payload.get('checkpoint_index'),
                        origin_event_id=message.get('source_event_id') or payload.get('origin_event_id'),
                        requester_qq_uid=message.get('requester_qq_uid'),
                        job_id=message.get('job_id'), job_revision=message.get('job_revision'),
                        file_asset_id=message.get('file_asset_id'),
                        answer_basis=message.get('answer_basis'),
                        operation_ref=message.get('operation_ref'), fulfils_task_id=message.get('fulfils_task_id'))
        for row in reversed(trace_rows):
            payload = json.loads(row['payload'])
            gates=[payload.get('gate') or {}, *(gate for run in self._trace_runs(row['kind'],payload)
                for gate in [*run.get('commits',[]),*(checkpoint['gate'] for checkpoint in run.get('checkpoints',[]))])]
            for gate in gates:
                publication = gate.get('publication') or {}
                for published in publication.get('actions', []):
                    action = actions.get(published['action_id'])
                    if action is not None:
                        action.update({key: published[key] for key in ('origin_event_id', 'requester_qq_uid',
                            'job_id', 'job_revision', 'operation_ref', 'fulfils_task_id', 'acknowledges_task_id') if key in published})
                        action.update(publication_status=published['status'], publication_phase=publication.get('phase'),
                            publication_error=publication.get('error'))
                        if gate.get('commit_event_id'):
                            action['commit_event_id'] = gate['commit_event_id']
        for job in jobs:
            if job.get("delivery_action_id") in actions:
                actions[job["delivery_action_id"]].update(job_id=job["id"], request_source_event_id=job.get("request_source_event_id"))
            if job.get("ack_action_id") in actions:
                actions[job["ack_action_id"]].update(acknowledges_task_id=job["id"], request_source_event_id=job.get("request_source_event_id"))
        for event in reversed(event_views):
            if event['event_type'] not in {'MESSAGE_SENT', 'MESSAGE_SEND_FAILED', 'FILE_UPLOADED',
                                          'FILE_UPLOAD_FAILED', 'ACTION_SHADOWED', 'DELIVERY_ATTEMPTED'}:
                continue
            ident=event["payload"].get("action_id")
            if not ident:continue
            action=actions.setdefault(ident,{"id":ident,"scene_id":scene_id,"receipt_event_ids":[],"attempt_event_ids":[]})
            payload = event['payload']
            if event['event_type'] == 'DELIVERY_ATTEMPTED':
                action['attempt_event_ids'].append(event['id'])
                if not action['receipt_event_ids']:
                    action['delivery_status'] = 'unknown'
                action.update({key: payload[key] for key in ('file_asset_id', 'job_id', 'origin_event_id') if key in payload})
                continue
            action.update(delivery_status=event["delivery_status"],simulated=event["simulated"],origin_mode=event["origin_mode"])
            action.update({key: payload[key] for key in ('answer_basis', 'plugin_origin', 'job_id', 'checkpoint_index',
                'job_revision', 'origin_event_id', 'requester_qq_uid', 'operation_ref', 'acknowledges_task_id',
                'fulfils_task_id', 'file_asset_id', 'file_id', 'file_name') if key in payload})
            if 'episode_id' in payload or 'batch_id' in payload:
                action['episode_id'] = payload.get('episode_id', payload.get('batch_id'))
            action["receipt_event_ids"].append(event["id"])
        tools=[]
        for row in observations:
            result=ToolResult.model_validate_json(row["result_json"])
            tools.append({**self._observation_view(result), "id":row["id"],"event_id":row["event_id"],
                          "scene_id":scene_id,"tool_name":row["tool_name"],"created_at":row["created_at"]})
        turns = []
        operations = []
        for event in event_views:
            if event["event_type"] != "CONVERSATION_COMMITTED":
                continue
            payload = event["payload"]
            episode = payload.get('episode_id',event['id'][5:] if event['id'].startswith('turn:') else None)
            for reference, receipt in payload.get('operation_receipts', {}).items():
                operation = {**receipt, 'proposal_ref': reference, 'commit_event_id': event['id'],
                             'committed_at': event['timestamp'], 'episode_id': episode}
                operations.append(operation)
                action = actions.get(receipt['action_id'])
                if (action is not None and (not action.get('episode_id') or action['episode_id'] == episode)
                        and action.get('operation_ref') in {None, reference}):
                    action['operation_receipt'] = operation
                    action['operation_ref'] = reference
                    action['episode_id'] = episode
            turns.append({"event_id":event["id"], "episode_id":episode,'checkpoint_index':payload.get('checkpoint_index'),
                          "read_source_event_ids":payload.get("source_event_ids", []),
                          'provided_result_ranges':payload.get('provided_result_ranges'),
                          'provided_work_results':payload.get('provided_work_results'),
                          "handled_source_event_ids":[source['source_event_id'] for source in payload['source_outcomes']]
                              if 'source_outcomes' in payload else payload.get('handled_source_event_ids'),
                          'source_outcomes':payload.get('source_outcomes'),
                          "outcome":payload.get("outcome"), 'operation_receipts': payload.get('operation_receipts', {})})
        session = await self.runtime.event_store.load_scene_session(scene_id) if event_id else None
        source_handling = ({"event_id":event_id,
                            "pending":any(wake["event_id"] == event_id for wake in session.get("pending_wakes", [])) if session else None}
                           if event_id else None)
        truncated['operation_receipts'] = len(operations) > limit
        return {"events":event_views,"traces":[self._trace(row, identities=True) for row in trace_rows],"calls":[self._call(row) for row in calls],
                "jobs":jobs,"actions":list(actions.values())[:limit],"tool_results":tools,"batches":[],
                "turns":turns,"source_handling":source_handling,'operation_receipts':operations[:limit],
                "limits":{name:limit for name in ("events","traces","calls","jobs","actions","tool_results","batches","operation_receipts")},
                "truncated":{**truncated,"actions":len(actions)>limit,"batches":False}}

    async def event_diagnostics(self, event_id: str, scene_id: str):
        """Export a bounded identity/status projection, never whole objects.

        The existing relation query owns scope and limits. Its wider episode
        context is not evidence that every related object belongs to one input.
        """
        started = self.current_time()
        related = await self.relations(scene_id, event_id=event_id)
        if related is None:
            return None

        def fields(record, names):
            return {name: record[name] for name in names if name in record}

        events = []
        for event in related['events']:
            payload = event['payload']
            events.append({**fields(event, ('id', 'event_type', 'timestamp', 'delivery_status', 'simulated', 'origin_mode')),
                'references': fields(payload, ('action_id', 'episode_id', 'job_id', 'job_revision',
                    'origin_event_id', 'covered_source_event_ids', 'file_asset_id', 'file_id')),
                'timings_ms': fields(payload, ('queue_ms', 'send_ms', 'event_to_delivery_ms')),
                'error_recorded': bool(payload.get('error'))})
        calls = []
        for call in related['calls']:
            usage = call.get('usage')
            calls.append({**fields(call, ('id', 'episode_id', 'job_id', 'batch_id', 'purpose', 'status',
                'disposition', 'started_at', 'ended_at')),
                'usage': None if usage is None else {
                    **fields(usage, ('prompt_tokens', 'completion_tokens', 'total_tokens')),
                    'cached_tokens': (usage.get('prompt_tokens_details') or {}).get('cached_tokens'),
                    'reasoning_tokens': (usage.get('completion_tokens_details') or {}).get('reasoning_tokens')},
                'estimated_input_tokens': (call.get('estimate') or {}).get('input_tokens'),
                'error_recorded': bool(call.get('error_type'))})
        return {'format_version': 1, 'root': {'scene_id': scene_id, 'event_id': event_id},
            'read_started_at': started, 'read_finished_at': self.current_time(),
            'scope': 'bounded_related_records',
            'limitations': ['关联可能包含同轮其他来源，不是单条消息的独占消耗或依据。',
                '读取跨多个查询，不是数据库原子快照；读取期间状态可能变化。',
                '缺字段不表示零消耗、未执行或已成功；截断类别仅包含当前受限结果。',
                '只读导出不执行模型、工具或平台动作，不证明材料已读或平台送达。',
                '文件仍含场景和业务记录编号，分享前须人工核对接收范围。'],
            'not_exported': ['message_bodies', 'personas', 'tool_arguments', 'tool_bodies',
                'error_text', 'request_snapshots', 'provider_continuations', 'configuration', 'media', 'credentials'],
            'limits': related['limits'], 'truncated': related['truncated'],
            'source_handling': related['source_handling'], 'events': events, 'calls': calls,
            'turns': [{**fields(turn, ('event_id', 'episode_id', 'checkpoint_index', 'read_source_event_ids',
                        'handled_source_event_ids')),
                'source_outcomes': None if turn.get('source_outcomes') is None else [
                    {**fields(outcome, ('source_event_id', 'status', 'action_ids', 'task_ids')),
                     'reason_recorded': bool(outcome.get('reason')),
                     'unfinished_count': len(outcome['unfinished']) if 'unfinished' in outcome else None}
                    for outcome in turn['source_outcomes']]}
                for turn in related['turns']],
            'traces': [{**fields(trace, ('id', 'kind', 'ref_id', 'created_at', 'committed',
                        'publication_status', 'error_phase', 'gate_accepted', 'source_event_ids', 'read_source_event_ids', 'commit_event_ids')),
                'error_recorded': bool(trace.get('error')),
                'publication_error_recorded': bool(trace.get('publication_error'))}
                for trace in related['traces']],
            'actions': [fields(action, ('id', 'episode_id', 'commit_event_id', 'checkpoint_index',
                'origin_event_id', 'request_source_event_id', 'job_id', 'job_revision', 'file_asset_id', 'file_id',
                'publication_status', 'delivery_status', 'origin_mode', 'simulated', 'receipt_event_ids', 'attempt_event_ids'))
                for action in related['actions']],
            'jobs': [fields(job, ('id', 'revision', 'status', 'execution_status', 'delivery_required',
                'request_source_event_id', 'ack_action_id', 'delivery_action_id', 'delivery_event_id'))
                for job in related['jobs']],
            'tool_results': [fields(result, ('id', 'event_id', 'tool_name', 'status', 'content_length', 'created_at'))
                for result in related['tool_results']]}

    @staticmethod
    def _participation(event, metadata, delivery):
        reasons = list(metadata.get('attention_reasons') or [])
        lane = metadata.get('attention_lane') or ('fast' if metadata.get('attention_certain') else 'slow' if reasons else 'none')
        if metadata.get('conversation_excluded'):
            stage, title = 'excluded', '交互已排除出普通聊天'
        elif not reasons:
            stage, title = 'no_opportunity', '没有观察机会'
        elif delivery == 'unknown':
            stage, title = 'attempted_unknown', '已尝试未知'
        elif delivery in {'sent', 'shadow', 'simulated'}:
            stage, title = 'delivered', {'sent': '实际送达', 'shadow': 'Shadow 记录', 'simulated': '模拟回执'}[delivery]
        elif metadata.get('attention_certain'):
            stage, title = 'waiting_resource', '等待资源或尚未提交'
        else:
            stage, title = 'slow_opportunity', '普通观察机会，可以沉默'
        return {'lane': lane, 'stage': stage, 'title': title, 'reasons': reasons}

    def metrics(self) -> dict:
        return self.runtime.metrics.snapshot()

    def plugins(self) -> list[dict]:
        """Every declared plugin with a credential-free configuration.

        The credential scan follows the declared schema at every depth, not
        just the first level: a service token nested inside a backend object is
        the same secret as a top-level ``sessdata``, and a list or save
        response that carried it would be an authenticated-interface
        disclosure.  The response says only whether each credential is set;
        the value itself never leaves the root file.
        """
        from len_bot.plugins.credentials import public_config, schema_secret_fields, secret_values

        result = []
        root = self.runtime.config_store.current
        for active in self.runtime.plugin_host.status_snapshot():
            item = copy.deepcopy(active)
            plugin_id = item['id']
            saved = root.plugins.get(plugin_id)
            item['active_enabled'] = bool(item['enabled'] and item['state'] == 'enabled')
            item['enabled'] = bool(saved and saved.enabled)
            item['configured'] = bool(saved and saved.config is not None)
            scene_schema = item.get("scene_config_schema") or {}
            item['open_scenes'] = [
                {'scene_id': scene_id,
                 'enabled': scene.enabled and scene.plugins[plugin_id].enabled,
                 # A per-scene plugin config is read through the same
                 # credential path as the global one: a scene-scoped model may
                 # gain a token field later, and it must not start leaking just
                 # because it was added to the scene schema instead.
                 'config': public_config(scene.plugins[plugin_id].config, scene_schema)[0]}
                for scene_id, scene in root.scenes.items() if plugin_id in scene.plugins]
            result.append(item)
        for item in result:
            schema = item.get("config_schema") or {}
            raw = item.get("config")
            config, config_set, secret_paths = public_config(raw, schema)
            item["config"] = config
            item["config_set"] = config_set
            item["secret_paths"] = secret_paths
            stored = root.plugins.get(item['id'])
            item["credential_revision"] = int(stored.credential_revision) if stored else 1
            # Top-level names keep the existing field-level rendering; a nested
            # path is reported only as "set", since a compound field is edited
            # as one JSON value.
            item["secret_fields"] = sorted({path for path in config_set if "." not in path})
            hidden_values = secret_values(raw, schema)
            for field in schema_secret_fields(schema, set(secret_paths)):
                field["writeOnly"] = True
                for key in ("default", "example", "examples"):
                    field.pop(key, None)
            if item.get("last_error"):
                for value in hidden_values:
                    item["last_error"] = item["last_error"].replace(value, "[已隐藏凭据]")
            source = item["source_status"]
            if source.get("last_error"):
                for value in hidden_values:
                    source["last_error"] = source["last_error"].replace(value, "[已隐藏凭据]")
        return result

    async def provider_models(self, provider_id):
        return await self.runtime.provider_registry.list_models(provider_id)

    def model_configuration(self) -> dict:
        """Private control input; credentials never pass through a public response."""
        data = self.runtime.provider_registry.export()
        data["retrieval"] = self.runtime.config_store.current.models.retrieval.model_dump()
        return data

    def model_settings_draft(self):
        saved = self.runtime.config_store.current.models.model_dump()
        for provider in saved['providers']:
            provider['api_key_masked'] = '已设置' if provider.pop('api_key', '') else ''
        effective = self.providers()
        effective['retrieval'] = self.runtime.retrieval_profiles.model_dump() if self.runtime.retrieval_profiles else None
        for provider in effective['providers']:
            provider['api_key_masked'] = '已设置' if provider['api_key_masked'] else ''
        return {**saved, 'effective': effective, 'apply': 'next_run'}

    def providers(self) -> dict:
        data = self.runtime.provider_registry.snapshot()
        data["retrieval"] = self.runtime.config_store.current.models.retrieval.model_dump()
        return data

    async def memory_index_status(self, scene_id: str) -> dict:
        index = self.runtime.memory_index
        profile = index.profile if index else None
        scene_enabled = self.runtime.semantic_retrieval_enabled(scene_id)
        now = self.current_time()
        if index is None:
            total = (await self._rows("SELECT COUNT(*) AS total FROM memories WHERE scope=? AND status='active' AND (expires_at IS NULL OR expires_at>?)", [scene_id, now]))[0]['total']
            summaries = (await self._rows("SELECT COUNT(*) AS total FROM history_batches WHERE scene_id=? AND status='completed'", [scene_id]))[0]['total']
            coverage = {'total': total, 'indexed': None, 'pending': None}
            summary_coverage = {'total': summaries, 'indexed': None, 'pending': None}
        else:
            coverage = await index.coverage(scene_id)
            summary_coverage = await index.summary_coverage(scene_id)
        reason = ('索引尚未初始化' if index is None else
                  '该场景未开启语义检索；不会为其发起语义检索请求' if not scene_enabled else
                  '未配置嵌入绑定' if profile is None else
                  '检索客户端尚未初始化' if index.retrieval_models is None else None)
        errors = await self._rows("""SELECT id,payload,created_at FROM traces WHERE scene_id=?
            AND (kind='memory_index_error' OR (kind='memory_index_rebuild' AND json_extract(payload,'$.status')='error'))
            ORDER BY created_at DESC,id DESC LIMIT 1""", [scene_id])
        last_error = None
        if errors:
            try: last_error = {**self._public(json.loads(errors[0]['payload'])), 'created_at': errors[0]['created_at'], 'trace_id': errors[0]['id']}
            except (TypeError, ValueError, json.JSONDecodeError): last_error = {'error': '索引错误记录无法解析'}
        rebuilds = await self._rows("SELECT id,payload,created_at FROM traces WHERE scene_id=? AND kind='memory_index_rebuild' ORDER BY created_at DESC,id DESC LIMIT 1", [scene_id])
        last_rebuild = ({**self._public(json.loads(rebuilds[0]['payload'])), 'created_at': rebuilds[0]['created_at'], 'trace_id': rebuilds[0]['id']} if rebuilds else None)
        return {"enabled": reason is None, "reason": reason, "initialized": index is not None,
                "scene_enabled": scene_enabled, "sampled_at": now, "last_rebuild": last_rebuild,
                "profile": profile.model_dump() if profile else None, "last_error": last_error,
                "summary_coverage": summary_coverage, **coverage}

    def shadow_would_send(self, limit: int = 100) -> list[dict]:
        return list(self.runtime.shadow_would_send_log)[-limit:][::-1]

    def delivery_settings(self) -> dict:
        return {"enabled": self.runtime.shadow_mode}
