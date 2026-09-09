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
from len_bot.events.models import Event, EventType
from len_bot.tools.results import ToolResult
from typing import Optional
from len_bot.scenes.models import SceneSession
from len_bot.memory.store import MEMORY_COLUMNS, memory_from_row


class RuntimeQueryService:
    def __init__(self, runtime):
        self.runtime = runtime

    def current_time(self) -> float:
        return self.runtime.event_store.clock()

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
                'content_length': len(observation.content)}

    async def tool_results(self, scene_id, page=1, page_size=30):
        result = await self._page("SELECT id,event_id,tool_name,result_json,created_at", "FROM tool_observations WHERE scene_id=?",
                                  [scene_id], "created_at DESC,id DESC", page, page_size)
        for item in result["items"]:
            observation = ToolResult.model_validate_json(item.pop("result_json"))
            item["result"] = self._observation_view(observation)
            item["content_length"] = len(observation.content)
        return result

    async def tool_result(self, scene_id, result_id, offset=0):
        result = await self.runtime.event_store.read_tool_observation(result_id, [scene_id])
        if result is None:
            return None
        page = self._public(result.page(offset, self.runtime.config.tool_result_page_chars).model_dump())
        page["content_length"] = len(result.content)
        if result.source_next_call:
            page["source_next_call"] = self._public(result.source_next_call.model_dump())
            page["source_next_call_note"] = "源端下一批，仅位置未取得"
        return page

    def attention_settings(self):
        return {key: getattr(self.runtime.config, key) for key in (
            "attention_keywords", "attention_sample_window_seconds", "attention_sample_probability",
            "attention_keyword_cooldown_seconds", "attention_focus_seconds", "conversation_recent_tokens")}

    def access_settings(self):
        return self.runtime.config_store.current.access.model_dump()

    def time_settings(self):
        settings = self.runtime.config_store.current.time
        return settings.model_dump() if settings is not None else None

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
        else:
            effect = "普通成员闲聊仅保存；QQ 白名单仍可正常提问，命令与公告按本群选项执行。"
        if self.runtime.shadow_mode:
            effect += " 当前全局 Shadow 开启，不实际发送。"
        return {"scene_id": scene_id, "configured": settings is not None,
                "settings": settings.model_dump() if settings is not None else None,
                "effect": effect, "members": self.member_settings(),
                "plugins": [{key: item[key] for key in ("id", "name", "configured", "enabled")}
                            for item in self.plugins()]}

    def maintenance_readiness(self):
        snapshot = self.providers()
        profile = snapshot["routing"]["maintenance"] if snapshot["routing"] else None
        if profile is None:
            return {"configured": False, "ready": False, "reason": "未配置维护模型"}
        provider = next((item for item in snapshot["providers"] if item["id"] == profile["provider_id"]), None)
        ready = bool(provider and provider["enabled"] and provider["api_key_masked"])
        return {"configured": True, "ready": ready, "reason": "已就绪" if ready else "维护供应商未启用或未设置密钥"}

    @staticmethod
    def _call(item):
        item = dict(item)
        usage = item.pop("usage_json")
        item["usage"] = json.loads(usage) if usage else None
        item["estimate"] = json.loads(item.pop("estimate_json"))
        return RuntimeQueryService._public(item)

    async def model_usage(self, scene_id=None, *, since=None, until=None, purpose=None, status=None, page=1, page_size=30):
        source, params = "FROM model_calls WHERE 1=1", []
        for column,value in (("scene_id",scene_id),("purpose",purpose),("status",status)):
            if value is not None:
                source += f" AND {column}=?"; params.append(value)
        for op,value in ((">=",since),("<=",until)):
            if value is not None:
                source += f" AND started_at{op}?"; params.append(value)
        result = await self._page("SELECT *", source, params, "started_at DESC,id DESC", page, page_size)
        result["items"] = [self._call(item) for item in result["items"]]
        known = "json_type(usage_json,'$.prompt_tokens') IN ('integer','real') AND json_type(usage_json,'$.completion_tokens') IN ('integer','real')"
        fields = ["purpose", "disposition", "COUNT(*) AS calls"]
        fields += [f"SUM(status='{name}') AS {name}" for name in ("completed","failed","cancelled","unconfirmed")]
        fields += [f"SUM(CASE WHEN {known} THEN 0 ELSE 1 END) AS unknown_usage"]
        for name,path in (("prompt_tokens","prompt_tokens"),("completion_tokens","completion_tokens"),
                          ("cached_tokens","prompt_tokens_details.cached_tokens"),("reasoning_tokens","completion_tokens_details.reasoning_tokens")):
            fields.append(f"SUM(CASE WHEN json_type(usage_json,'$.{path}') IN ('integer','real') THEN json_extract(usage_json,'$.{path}') ELSE 0 END) AS {name}")
        fields.append("SUM(json_extract(estimate_json,'$.input_tokens')) AS estimated_input_tokens")
        result["totals"] = await self._rows("SELECT " + ",".join(fields) + " " + source + " GROUP BY purpose,disposition ORDER BY purpose,disposition", params)
        result["filters"] = {"scene_id":scene_id,"since":since,"until":until,"purpose":purpose,"status":status}
        result["cost"] = {"status":"unverified","amount":None,"reason":"尚未提供可核实的供应商价格或账单；未知 usage 不按零成本计入"}
        return result

    async def model_call(self, call_id, scene_id=None):
        rows = await self._rows("SELECT * FROM model_calls WHERE id=? AND (? IS NULL OR scene_id=?)", [call_id,scene_id,scene_id])
        return self._call(rows[0]) if rows else None

    async def history_batches(self, scene_id, page=1, page_size=30):
        result = await self._page("SELECT *", "FROM history_batches WHERE scene_id=?", [scene_id], "end_rowid DESC,end_offset DESC,id DESC", page,page_size)
        result["items"] = [self.runtime.event_store._history_row(item) for item in result["items"]]
        return result

    async def history_batch(self, batch_id):
        rows = await self._rows("SELECT * FROM history_batches WHERE id=?", [batch_id])
        return self.runtime.event_store._history_row(rows[0]) if rows else None

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
        return result

    async def skill(self, skill_id, scene_id, version=None):
        result = await self.runtime.event_store.read_skill(skill_id,scene_id,version=version)
        if result is None:return None
        result["versions"] = await self._rows("""SELECT v.version,v.scope,v.created_at FROM skill_versions v JOIN skills s ON s.id=v.skill_id
            WHERE v.skill_id=? AND (s.scene_id=? OR v.scope='global-safe') ORDER BY v.version DESC""",[skill_id,scene_id])
        return self._public(result)

    def job_budget(self):
        config = self.runtime.config
        return {"max_model_steps": config.job_max_steps, "max_tool_calls": config.job_max_tool_calls,
                "max_seconds": config.job_max_seconds, "context_tokens": config.job_context_tokens,
                "output_tokens": config.work_output_tokens,
                "effective_input_tokens": config.job_context_tokens - config.work_output_tokens,
                "compression_trigger": config.job_compress_trigger, "compression_target": config.job_compress_target}

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
            items.append({**self._public(job),"budget":self.job_budget()})
        result["items"]=items
        return result

    async def job(self, job_id, scene_id=None):
        task=await self.get_task(job_id,scene_id)
        job=await self.runtime.event_store.get_job(job_id,task["scene_id"]) if task else None
        if job and job['can_resume']:
            job['resume_issue']=self.runtime.job_resume_issue(job)
            job['can_resume']=job['resume_issue'] is None
        return {**self._public(job),"budget":self.job_budget()} if job else None

    @staticmethod
    def public_asset(asset):
        return {key:asset[key] for key in ("id","scope","source_event_id","mime_type","description","tags","enabled","curated","created_at","palette_order")}

    async def media_assets(self, scene_id, query="", *, curated=None, enabled=None, palette_only=False, page=1, page_size=48):
        source="FROM media_assets WHERE scope IN (?, 'global-safe')";params=[scene_id]
        for field,value in (("curated",curated),("enabled",enabled)):
            if value is not None:source+=f" AND {field}=?";params.append(int(value))
        if palette_only:source+=" AND palette_order IS NOT NULL AND curated=1 AND enabled=1"
        terms=list(dict.fromkeys(query.split()))
        if terms:
            source+=" AND ("+" OR ".join("(instr(lower(description),lower(?))>0 OR instr(lower(tags_json),lower(?))>0)" for _ in terms)+")"
            params.extend(value for term in terms for value in (term,term))
        result=await self._page("SELECT *",source,params,"created_at DESC,id DESC",page,page_size)
        for item in result["items"]:
            item["tags"]=json.loads(item.pop("tags_json"));item["enabled"]=bool(item["enabled"]);item["curated"]=bool(item["curated"])
        result["items"]=[self.public_asset(item) for item in result["items"]]
        return result

    async def media_asset(self, asset_id, scene_id):
        asset=await self.runtime.event_store.get_media(asset_id,[scene_id,"global-safe"],include_disabled=True)
        return self.public_asset(asset) if asset else None

    async def media_palette(self, scene_id):
        items=[self.public_asset(asset) for asset in await self.runtime.event_store.list_palette(scene_id, limit=self.runtime.config.media_palette_limit)]
        return {"items":items,"total":len(items),"complete":True}

    async def media_file(self, asset_id, scene_id):
        return await self.runtime.media_service.get_bytes(asset_id, scene_id, include_disabled=True)

    def persona_settings(self):
        return {key:getattr(self.runtime.config,key) for key in (
            "character_context","identity_name","identity_core","identity_persona","conversation_style","address_names","bot_qq")}

    async def preview_diana_persona(self):
        return await self.runtime.event_store.preview_diana_persona(palette_limit=self.runtime.config.media_palette_limit)

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
            access_token_set=bool(configured.onebot_access_token), requires_restart=self.runtime.restart_required)
        return status

    def runtime_settings(self):
        from len_bot.config import EXECUTION_BUDGET_FIELDS
        excluded = {"ws_host", "ws_port", "address_names", "character_context",
                    "conversation_style", "dashboard_secret_key", "dashboard_default_admin_password"}
        values = self.runtime.config_store.current.runtime.model_dump()
        settings = {key:value for key,value in values.items()
                    if key not in excluded and not key.startswith(("identity_", "onebot_"))}
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
            "history_status":status,"maintenance":self.maintenance_readiness(),"sampled_at":self.current_time()}

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
                delivery=payload.get("delivery_status")
                if event.event_type==EventType.ACTION_SHADOWED:delivery="shadow"
                elif payload.get("delivery_unknown"):delivery="unknown"
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
                    "attention":{key:metadata[key] for key in ("attention_reasons","attention_certain") if key in metadata},
                    "interaction": {key:metadata[key] for key in ("interaction", "interaction_reason", "requester_qq_uid",
                                       "command_id", "calendar_parent_event_id", "conversation_excluded") if key in metadata}
                                   if "interaction" in metadata else None,
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
        if kind=="reminder":source+=" AND COALESCE(json_extract(payload,'$.kind'),'reminder')!='agent_job'"
        elif kind=="agent_job":source+=" AND json_extract(payload,'$.kind')='agent_job'"
        result=await self._page("SELECT *",source,params,"created_at DESC,id DESC",page,page_size)
        result["items"]=[self._task(item) for item in result["items"]]
        return result

    async def get_task(self, task_id, scene_id=None):
        rows=await self._rows("SELECT * FROM tasks WHERE id=? AND (? IS NULL OR scene_id=?)",[task_id,scene_id,scene_id])
        return self._task(rows[0]) if rows else None

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

    async def list_memories(self, status=None, scope=None, subject=None, *, kind=None, query="", page=1, page_size=30):
        source="FROM memories WHERE 1=1";params=[]
        for field,value in (("status",status),("scope",scope),("subject",subject),("kind",kind)):
            if value is not None:source+=f" AND {field}=?";params.append(value)
        if query:source+=" AND instr(lower(statement),lower(?))>0";params.append(query)
        result=await self._page("SELECT "+','.join(MEMORY_COLUMNS),source,params,"created_at DESC,id DESC",page,page_size)
        result["items"]=[memory_from_row(tuple(item[column] for column in MEMORY_COLUMNS)).model_dump(mode="json") for item in result["items"]]
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
        if kind.startswith("agent_job"):
            return payload.get("runs") or []
        if kind.startswith("history_maintenance"):
            return [payload.get("cognition") or {}]
        return [payload.get("conversation") or payload]

    @staticmethod
    def _trace(item, detail=False):
        item=dict(item);payload=RuntimeQueryService._public(json.loads(item.pop("payload")))
        conversation=payload.get("conversation") or {}
        result = payload.get("result") or {}
        gate = payload.get('gate') or {}
        publication = gate.get('publication') or {}
        item['committed'] = gate.get('committed')
        item['publication_status'] = publication.get('status')
        item["summary"]=(payload.get("error") or result.get("decision_reason") or result.get("reason")
                         or conversation.get("failure_reason") or result.get("summary") or payload.get("kind") or item["kind"])[:300]
        item["result_status"] = result.get("status")
        if item["kind"] in {"calendar_command", "live_announcement"}:
            label = "日程命令" if item["kind"] == "calendar_command" else "开播邀请"
            state = {"generating":"生成中", "committed":"已提交待回执", "failed":"失败", "interrupted":"已中断"}.get(payload.get("state"), payload.get("state", ""))
            item["summary"] = f"{label} · {state} · {payload.get('member') or payload.get('command_id', '')}"
            if payload.get("error"):
                item["summary"] += " · " + payload["error"][:200]
        if gate.get('committed') and publication.get('status') in {'failed', 'interrupted'}:
            state = '发布失败' if publication['status'] == 'failed' else '发布中断'
            item['summary'] = f"已提交，{state} · {publication.get('phase')} · {publication.get('error')}"[:300]
        elif gate.get('committed') and item['kind'] == 'conversation_error':
            item['summary'] = '已提交，后续处理异常 · ' + item['summary']
        calls = [call for run in RuntimeQueryService._trace_runs(item["kind"], payload)
                 for step in run.get("steps", []) for call in step.get("tool_calls", [])]
        observations = [call.get("observation_status") or (call.get("observation") or {}).get("status") for call in calls]
        item["tool_outcomes"] = {"returned": sum(call.get("status") == "completed" for call in calls),
                                 "errors": sum(status in {"error", "unsupported"} or call.get("status") in {"error", "invalid_arguments"}
                                               for call, status in zip(calls, observations)),
                                 "no_results": observations.count("no_results")}
        if detail:item["payload"]=payload
        return item

    async def query_traces(self, scene_id=None, kind=None, ref_id=None, *, episode_id=None, since=None, until=None, page=1, page_size=30):
        source="FROM traces WHERE 1=1";params=[]
        for field,value in (("scene_id",scene_id),("kind",kind),("ref_id",ref_id)):
            if value is not None:source+=f" AND {field}=?";params.append(value)
        if episode_id is not None:
            source += " AND ((kind IN ('conversation','conversation_error') AND ref_id=?) OR json_extract(payload,'$.episode_id')=?)"
            params.extend([episode_id, episode_id])
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

        if event_id and await self.event(event_id,scene_id) is None:return None
        if job_id and await self.job(job_id,scene_id) is None:return None
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

        # A committed turn explicitly records all originals read in that turn.
        source_clause,source_values=membership("value",event_ids)
        commits=await linked("SELECT *,rowid","FROM events WHERE scene_id=?",[
            membership("id",event_ids|{'turn:'+ident for ident in episode_ids}),
            ("event_type='CONVERSATION_COMMITTED' AND EXISTS(SELECT 1 FROM json_each(payload,'$.source_event_ids') WHERE "+source_clause+")",source_values),
            membership("json_extract(payload,'$.origin_event_id')", event_ids),
            membership("json_extract(payload,'$.batch_id')",episode_ids),
            membership("json_extract(payload,'$.episode_id')",episode_ids),
            membership("json_extract(payload,'$.action_id')",action_ids)],"rowid DESC","events")
        for event in commits:
            event_ids.add(event["id"]);payload=json.loads(event["payload"])
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
                        "acknowledges_task_id":None,"fulfils_task_id":None,
                        "publication_status":None,"delivery_status":None,"simulated":False,"receipt_event_ids":[]} for ident in sorted(action_ids)}
        for event in event_views:
            if event['event_type'] != 'CONVERSATION_COMMITTED':
                continue
            payload = event['payload']
            for ident, message in zip(payload.get('action_ids', []), payload.get('outcome', {}).get('message_proposals', [])):
                if ident in actions:
                    actions[ident].update(episode_id=payload.get('episode_id',event['id'][5:]), commit_event_id=event['id'],
                        checkpoint_index=payload.get('checkpoint_index'),
                        origin_event_id=message.get('source_event_id') or payload.get('origin_event_id'),
                        requester_qq_uid=message.get('requester_qq_uid'),
                        job_id=message.get('job_id'), job_revision=message.get('job_revision'),
                        operation_ref=message.get('operation_ref'), fulfils_task_id=message.get('fulfils_task_id'))
        for row in reversed(trace_rows):
            payload = json.loads(row['payload'])
            gates=[payload.get('gate') or {}, *(checkpoint['gate'] for checkpoint in
                (payload.get('conversation') or {}).get('checkpoints',[]))]
            for gate in gates:
                publication = gate.get('publication') or {}
                for published in publication.get('actions', []):
                    action = actions.get(published['action_id'])
                    if action is not None:
                        action.update({key: published.get(key) for key in ('origin_event_id', 'requester_qq_uid',
                            'job_id', 'job_revision', 'operation_ref', 'fulfils_task_id', 'acknowledges_task_id')})
                        action.update(publication_status=published['status'], publication_phase=publication.get('phase'),
                            publication_error=publication.get('error'), commit_event_id=gate.get('commit_event_id'))
        for job in jobs:
            if job.get("delivery_action_id") in actions:
                actions[job["delivery_action_id"]].update(job_id=job["id"], request_source_event_id=job.get("request_source_event_id"))
            if job.get("ack_action_id") in actions:
                actions[job["ack_action_id"]].update(acknowledges_task_id=job["id"], request_source_event_id=job.get("request_source_event_id"))
        for event in reversed(event_views):
            ident=event["payload"].get("action_id")
            if not ident:continue
            action=actions.setdefault(ident,{"id":ident,"scene_id":scene_id,"receipt_event_ids":[]})
            action.update(delivery_status=event["delivery_status"],simulated=event["simulated"],origin_mode=event["origin_mode"],
                          episode_id=event["payload"].get('episode_id',event["payload"].get("batch_id")),job_id=event["payload"].get("job_id"),
                          checkpoint_index=event['payload'].get('checkpoint_index'),
                          job_revision=event["payload"].get("job_revision"),origin_event_id=event["payload"].get("origin_event_id"),
                          requester_qq_uid=event["payload"].get("requester_qq_uid"),
                          operation_ref=event['payload'].get('operation_ref'),
                          acknowledges_task_id=event["payload"].get("acknowledges_task_id", action.get("acknowledges_task_id")),
                          fulfils_task_id=event["payload"].get("fulfils_task_id"))
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
                          "handled_source_event_ids":[source['source_event_id'] for source in payload['source_outcomes']]
                              if 'source_outcomes' in payload else payload.get('handled_source_event_ids'),
                          'source_outcomes':payload.get('source_outcomes'),
                          "outcome":payload.get("outcome"), 'operation_receipts': payload.get('operation_receipts', {})})
        session = await self.runtime.event_store.load_scene_session(scene_id) if event_id else None
        source_handling = ({"event_id":event_id,
                            "pending":any(wake["event_id"] == event_id for wake in session.get("pending_wakes", [])) if session else None}
                           if event_id else None)
        truncated['operation_receipts'] = len(operations) > limit
        return {"events":event_views,"traces":[self._trace(row) for row in trace_rows],"calls":[self._call(row) for row in calls],
                "jobs":jobs,"actions":list(actions.values())[:limit],"tool_results":tools,"batches":[],
                "turns":turns,"source_handling":source_handling,'operation_receipts':operations[:limit],
                "limits":{name:limit for name in ("events","traces","calls","jobs","actions","tool_results","batches","operation_receipts")},
                "truncated":{**truncated,"actions":len(actions)>limit,"batches":False}}

    def metrics(self) -> dict:
        return self.runtime.metrics.snapshot()

    def plugins(self) -> list[dict]:
        result = []
        root = self.runtime.config_store.current
        for active in self.runtime.plugin_host.status_snapshot():
            item = copy.deepcopy(active)
            plugin_id = item['id']
            saved = root.plugins.get(plugin_id)
            item['active_enabled'] = bool(item['enabled'] and item['state'] == 'enabled')
            item['enabled'] = bool(saved and saved.enabled)
            item['configured'] = bool(saved and saved.config is not None)
            item['open_scenes'] = [{'scene_id': scene_id, 'enabled': scene.enabled}
                for scene_id, scene in root.scenes.items() if plugin_id in scene.plugins]
            result.append(item)
        credential_names={"sessdata","bili_jct","api_key","access_token","refresh_token","token","password","secret","cookie","authorization"}
        for item in result:
            config = item["config"]
            properties=item["config_schema"].get("properties",{})
            secrets={name for name,field in properties.items() if name.lower() in credential_names
                     or field.get("writeOnly") or field.get("format")=="password"}
            secrets.update(name for name in (config or {}) if name.lower() in credential_names)
            item["secret_fields"]=sorted(secrets)
            item["config_set"]={name:bool(config and config.get(name)) for name in secrets}
            hidden_values=[]
            if secrets:
                for key in ("default","example","examples"):item["config_schema"].pop(key,None)
            for name in secrets:
                value = config.pop(name, None) if config is not None else None
                if isinstance(value,str) and value:hidden_values.append(value)
                if name in properties:
                    properties[name]["writeOnly"]=True
                    for key in ("default","example","examples"):properties[name].pop(key,None)
            if item.get("last_error"):
                for value in hidden_values:item["last_error"]=item["last_error"].replace(value,"[已隐藏凭据]")
            source = item["source_status"]
            if source.get("last_error"):
                for value in hidden_values:
                    source["last_error"] = source["last_error"].replace(value, "[已隐藏凭据]")
        return result

    async def provider_models(self, provider_id):
        return await self.runtime.provider_registry.list_models(provider_id)

    def model_configuration(self) -> dict:
        """Private control input; credentials never pass through a public response."""
        return self.runtime.provider_registry.export()

    def providers(self) -> dict:
        return self.runtime.provider_registry.snapshot()

    def shadow_would_send(self, limit: int = 100) -> list[dict]:
        return list(self.runtime.shadow_would_send_log)[-limit:][::-1]

    def delivery_settings(self) -> dict:
        return {"enabled": self.runtime.shadow_mode}
