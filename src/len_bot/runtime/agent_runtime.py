"""Runtime lifecycle and the single event-owned conversation/work handoff."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
import json
import logging
import time
from typing import Any
import uuid

from len_bot.actions.models import ActionItem, DeliveryResult
from len_bot.actions.queue import ActionQueue
from len_bot.cognition.agent_loop import CommitConflict, FreshInputConflict, _error_text
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.models import ConversationResume, EpisodeOutcome, FinalDisposition
from len_bot.cognition.providers import ModelProfile, ProviderConfig, ProviderRegistry, RoutingConfig
from len_bot.cognition.retrieval_models import RetrievalModels
from len_bot.cognition.social_core import SocialCognitionCore
from len_bot.config import RuntimeConfig
from len_bot.config_store import ConfigStore, RootConfig
from len_bot.events.builder import BurstAssembler
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.events.store import EventStore
from len_bot.memory.history import HistoryConflictError
from len_bot.media.service import MediaService
from len_bot.memory.interests import InterestStore
from len_bot.memory.reflection import ReflectionEngine
from len_bot.memory.reflector import LLMReflector
from len_bot.memory.store import MemoryStore
from len_bot.memory.index import MemoryIndex
from len_bot.plugins.host import PluginHost, PluginConfigurationApplyError
from len_bot.runtime.gate import GateDecision, RuntimeGate
from len_bot.runtime.capabilities import CapabilityAuthority
from len_bot.runtime.job_runner import InformationJobRunner
from len_bot.runtime.metrics import RuntimeMetrics
from len_bot.runtime.attention import AttentionPolicy, HUMAN_INPUTS
from len_bot.runtime.scene_policy import ScenePolicy, conversation_visible
from len_bot.runtime.plugin_interactions import classify_event, validate_plugin_origin
from len_bot.runtime.heartbeat import Heartbeat
from len_bot.runtime.sleep_policy import DeliveryDeferred, should_defer_send, should_ingest_social
from len_bot.scenes.actor import HistoryCommitDeferred, SceneCommitConflict
from len_bot.scenes.manager import SceneManager
from len_bot.scenes.models import OriginalCoverage, SceneSession
from len_bot.scheduler.engine import TaskScheduler
from len_bot.scheduler.models import TaskItem, TaskStatus
from len_bot.state.open_loops import OpenLoopManager

logger = logging.getLogger(__name__)


def _is_shadow_input(event: Event) -> bool:
    source_types = {
        EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED,
        EventType.TASK_DUE, EventType.TASK_REVIEW, EventType.AGENT_JOB_FINISHED,
        EventType.AGENT_JOB_PROGRESS, EventType.REFLECTION_RECORDED,
        EventType.MESSAGE_SEND_FAILED, EventType.FILE_UPLOAD_FAILED, EventType.FILE_UPLOADED, EventType.LIVE_STARTED, EventType.LIVE_ENDED,
        EventType.TOOL_COMPLETED, EventType.USER_JOINED,
    }
    if event.event_type not in source_types:
        return False
    if event.event_type == EventType.REFLECTION_RECORDED and not event.metadata.get("needs_review"):
        return False
    return event.metadata.get("delivery_origin") == "shadow" or event.payload.get("origin_mode") == "shadow"


class AgentRuntime:
    def __init__(
        self,
        config: RuntimeConfig,
        send_adapter: Callable[[ActionItem], Awaitable[DeliveryResult]] | None = None,
        mock_turn_handler: Callable[[SceneSession, list[Event]], Awaitable[EpisodeOutcome]] | None = None,
        clock=time.time,
        attention_random=None,
        *, config_store: ConfigStore,
    ):
        self.config, self.clock = config, clock
        self.config_store = config_store
        self.restart_required = False
        self.mock_turn_handler = mock_turn_handler
        self.evaluation_hook = None
        self.config_update_lock = asyncio.Lock()
        self.control_plane_lock = asyncio.Lock()
        self._reset_lock = asyncio.Lock()
        self._ingest_lock = asyncio.Lock()
        self._ingestion_ready = asyncio.Event()
        self._ingestion_ready.set()
        self.bot_actor_id = f"user:{config.bot_qq}"
        self.event_store = EventStore(config.db_path, clock=clock)
        self.memory_store: MemoryStore | None = None
        self.interest_store: InterestStore | None = None
        self.history_engine: ReflectionEngine | None = None
        self.provider_registry = ProviderRegistry()
        self.retrieval_models: RetrievalModels | None = None
        self.retrieval_profiles = None
        self.memory_index: MemoryIndex | None = None
        self.provider_configuration_error: str | None = None
        self.plugin_host = PluginHost(runtime=self)
        self.event_store.resolve_plugin_work=self.plugin_host.work_spec
        self.scene_policy = ScenePolicy(config_store)
        self.metrics = RuntimeMetrics()
        self.shadow_mode = config_store.current.delivery.shadow
        self.shadow_would_send_log: deque[dict] = deque(maxlen=500)
        self.action_queue = ActionQueue(
            self.event_store, send_adapter=send_adapter, on_action_event=self._on_action_event,
            bot_actor_id=self.bot_actor_id, prepare_action=self.prepare_outbound_action,
            shadow_probe=lambda: self.shadow_mode, shadow_recorder=self._record_shadow_action,
            max_concurrent=config.action_max_concurrent,
        )
        self.action_queue.pacing = config.message_pacing
        self.action_queue.validate_before_send = self.validate_outbound_action
        self.action_queue.defer_action = self._defer_delivery
        self.heartbeat = Heartbeat(self)
        self.scheduler = TaskScheduler(self.event_store, self.receive_event, sweep_interval=config.scheduler_interval_seconds, metrics=self.metrics)
        self.scheduler.jobs_enabled_probe = self._work_enabled
        self.open_loop_manager = OpenLoopManager(self.event_store)
        self.runtime_gate = RuntimeGate(
            self.event_store, self.action_queue, scheduler=self.scheduler, metrics=self.metrics,
            origin_mode_provider=lambda: "shadow" if self.shadow_mode else "live",
            bot_actor_id=self.bot_actor_id,
            open_loop_ttl_seconds=config.open_loop_ttl_seconds,
        )
        self.runtime_gate.jobs_enabled_probe = self._work_enabled
        self.runtime_gate.validate_job_resume = self._validate_job_resume
        self.runtime_gate.scene_policy = self.scene_policy
        self.runtime_gate.time_settings = lambda: self.config_store.current.time
        self.runtime_gate.capability_authority = CapabilityAuthority(config_store, self.scene_policy)
        # The store reserves from the same numbers the runner enforces, so a
        # hold cannot disagree with the limits the work actually runs under.
        self._apply_budget_configuration()
        self.runtime_gate.validate_plugin_origin = lambda mailbox, scene: validate_plugin_origin(self, mailbox, scene)
        self.runtime_gate.deterministic_service = self.plugin_host.deterministic_service
        from len_bot.runtime.rate_limit import MessageRateLimiter
        self.rate_limiter = MessageRateLimiter(self.event_store, lambda: self.config_store.current.runtime, clock)
        self.attention_policy = AttentionPolicy(config, clock)
        if attention_random is not None:
            self.attention_policy.random_source = attention_random
        self.attention_policy.chat_allowed = self.scene_policy.chat_allowed
        self.attention_policy.time_settings = lambda: self.config_store.current.time
        from len_bot.runtime.attention_config import effective_attention
        self.attention_policy.effective_attention = lambda scene_id: effective_attention(self.config_store.current, scene_id)
        self.scene_manager = SceneManager(self.bot_actor_id, self.event_store, self._on_scene_event_committed,
                                          attention_policy=self.attention_policy,
                                          classify_event=lambda event, cutoff: classify_event(self, event, cutoff))
        self.burst_assembler = BurstAssembler(config, self._on_burst, wall_clock=clock)
        self.social_core = SocialCognitionCore(self)
        from len_bot.cognition.action_review import ActionReviewer
        self.action_reviewer = ActionReviewer(self)
        self.job_runner = InformationJobRunner(self)
        self.media_service = MediaService(self)
        from len_bot.media.files import FileAssetService
        self.file_assets = FileAssetService(self)
        self._cognition_semaphore = asyncio.Semaphore(config.conversation_max_concurrent)
        self._last_gate_decision: GateDecision | None = None
        self._started_at = self.clock()
        self._onebot_adapter = None
        self._running = False
        self._maintenance_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self._history_timers: dict[str, asyncio.TimerHandle] = {}
        self._maintaining_history_scenes: set[str] = set()
        self._pending_bursts: dict[str, Stimulus] = {}
        self._conversation_tasks: dict[str, asyncio.Task] = {}
        self._semantic_index_epochs: dict[str, int] = {}

    def _spawn_background_task(self, coroutine: Awaitable[Any]) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _index_memories(self, memories, scene_id: str):
        if not self.memory_index or not self.semantic_retrieval_enabled(scene_id):
            return
        guard = self.semantic_index_guard(scene_id)
        try:
            result = await self.memory_index.index_memories(memories, scene_id=scene_id, request_guard=guard)
        except Exception as error:
            result = {'status':'error','error_type':type(error).__name__,'error':str(error)}
        if result.get('status') in {'error', 'cancelled'}:
            await self.event_store.save_trace(kind='memory_index_error' if result.get('status') == 'error' else 'memory_index_cancelled', scene_id=scene_id,
                ref_id='memory-index:'+uuid.uuid4().hex, payload=result)

    async def _index_summary(self, batch_id: str, summary: str, generation: str | int, scene_id: str):
        if not self.memory_index or not self.semantic_retrieval_enabled(scene_id):
            return
        guard = self.semantic_index_guard(scene_id)
        try:
            result = await self.memory_index.index_summary(batch_id, summary, generation, scene_id=scene_id, request_guard=guard)
        except Exception as error:
            result = {'status':'error','error_type':type(error).__name__,'error':str(error)}
        if result.get('status') in {'error', 'cancelled'}:
            await self.event_store.save_trace(kind='memory_index_error' if result.get('status') == 'error' else 'memory_index_cancelled', scene_id=scene_id,
                ref_id='history-index:'+uuid.uuid4().hex, payload=result)

    def has_model_profile(self, role: str) -> bool:
        snapshot = self.provider_registry.snapshot()
        profile = (snapshot.get("routing") or {}).get(role)
        return bool(profile and any(
            provider["id"] == profile["provider_id"] and provider["enabled"] and provider["api_key_masked"]
            for provider in snapshot["providers"]
        ))

    def semantic_retrieval_enabled(self, scene_id: str) -> bool:
        scene = self.config_store.current.scenes.get(scene_id)
        return bool(scene and scene.semantic_retrieval)

    def semantic_index_guard(self, scene_id: str):
        epoch = self._semantic_index_epochs.get(scene_id, 0)
        return lambda: self.semantic_retrieval_enabled(scene_id) and self._semantic_index_epochs.get(scene_id, 0) == epoch

    def _work_enabled(self) -> bool:
        return self.config.jobs_enabled and self.has_model_profile("work")

    async def _validate_job_resume(self, job_id: str, scene_id: str):
        job = await self.event_store.get_job(job_id, scene_id)
        issue=self.job_resume_issue(job)
        if issue:raise ValueError(issue)

    def job_resume_issue(self, job):
        """One current model/budget decision for controls and their read views."""
        if not job or not job['can_resume']:
            return 'This work has no resumable interrupted execution or settled partial result with unfinished scope'
        if not self.config.jobs_enabled:
            return 'Information work is currently disabled'
        issue=self.plugin_host.work_issue(job)
        if issue:return issue
        work=self.plugin_host.work_spec(job['plugin_origin'],job['work_operation'])
        needs_model=not work or work.needs_model is None or work.needs_model(
            work.parameters_model.model_validate(job['work_parameters']),
            work.progress_model.model_validate(job['work_progress']),job['goal'],tuple(job['constraints']))
        from len_bot.cognition.budget import WorkBudgetSnapshot
        stored = job.get('budget')
        limits = (self.config.model_copy(update=WorkBudgetSnapshot.model_validate(stored).runtime_values())
                  if stored else self.config)
        deadline = (stored or {}).get('deadline_at')
        if deadline is not None and self.clock() >= deadline:
            return 'This work has passed its original deadline; resume cannot extend it'
        # A dimension the operator left unlimited cannot be the reason a work
        # may not resume; only a limit that actually carries a number can.
        if (needs_model and limits.job_max_steps is not None
                and job['model_steps']>=limits.job_max_steps):
            return 'This work has no remaining model steps; its spent budget is not reset by resume'
        if limits.job_max_seconds is not None and job['elapsed_seconds']>=limits.job_max_seconds:
            return 'This work has no remaining execution time; its spent budget is not reset by resume'
        if (job['execution_status']=='partial' and limits.job_max_tool_calls is not None
                and job['tool_calls']>=limits.job_max_tool_calls and (not work or work.execute is None)):
            return 'This partial work has no remaining read-tool budget; continuing does not reset its counters'
        if not needs_model:return None
        try:
            if job['model_binding']:
                self.provider_registry.resolve_profile(ModelProfile.model_validate(job['model_binding']),role='work')
            elif job['model_steps']:
                return 'This pre-upgrade work has no recorded model binding; cannot guess a provider for resume'
            else:
                self.provider_registry.resolve('work')
        except (ValueError,LookupError) as error:
            return str(error)
        return None

    def update_bot_identity(self, bot_qq: int) -> None:
        self.bot_actor_id = f"user:{bot_qq}"
        self.action_queue.bot_actor_id = self.bot_actor_id
        self.runtime_gate.bot_actor_id = self.bot_actor_id
        self.scene_manager.bot_actor_id = self.bot_actor_id
        for actor in self.scene_manager._actors.values():
            actor.bot_actor_id = self.bot_actor_id

    async def start(self) -> None:
        if self._running:
            return
        await self.event_store.initialize()
        await self.event_store.mark_suspended_conversations_for_review()
        self.memory_store = MemoryStore(self.event_store._db, self.event_store._write_lock, clock=self.clock)
        await self.memory_store.initialize()
        self.interest_store = InterestStore(self.event_store)
        await self._load_configuration()
        self.memory_index = MemoryIndex(self.event_store._db, self.event_store._write_lock,
                                        self.retrieval_models, self.retrieval_profiles.embedding, self.clock)
        await self.memory_index.initialize()
        self.history_engine = ReflectionEngine(
            self.memory_store,
            llm_reflector=LLMReflector(
                lambda: self.provider_registry.resolve("maintenance"), memory_store=self.memory_store,
                call_store=self.event_store, context_tokens=self.config.maintenance_context_tokens,
                output_tokens=self.config.maintenance_output_tokens,
                max_steps=self.config.maintenance_max_steps, max_tool_calls=self.config.maintenance_max_tool_calls,
                memory_limit=self.config.retrieval_default_limit,
            ),
        )
        await self._start_workers(recover=True)

    async def _load_configuration(self) -> None:
        models = self.config_store.current.models
        await self.provider_registry.apply_update(models.providers, models.routing)
        self.retrieval_profiles = models.retrieval
        self.retrieval_models = RetrievalModels(self.provider_registry, self.event_store,
                                                timeout=self.config.media_request_timeout_seconds)
        self.memory_index = MemoryIndex(self.event_store._db, self.event_store._write_lock,
                                        self.retrieval_models, self.retrieval_profiles.embedding, self.clock)

    async def update_runtime_settings(self, values: dict, *, live: bool, baseline: dict,
                                      credential_change=None) -> None:
        from len_bot.config import EXECUTION_BUDGET_FIELDS
        from len_bot.config_edit import merge_fields
        async with self.config_update_lock:
            data = self.config_store.current.model_dump()
            before = data['runtime']
            data["runtime"] = merge_fields(before, baseline, values, ('runtime',))
            changed_keys = {key for key in values if before[key] != data['runtime'][key]}
            if credential_change is not None:
                from len_bot.config_edit import ConfigEditConflict
                expected_revision, replacement = credential_change
                current_revision = int(data['runtime'].get('onebot_credential_revision') or 1)
                if current_revision != int(expected_revision or 1):
                    raise ConfigEditConflict(('runtime', 'onebot_access_token'))
                if data['runtime']['onebot_access_token'] != replacement:
                    data['runtime']['onebot_access_token'] = replacement
                    data['runtime']['onebot_credential_revision'] = current_revision + 1
            candidate = self.config_store.parse(data)
            if 'character_reference_assets' in changed_keys:
                await self.media_service.validate_character_references(candidate.runtime.character_reference_assets)
            self.config_store.save(candidate)
            live_keys = changed_keys if live else changed_keys & EXECUTION_BUDGET_FIELDS
            if live_keys:
                self.config = self.config.model_copy(update={key: getattr(candidate.runtime, key) for key in live_keys})
                self.event_store.budget_config = self.config
                self.attention_policy.config = self.config
                self.burst_assembler.config = self.config
            if not live and any(getattr(candidate.runtime, key) != getattr(self.config, key)
                                for key in changed_keys if key not in live_keys):
                self.restart_required = True
            if credential_change is not None:
                self.restart_required = True

    async def _start_workers(self, *, recover: bool) -> None:
        self.job_runner = InformationJobRunner(self)
        self._running = True
        restored = await self.event_store.recover_social_work() if recover else []
        await self.action_queue.start()
        await self._load_plugins()
        await self.job_runner.reconcile_plugins()
        self._ingestion_ready.set()
        self._delivery_run_id = uuid.uuid4().hex
        await self._recover_deferred_deliveries()
        await self.scheduler.start()
        await self._reevaluate_awake_deliveries()
        await self.heartbeat.ensure_next()
        await self.job_runner.resume_skill_candidates()
        for scene_id in restored:
            actor = await self.scene_manager.get_or_create_actor(scene_id)
            await self.record_operator_event(scene_id, 'apply_scene_settings', 'runtime', {'policy_changed': True})
            self._schedule_history_maintenance(actor.session)
            pending = await self.event_store.events_by_ids(scene_id,
                [wake.event_id for wake in actor.session.pending_wakes], actor.session.last_observed_event_rowid)
            if pending:
                await self._on_burst(BurstAssembler._create_burst(pending))
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def _load_plugins(self) -> None:
        for plugin_id, state in self.config_store.current.plugins.items():
            if state.enabled:
                try:
                    await self.plugin_host.enable_plugin(plugin_id)
                except Exception as error:
                    logger.error('Plugin %s could not be enabled: %s', plugin_id, _error_text(error))

    async def update_plugin_settings(self, plugin_id, *, baseline, enabled=None, values=None):
        async with self.config_update_lock:
            data = self.config_store.current.model_dump()
            if plugin_id not in self.config_store.catalog.entries:
                raise KeyError(plugin_id)
            state = data["plugins"].setdefault(plugin_id, {'enabled': False, 'config': None})
            if enabled is not None:
                from len_bot.config_edit import merge_edit
                state["enabled"] = merge_edit(state["enabled"], baseline, enabled, ('plugins', plugin_id, 'enabled'))
            if values is not None:
                # Credentials have no readable form, so they cannot round trip
                # through a form: a request that omits one, or sends it back
                # empty, keeps the stored value instead of erasing it.  Only a
                # non-empty value replaces it, and an explicit null clears it.
                from len_bot.plugins.credentials import credentials_changed, merge_config_edit, schema_of
                schema = schema_of(self.config_store.catalog.entries[plugin_id].spec)
                previous = state.get("config")
                current_revision = int(state.get("credential_revision") or 1)
                state["config"] = merge_config_edit(
                    previous, values, schema, baseline["config"], baseline["config_set"],
                    current_revision=current_revision,
                    baseline_revision=baseline.get("credential_revision", 1))
                if credentials_changed(previous, state["config"], schema):
                    state["credential_revision"] = current_revision + 1
            candidate = self.config_store.parse(data)
            self.config_store.save(candidate)
            try:
                if values is not None:
                    await self.plugin_host.apply_plugin_config(plugin_id)
                elif enabled is True:
                    await self.plugin_host.enable_plugin(plugin_id)
                elif enabled is False:
                    await self.plugin_host.disable_plugin(plugin_id)
            except Exception as error:
                raise PluginConfigurationApplyError('根配置已保存，但插件运行更新失败：'+_error_text(error)) from error

    async def set_shadow_mode(self, enabled: bool) -> None:
        async with self.config_update_lock:
            data = self.config_store.current.model_dump()
            data["delivery"]["shadow"] = enabled
            candidate = self.config_store.parse(data)
            self.config_store.save(candidate)
            self.shadow_mode = enabled

    def _apply_budget_configuration(self) -> None:
        """Point the store at the live limits a reservation is computed from."""
        from len_bot.cognition.budget import ReservationPolicy
        self.event_store.budget_config = self.config
        self.event_store.capability_authority = self.runtime_gate.capability_authority
        settings = self.config_store.current.time
        self.event_store.billing_timezone = settings.timezone if settings else None
        self.event_store.reservation_policy = (
            self.config_store.current.resources.policies.get('default') or ReservationPolicy())

    async def update_root_settings(self, section: str, values, *, baseline) -> None:
        from len_bot.config_edit import merge_edit
        if section not in {'access', 'time', 'members', 'resources'}:
            raise ValueError('Unknown settings section')
        async with self.config_update_lock:
            data = self.config_store.current.model_dump()
            data[section] = (values(data[section], baseline) if callable(values)
                             else merge_edit(data[section], baseline, values, (section,)))
            self.config_store.save(self.config_store.parse(data))
            if section in {'time', 'members'}:
                self.restart_required = True
        if section == 'resources':
            # Quota numbers take effect for the next work created; already
            # reserved work keeps the policy named on its own reservation.
            self._apply_budget_configuration()

    async def update_scene_settings(self, scene_id: str, values: dict, *, baseline) -> None:
        from len_bot.config_edit import merge_edit
        was_enabled = self.semantic_retrieval_enabled(scene_id)
        async with self.config_update_lock:
            data = self.config_store.current.model_dump()
            data['scenes'][scene_id] = merge_edit(data['scenes'].get(scene_id), baseline, values, ('scenes', scene_id))
            self.config_store.save(self.config_store.parse(data))
        await self._apply_scene_settings(scene_id, was_enabled)

    async def apply_group_quick(self, scene_id: str, baseline: dict, values: dict, *, operator_id: str) -> None:
        from len_bot.config_edit import merge_edit
        from len_bot.config_store import SceneSettings
        from len_bot.runtime.capabilities import CapabilityGrant
        from len_bot.web.group_quick import apply_send_file_grants
        settings = SceneSettings.model_validate(values['settings']).model_dump()
        was_enabled = self.semantic_retrieval_enabled(scene_id)
        async with self.config_update_lock:
            data = self.config_store.current.model_dump()
            data['scenes'][scene_id] = merge_edit(data['scenes'].get(scene_id), baseline.get('settings'),
                                                  settings, ('scenes', scene_id))
            if values.get('send_file_principals') is not None:
                grants = [CapabilityGrant.model_validate(item) for item in data['access']['capability_grants']]
                grants = apply_send_file_grants(grants, scene_id, values['send_file_principals'],
                                                baseline.get('send_file_grants'), operator_id)
                data['access']['capability_grants'] = [grant.model_dump() for grant in grants]
            self.config_store.save(self.config_store.parse(data))
        await self._apply_scene_settings(scene_id, was_enabled)

    async def _apply_scene_settings(self, scene_id: str, was_enabled: bool) -> None:
        if was_enabled != self.semantic_retrieval_enabled(scene_id):
            self._semantic_index_epochs[scene_id] = self._semantic_index_epochs.get(scene_id, 0) + 1
        actor = self.scene_manager._actors.get(scene_id)
        for plugin_id in self.config_store.catalog.entries:
            if not self.scene_policy.plugin_allowed(scene_id,plugin_id,'handler'):
                await self.plugin_host.stop_scene_work(plugin_id,scene_id)
        if actor:
            if not self.scene_policy.enabled(scene_id):
                if actor._active_mailbox:
                    actor._active_mailbox.cancel('Group disabled by operator')
                self._pending_bursts.pop(scene_id, None)
            if not self.scene_policy.maintenance_allowed(scene_id):
                timer = self._history_timers.pop(scene_id, None)
                if timer:
                    timer.cancel()
            await self.record_operator_event(scene_id, 'scene_settings', 'panel', {'policy_changed': True})
        interest_share = self.plugin_host.get_plugin('interest_share')
        if interest_share and interest_share.manifest.enabled:
            await interest_share.ensure_next()

    async def _record_shadow_action(self, action: ActionItem) -> None:
        self.metrics.inc_social("would_send")
        self.shadow_would_send_log.append({
            "action_id": action.id, "scene_id": action.scene_id, "action_type": action.action_type.value,
            "content": action.content, "reply_to": action.reply_to, "recorded_at": self.clock(),
        })

    async def stop(self) -> None:
        async with self._reset_lock:
            await self._stop_conversation_workers()
            self._ingestion_ready.set()
            if self.retrieval_models:
                await self.retrieval_models.close()
            await self.media_service.close()
            await self.event_store.close()

    async def _stop_conversation_workers(self) -> None:
        self._running = False
        await self.burst_assembler.close()
        self._pending_bursts.clear()
        for timer in self._history_timers.values():
            timer.cancel()
        self._history_timers.clear()
        for actor in self.scene_manager._actors.values():
            if actor._active_mailbox:
                actor._active_mailbox.cancel("Runtime stopping")
        tasks = set(self._background_tasks)
        if self._maintenance_task:
            tasks.add(self._maintenance_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._conversation_tasks.clear()
        self._maintenance_task = None
        self._maintaining_history_scenes.clear()
        await self.scheduler.stop()
        await self.job_runner.stop()
        await self.plugin_host.unload_all()
        # Cancellation/unknown receipts are still written into the old scene.
        await self.action_queue.stop()
        async with self._ingest_lock:
            for actor in list(self.scene_manager._actors.values()):
                await actor._queue.join()
            await self.scene_manager.stop()

    async def reset_conversation_data(self, operator: str) -> dict:
        async with self._reset_lock:
            self._ingestion_ready.clear()
            try:
                await self._stop_conversation_workers()
                async with self._ingest_lock:
                    event = Event(
                        event_type=EventType.OPERATOR_ACTION, scene_id="system:settings",
                        actor_id=f"operator:{operator}", timestamp=self.clock(),
                        payload={"operation": "reset_conversation_data", "operator": operator},
                    )
                    counts = await self.event_store.reset_conversation_data(event)
                    await self.media_service.reset_cache()
                    self.shadow_would_send_log.clear()
                    self._last_gate_decision = None
                    self.metrics = RuntimeMetrics()
                    self.runtime_gate.metrics = self.metrics
                    self.scheduler.metrics = self.metrics
                    if self._onebot_adapter:
                        self._onebot_adapter.restore_own_message_ids([])
                await self._start_workers(recover=False)
            finally:
                self._ingestion_ready.set()
            return {"success": True, "cleared": counts}

    async def _maintenance_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(max(0.05, self.config.maintenance_interval_seconds))
                if self._running:
                    await self.open_loop_manager.sweep_ttl_expiration()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("OpenLoop expiration failed")

    async def receive_event(self, event: Event, *, _internal: bool = False) -> None:
        if not _internal:
            await self._ingestion_ready.wait()
        async with self._ingest_lock:
            if not self._running and not _internal:
                raise RuntimeError("Runtime is not accepting events")
            if self.event_store._db is None:
                raise RuntimeError("Runtime event store is closed")
            if self.shadow_mode or event.scene_id.startswith('private:'):
                event.metadata["delivery_origin"] = "shadow"
            await self.scene_manager.dispatch_event(event)

    async def commit_tool_observation(self, event: Event) -> None:
        """Record an already durable tool envelope without creating a new chat trigger."""
        await self.receive_event(event, _internal=True)
        actor = await self.scene_manager.get_or_create_actor(event.scene_id)
        await actor._queue.join()
        if not await self.event_store.event_exists(event.id, event.scene_id):
            raise RuntimeError("Tool observation could not be committed")

    async def record_operator_event(self, scene_id: str, operation: str, operator: str, details: dict | None = None) -> Event:
        event = Event(
            event_type=EventType.OPERATOR_ACTION, scene_id=scene_id, actor_id=f"operator:{operator}",
            timestamp=self.clock(), payload={**(details or {}), "operation": operation, "operator": operator},
        )
        await self.receive_event(event)
        actor = await self.scene_manager.get_or_create_actor(scene_id)
        await actor._queue.join()
        if not await self.event_store.event_exists(event.id, scene_id):
            raise RuntimeError("Operator request could not be recorded")
        return event

    async def operator_outcome(
        self, scene_id: str, outcome: EpisodeOutcome, *, source_event_ids: list[str] | None = None,
    ) -> GateDecision:
        """Commit panel controls; the Actor/Gate receives no fabricated QQ identity."""
        await self._ingestion_ready.wait()
        if not self._running:
            raise RuntimeError("Runtime is not running")
        actor = await self.scene_manager.get_or_create_actor(scene_id)
        session = actor.session.model_copy(deep=True)
        episode_id = f"operator:{uuid.uuid4().hex}"
        mailbox = EpisodeMailbox(episode_id, scene_id, session.version)
        mailbox.origin_mode = "shadow" if self.shadow_mode or scene_id.startswith('private:') else "live"
        evidence = list(dict.fromkeys(
            [*(source_event_ids or [])]
            + [event_id for proposal in [*outcome.task_proposals, *outcome.job_proposals] for event_id in proposal.source_event_ids]
            + [event_id for proposal in outcome.memory_proposals for event_id in proposal.evidence]
        ))
        decision = await actor.commit_turn(
            outcome, session.last_observed_event_rowid, evidence, session.knowledge_revision,
            mailbox, self.runtime_gate, operator=True,
        )
        self._last_gate_decision = decision
        if decision.accepted:
            if self.memory_index and decision.committed_proposal.committed_memories:
                self._spawn_background_task(self._index_memories(
                    list(decision.committed_proposal.committed_memories), scene_id=scene_id))
            try:
                await self.runtime_gate.publish_committed(decision, mailbox)
            finally:
                await self.event_store.save_trace(kind='conversation', scene_id=scene_id, ref_id=episode_id,
                    payload={'operator_control': True, 'source_event_ids': evidence,
                             'result': decision.committed_proposal.outcome.model_dump(mode='json'),
                             'gate': self._gate_record(decision)})
        return decision

    async def _defer_delivery(self, action: ActionItem, deferred: DeliveryDeferred) -> None:
        task = await self.event_store.defer_delivery(action, deferred.due_at, deferred.detail)
        if task is None:
            return
        self.scheduler.reschedule_task(task)
        await self.receive_event(Event(
            event_type=EventType.OPERATOR_ACTION, scene_id=action.scene_id,
            actor_id='system:sleep', timestamp=self.event_store.clock(),
            payload={'kind': 'deferred_delivery', 'action_id': action.id,
                     'due_at': deferred.due_at, 'detail': deferred.detail, 'task_id': task.id},
            metadata={'conversation_excluded': True}), _internal=True)

    async def _delivery_terminal(self, action_id: str, scene_id: str) -> str | None:
        fact = await self.event_store.delivery_fact(action_id, scene_id)
        return fact[0] if fact else None

    async def _recover_deferred_deliveries(self) -> None:
        await self.event_store.recover_deferred_delivery_tasks()

    async def _reevaluate_awake_deliveries(self) -> None:
        from len_bot.runtime.sleep_policy import is_asleep
        for task in await self.event_store.get_pending_tasks():
            if task.payload.get('kind') != 'deferred_delivery':
                continue
            actor = await self.scene_manager.get_or_create_actor(task.scene_id)
            if not is_asleep(actor.session, self.config_store.current.time, self.clock()):
                await self.scheduler.trigger_task_now(task.id)

    async def _release_deferred(self, event: Event) -> None:
        if event.metadata.get('obsolete_task_wake'):
            return
        action = await self.event_store.claim_deferred_delivery(
            event.payload['task_id'], event.scene_id, self._delivery_run_id)
        if action is None:
            return
        actor = await self.scene_manager.get_or_create_actor(action.scene_id)
        deferred = should_defer_send(action, actor.session, self.config_store.current.time, self.clock(),
            deterministic_service=self.plugin_host.deterministic_service(action.plugin_origin))
        if deferred:
            await self._defer_delivery(action, deferred)
            return
        if action.output_kind == 'chat' and not (action.job_id or action.fulfils_task_id):
            self._spawn_background_task(self.action_queue._reject(action,
                '睡眠期间的旧闲聊已过期，等待新的真实人类上下文', status='rejected', cancelled=True))
            return
        if self.plugin_host.has_deferred_refresh(action.plugin_origin):
            self._spawn_background_task(self._refresh_deferred_plugin(action))
            return
        self.action_queue.enqueue(action)

    async def _refresh_deferred_plugin(self, action):
        try:
            replacement = await self.plugin_host.refresh_deferred(action)
        except asyncio.CancelledError:
            await self.action_queue._reject(action, '延期来源重核已取消，停止原延期行动',
                status='rejected', cancelled=True)
            raise
        except Exception as error:
            await self.action_queue._reject(action, f'延期来源重核失败：{type(error).__name__}: {error}',
                status='rejected', cancelled=True)
        else:
            await self.action_queue._reject(action,
                '替代业务来源已保存：' + replacement if replacement else '原业务来源已失效，延期行动过期',
                status='rejected', cancelled=True)

    async def prepare_outbound_action(self, action: ActionItem) -> ActionItem:
        if action.file_asset_id:
            return await self.file_assets.prepare_action(action)
        if action.plugin_origin and action.plugin_origin.plugin_id == 'interest_share':
            from len_bot.runtime.interest_publication import publication_for
            from len_bot.plugins.builtin.interest_share.config import Candidate
            rows = await self.event_store.events_by_ids(action.scene_id, [action.plugin_origin.source_event_id], 2**63-1)
            if len(rows) != 1:
                raise ValueError('兴趣分享来源不存在')
            candidate = Candidate.model_validate(rows[0].payload['data'])
            _, publication = await publication_for(self.event_store, candidate.interest_id, candidate.revision)
            action = action.model_copy(update={'interest_publication': publication})
        return await self.media_service.prepare_action(action)

    async def validate_outbound_action(self, action: ActionItem) -> None:
        if action.covered_source_event_ids:
            originals=await self.event_store.events_by_ids(action.scene_id,action.covered_source_event_ids,2**63-1)
            if len(originals)!=len(action.covered_source_event_ids):
                raise ValueError('合并回复的原始来源不再完整可用')
            for original in originals:
                if (original.event_type not in {EventType.GROUP_MESSAGE_RECEIVED,EventType.PRIVATE_MESSAGE_RECEIVED}
                        or not original.actor_id.startswith('user:') or original.actor_id==self.bot_actor_id
                        or not self.scene_policy.chat_allowed(action.scene_id,original.actor_id.removeprefix('user:'))):
                    raise ValueError('合并回复的来源不再具备当前群的回应资格')
        if action.file_asset_id:
            from len_bot.media.files import validate_file_action
            await validate_file_action(self.event_store, action)
        if action.job_id and not action.operation_ref:
            job=await self.event_store.get_job(action.job_id,action.scene_id)
            if job:
                issue=self.plugin_host.work_issue(job)
                if issue:raise ValueError(issue)
                handler_owned=job['plugin_origin'] and job['plugin_origin']['scene_entry']=='handler'
                if not handler_owned and not self.scene_policy.chat_allowed(action.scene_id,job['requester_qq_uid']):
                    raise ValueError('The original work requester no longer has chat eligibility')
        if action.plugin_origin is not None or action.output_kind != 'chat':
            await validate_plugin_origin(self, action, action.scene_id)
        else:
            if not self.scene_policy.chat_allowed(action.scene_id, action.requester_qq_uid):
                raise ValueError('本群已停用或请求者没有普通对话资格')
        if any(segment.type == 'at_all' for segment in action.segments) and action.plugin_origin is None:
            raise ValueError('普通对话未开放全体提及')
        for segment in action.segments:
            if segment.type in {"image", "video", "audio"} and (
                not self.config.media_enabled
                or await self.event_store.get_media(segment.asset_id, [action.scene_id, "global-safe"]) is None
            ):
                raise ValueError("媒体已停用或不在本场景中")
        actor = await self.scene_manager.get_or_create_actor(action.scene_id)
        from len_bot.runtime.gate import MAX_CONSECUTIVE_BOT_MESSAGES
        if action.interest_publication and actor.session.consecutive_bot_messages >= MAX_CONSECUTIVE_BOT_MESSAGES:
            raise ValueError('本群连续 Bot 消息已达上限，停止主动分享')
        deferred = should_defer_send(action, actor.session, self.config_store.current.time,
            self.event_store.clock(), deterministic_service=self.plugin_host.deterministic_service(action.plugin_origin))
        if deferred is not None:
            raise deferred

    async def _on_action_event(self, event: Event) -> None:
        if event.event_type == EventType.MESSAGE_SENT:
            self.metrics.inc_social("simulated_messages" if event.metadata.get("simulated") else "visible_messages")
            self.rate_limiter.note_send(event.scene_id)
        await self.receive_event(event, _internal=True)
        actor = await self.scene_manager.get_or_create_actor(event.scene_id)
        await actor._queue.join()
        if not await self.event_store.event_exists(event.id, event.scene_id):
            raise RuntimeError('发送回执尚未提交；保留发送尝试为未知，不重发')

    async def _on_scene_event_committed(self, session: SceneSession, event: Event) -> None:
        if not self._running:
            return
        human = event.event_type in {EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED} and event.actor_id != self.bot_actor_id
        if human:
            self.metrics.inc_social("human_messages")
        if event.metadata.get('conversation_resume_error'):
            await self.event_store.save_trace(kind='observation', scene_id=event.scene_id, ref_id=event.id,
                payload={'source_event_ids': [event.id], 'status': 'not_started', 'at': self.clock(),
                    'error': event.metadata['conversation_resume_error'],
                    'rejections': [{'event_id': event.id, 'reason': 'wait_claim_rejected'}]})
            return
        self.plugin_host.dispatch_event(event, session.last_observed_event_rowid)
        self.plugin_host.notify_delivery(event, session.last_observed_event_rowid)
        kind = (event.payload.get("payload") or {}).get("kind")
        if event.event_type == EventType.TASK_DUE and kind == 'interest_share':
            plugin = self.plugin_host.get_plugin('interest_share')
            if plugin and plugin.manifest.enabled:
                await plugin.run_slot(event)
            else:
                from len_bot.scheduler.models import TaskStatus
                await self.event_store.mark_task_status(event.payload['task_id'], TaskStatus.CANCELLED)
            return
        if event.event_type == EventType.TASK_DUE and kind == 'heartbeat':
            await self.heartbeat.run_slot(event)
            return
        if event.scene_id == 'system:heartbeat':
            job_id = event.payload.get('job_id') or event.payload.get('task_id')
            job = await self.event_store.get_job(job_id, event.scene_id) if job_id else None
            from len_bot.runtime.public_research import verify_public_job
            if job and await verify_public_job(self.event_store, job):
                await self.job_runner.on_event(event)
                if event.event_type == EventType.AGENT_JOB_FINISHED:
                    await self.heartbeat.occupied()
            return
        if event.event_type == EventType.TASK_DUE and kind == 'deferred_delivery':
            await self._release_deferred(event)
            return
        if not self.scene_policy.enabled(event.scene_id):
            return
        await self.job_runner.on_event(event)
        job_due = event.event_type == EventType.TASK_DUE and kind == "agent_job"
        heartbeat_due = kind in {"heartbeat", "deferred_delivery"}
        if (not event.metadata.get('plugin_consumed') and not job_due and not heartbeat_due
                and event.metadata.get("attention_reasons")
                and should_ingest_social(session, self.config_store.current.time,
                                         self.event_store.clock(),
                                         event.metadata.get("attention_reasons") or [])
                and await self._chat_within_allowance(event, session)):
            await self.burst_assembler.ingest(event)
        await self.scheduler.on_event(event)
        if (human or event.event_type == EventType.MESSAGE_SENT) and conversation_visible(event):
            self._schedule_history_maintenance(session)
            if self._can_maintain_history():
                self._spawn_background_task(self._maintain_history(session.scene_id, quiet=False))

    def _chat_ceiling_applies(self, event: Event) -> bool:
        """Whether the hourly chat allowance governs this input at all.

        Both gates ask this, because they used to disagree: a job checkpoint,
        a due task and a finished job all travel with interaction='chat', so
        the eligibility pass was cancelling exactly the obligations the entry
        gate exempts by name.
        """
        return (event.event_type == EventType.GROUP_MESSAGE_RECEIVED
                and event.actor_id != self.bot_actor_id)

    async def _chat_within_allowance(self, event: Event, session) -> bool:
        """Whether ordinary chat may still start a turn in this scene.

        The ceiling stops the turn, not the send.  A turn's input tokens are
        spent the moment it starts, so refusing at the send would buy silence
        without buying anything else.  Only human group messages are weighed:
        work results, live events and plugin pushes carry an obligation the
        ceiling was never meant to cancel.
        """
        if not self._chat_ceiling_applies(event):
            return True
        requester = event.actor_id.removeprefix('user:') if event.actor_id.startswith('user:') else None
        state = await self.rate_limiter.status(event.scene_id, requester)
        if not (state['scene_exhausted'] or state['user_exhausted']):
            return True
        # A reaction containing @ is still allowed to end without a message.
        # The panel exposes this allowance; do not buy a model call or send a
        # fixed chat notification just to explain an exhausted allowance.
        await self.event_store.save_trace(kind='observation', scene_id=event.scene_id, ref_id=event.id,
            payload={'source_event_ids': [event.id], 'status': 'not_started', 'reason': 'hourly_limit',
                     'scene_exhausted': state['scene_exhausted'], 'user_exhausted': state['user_exhausted']})
        return False

    def _can_maintain_history(self) -> bool:
        return bool(self.history_engine and self.mock_turn_handler is None and self.has_model_profile("maintenance"))

    def _schedule_history_maintenance(self, session: SceneSession) -> None:
        if not self._running or not self._can_maintain_history() or not self.scene_policy.maintenance_allowed(session.scene_id):
            return
        previous = self._history_timers.pop(session.scene_id, None)
        if previous:
            previous.cancel()
        scene_id = session.scene_id

        def fire():
            self._history_timers.pop(scene_id, None)
            if self._running:
                self._spawn_background_task(self._maintain_history(scene_id))

        self._history_timers[scene_id] = asyncio.get_running_loop().call_later(
            max(0.05, self.config.history_quiet_window_seconds), fire,
        )

    async def retry_history(self, batch_id: str) -> None:
        """An explicit operator retry; new chat never retries a failed range."""
        if not self._running:
            raise ValueError('runtime is not running')
        if not self._can_maintain_history():
            raise ValueError('maintenance profile is not configured')
        try:
            batch = await self.event_store.load_history_batch(batch_id)
        except LookupError as error:
            raise ValueError(str(error)) from error
        if not self.scene_policy.maintenance_allowed(batch.scene_id):
            raise ValueError('该群未开放历史维护')
        if batch.scene_id in self._maintaining_history_scenes:
            raise ValueError('该群的历史维护正在进行，等这一轮跑完再重试')
        self._maintaining_history_scenes.add(batch.scene_id)
        try:
            # A turn in flight no longer refuses the retry: the background run
            # waits for it, so the operator's one chance to unblock this scene
            # does not depend on clicking between two conversations.
            maintenance_context = {'bot_qq':self.config.bot_qq, 'bot_actor_id':self.bot_actor_id, 'now':self.clock()}
            reflector = self.history_engine.llm_reflector
            estimate = reflector.input_tokens(batch, maintenance_context)
            budget = self.config.maintenance_context_tokens - self.config.maintenance_output_tokens
            if estimate > budget:
                raise ValueError(f'历史维护请求需要 {estimate} token，可用输入容量为 {budget}；请先调整维护上下文配置')
            batch = await self.event_store.retry_history_batch(batch_id)
            self._spawn_background_task(self._maintain_history(batch.scene_id, retry_batch=batch, claimed=True))
        except Exception:
            self._maintaining_history_scenes.discard(batch.scene_id)
            raise

    async def _maintain_history(self, scene_id: str, *, quiet=True, retry_batch=None, claimed=False) -> None:
        if scene_id in self._maintaining_history_scenes and not claimed:
            return
        if not self._can_maintain_history() or not self.scene_policy.maintenance_allowed(scene_id):
            if claimed:
                self._maintaining_history_scenes.discard(scene_id)
            return
        self._maintaining_history_scenes.add(scene_id)
        def require_current_maintenance():
            if not self._running:
                raise asyncio.CancelledError()
            if not self._can_maintain_history() or not self.scene_policy.maintenance_allowed(scene_id):
                raise PermissionError('当前群或维护配置不允许采用历史维护结果')

        batch = retry_batch
        stage = 'candidate'
        revision = None
        try:
            while self._running and self._can_maintain_history() and self.scene_policy.maintenance_allowed(scene_id):
                actor = await self.scene_manager.get_or_create_actor(scene_id)
                if actor.has_active_episode():
                    # A scheduled run can simply come back after the next quiet
                    # window: it owns nothing, and the timer will offer the same
                    # tail again.  An operator retry is holding the one batch
                    # that unblocks this scene, and the scheduled path refuses
                    # to touch a range it did not create, so handing the turn
                    # back to the timer drops the retry and the scene stays
                    # blocked until someone happens to click between two turns —
                    # which in a busy group may never come.  It waits out the
                    # turn instead, keeping its claim so the panel can say so.
                    if batch is None:
                        self._schedule_history_maintenance(actor.session)
                        return
                    await actor.wait_episode_idle()
                    continue
                if batch is None:
                    maintenance_context = {'bot_qq':self.config.bot_qq, 'bot_actor_id':self.bot_actor_id, 'now':self.clock()}
                    reflector = self.history_engine.llm_reflector
                    batch = await self.event_store.begin_history_batch(scene_id,
                        target_tokens=self.config.history_target_tokens, min_tokens=self.config.history_min_tokens,
                        quiet=quiet,
                        input_budget_tokens=self.config.maintenance_context_tokens - self.config.maintenance_output_tokens,
                        estimate_input=lambda candidate: reflector.input_tokens(candidate, maintenance_context))
                if batch is None:
                    return
                revision = actor.session.knowledge_revision
                memory_versions = await self.event_store.history_memory_versions(scene_id)
                stage = 'candidate'
                context = {'bot_qq':self.config.bot_qq, 'bot_actor_id':self.bot_actor_id, 'now':self.clock()}
                result = await self.history_engine.maintain_batch(scene_id, batch, context)
                review_event = Event(
                    event_type=EventType.REFLECTION_RECORDED, scene_id=scene_id, actor_id='system:maintenance',
                    timestamp=self.clock(), metadata={'needs_review':bool(result.review_items)},
                    payload={'batch_id':batch.id,
                        'review_items':[item.model_dump() for item in result.review_items],
                        'raw_text':'历史核对：'+'；'.join(item.summary for item in result.review_items) if result.review_items else '',
                        'origin_mode':'shadow' if self.shadow_mode else 'live'})
                stage = 'commit'
                while True:
                    try:
                        require_current_maintenance()
                        committed_memories = await actor.commit_history(
                            batch_id=batch.id, proposals=result.memory_proposals,
                            summary=result.summary, key_event_ids=result.key_event_ids,
                            review_event=review_event, expected_revision=revision,
                            expected_memories=memory_versions, validate_access=require_current_maintenance)
                        break
                    except HistoryCommitDeferred as deferred:
                        await self.event_store.save_trace(kind='history_maintenance_deferred',
                            scene_id=scene_id, ref_id=batch.id,
                            payload={'episode_id': str(deferred), 'knowledge_revision': revision})
                        await actor.wait_episode_idle()
                        if not self._running:
                            return
                stage = 'post_commit'
                if self.memory_index and committed_memories:
                    self._spawn_background_task(self._index_memories(
                        committed_memories, scene_id=scene_id))
                if self.memory_index:
                    self._spawn_background_task(self._index_summary(
                        batch.id, result.summary, batch.generation_version, scene_id))
                await self.event_store.save_trace(kind='history_maintenance', scene_id=scene_id, ref_id=batch.id,
                    payload={'cognition':result.trace, 'source_event_ids':batch.source_event_ids,
                             'result':result.model_dump(mode='json'), 'committed': True,
                             'knowledge_revision_before': revision,
                             'knowledge_revision_after': actor.session.knowledge_revision})
                batch = None
                # A caught-up small tail only runs after the actual quiet window.
                quiet = False
        except asyncio.CancelledError:
            # A pending batch remains unconfirmed across interruption.
            raise
        except Exception as error:
            if batch is not None and stage != 'post_commit':
                await self.event_store.fail_history_batch(batch.id, type(error).__name__)
            logger.exception('History maintenance %s failed: scene=%s batch=%s revision=%s committed=%s',
                             stage, scene_id, batch.id if batch else None, revision, stage == 'post_commit')
            await self.event_store.save_trace(kind='history_maintenance_error', scene_id=scene_id,
                ref_id=batch.id if batch else 'history:'+uuid.uuid4().hex,
                payload={'error':str(error),'error_type':type(error).__name__,
                         'stage': stage, 'committed': stage == 'post_commit',
                         'knowledge_revision': revision, 'cognition':getattr(error,'trace',{})})
        finally:
            self._maintaining_history_scenes.discard(scene_id)

    async def _on_burst(self, burst: Stimulus) -> None:
        if not self._running or not self.scene_policy.enabled(burst.scene_id):
            return
        if self.mock_turn_handler is None and not self.has_model_profile("conversation"):
            return
        actor = await self.scene_manager.get_or_create_actor(burst.scene_id)
        rejections = []
        allowed = await self._eligible_conversation_events(burst.events, actor.session.last_observed_event_rowid,
                                                          session=actor.session, rejections=rejections)
        if rejections:
            await self.event_store.save_trace(kind='observation', scene_id=burst.scene_id, ref_id=burst.id,
                payload={'source_event_ids': [item['event_id'] for item in rejections],
                         'status': 'not_started', 'at': self.clock(), 'rejections': rejections})
        if not allowed:
            return
        burst = self._burst_from_events(allowed, burst)
        if self.shadow_mode:
            burst.origin_mode = "shadow"
        self.metrics.inc_social("bursts_total")
        pending = self._pending_bursts.get(burst.scene_id)
        self._pending_bursts[burst.scene_id] = self._merge_bursts(pending, burst) if pending else burst
        if burst.scene_id not in self._conversation_tasks:
            self._start_conversation_task(burst.scene_id)

    def _start_conversation_task(self, scene_id: str) -> None:
        task = self._spawn_background_task(self._run_conversation_loop(scene_id))
        self._conversation_tasks[scene_id] = task

        def finished(done):
            if self._conversation_tasks.get(scene_id) is done:
                self._conversation_tasks.pop(scene_id, None)
            if self._running and scene_id in self._pending_bursts and scene_id not in self._conversation_tasks:
                self._start_conversation_task(scene_id)

        task.add_done_callback(finished)

    async def _read_initial_window(self, session: SceneSession, preferred_ids=()) -> tuple[list[Event], int, list[str], frozenset[str]]:
        """Fetch candidates only; ConversationContext owns all request packing."""
        cutoff = session.last_observed_event_rowid
        preferred = set(preferred_ids)
        sources = sorted(session.pending_wakes,
            key=lambda wake:(not (wake.certain and wake.event_id in preferred), wake.rowid))[:self.config.conversation_read_batch_limit]
        source_ids = [wake.event_id for wake in sources]
        required = await self.event_store.events_by_ids(session.scene_id,source_ids,cutoff)
        from len_bot.cognition.input_window import original_remainder
        wakes = {wake.event_id: wake for wake in sources}
        required = [original_remainder(event, wakes[event.id].observation)
                    if not AttentionPolicy._is_obligation(wakes[event.id]) else event for event in required]
        recent = await self.event_store.get_recent_events(session.scene_id,limit=self.config.conversation_history_limit,through_rowid=cutoff,conversation_only=True)
        if session.conversation_segment is not None:
            remembered = await self.event_store.events_by_ids(session.scene_id,
                session.conversation_segment.event_ids,cutoff)
            if {event.id for event in remembered} != set(session.conversation_segment.event_ids):
                raise SceneCommitConflict('Saved conversation window has unavailable original events')
            recent = sorted({event.id:event for event in [*remembered,*recent]}.values(),
                            key=lambda event:event.metadata['_rowid'])[-self.config.conversation_history_limit:]
        recent_ids = frozenset(event.id for event in recent)
        events = sorted({event.id:event for event in [*recent,*required] if conversation_visible(event)
                         and (not event.metadata.get('conversation_resume') or event.id in preferred)}.values(),
                        key=lambda event:event.metadata['_rowid'])
        events = await self.event_store.project_reply_context(session.scene_id,events,through_rowid=cutoff)
        return events,cutoff,source_ids,recent_ids

    async def _eligible_conversation_events(self, events, cutoff, *, session=None, rejections=None):
        """Current eligibility controls scheduling; it never consumes a wake."""
        result = []
        def rejected(event, reason):
            if rejections is not None:
                rejections.append({'event_id': event.id, 'reason': reason})
        for original in events:
            if original.metadata.get('conversation_resume_error'):
                rejected(original, 'wait_claim_rejected')
                continue
            if (original.metadata.get('interaction') != 'chat' or not self.scene_policy.chat_allowed(
                    original.scene_id, original.metadata.get('requester_qq_uid'))):
                rejected(original, 'chat_not_allowed')
                continue
            reasons = original.metadata.get('attention_reasons') or []
            if reasons == ['sample_opportunity'] and not self.attention_policy._attention(original.scene_id).observation_enabled:
                rejected(original, 'periodic_observation_disabled')
                continue
            if session is not None and not should_ingest_social(session, self.config_store.current.time,
                                                               self.clock(), reasons):
                rejected(original, 'sleep')
                continue
            # A ceiling reached while this burst waited still stops the turn,
            # over the same inputs the entry gate weighs and no others.
            if (self._chat_ceiling_applies(original)
                    and await self.rate_limiter.exhausted(original.scene_id,
                                                          original.metadata.get('requester_qq_uid'))):
                rejected(original, 'hourly_limit')
                continue
            event = original.model_copy(deep=True)
            event.metadata['conversation_excluded'] = False
            result.append(event)
        return result

    async def _conversation_snapshot(self, actor, *, resume_event_id=None):
        session = actor.session.model_copy(deep=True)
        pending = await self.event_store.events_by_ids(session.scene_id,
            [wake.event_id for wake in session.pending_wakes], session.last_observed_event_rowid)
        inputs=await self._eligible_conversation_events(pending, session.last_observed_event_rowid, session=session)
        other_resumes={event.id for event in inputs if event.metadata.get('conversation_resume') and event.id!=resume_event_id}
        eligible = {event.id for event in inputs}-other_resumes
        session.pending_wakes = [wake for wake in session.pending_wakes if wake.event_id in eligible]
        return session,other_resumes

    async def _run_conversation_loop(self, scene_id: str) -> None:
        while self._running and scene_id in self._pending_bursts:
            burst = self._pending_bursts.pop(scene_id)
            actor = await self.scene_manager.get_or_create_actor(scene_id)
            pending_ids = {wake.event_id for wake in actor.session.pending_wakes}
            if not pending_ids.intersection(burst.source_event_ids):
                continue
            resumed=[event for event in burst.events if event.metadata.get('conversation_resume')]
            if resumed:
                event=resumed[0]
                remaining=[item for item in burst.events if item.id!=event.id]
                if remaining:self._pending_bursts[scene_id]=self._burst_from_events(remaining,burst)
                burst=self._burst_from_events([event],burst)
            wait_started = time.monotonic()
            acquired = False
            try:
                async with self._cognition_semaphore:
                    acquired = True
                    await self.event_store.save_trace(kind='conversation_wait',scene_id=scene_id,ref_id=burst.id,
                        payload={'source_event_ids':list(burst.source_event_ids),'state':'acquired',
                            'cognition_slot_wait_ms':round((time.monotonic()-wait_started)*1000,2)})
                    await self._run_conversation(actor, burst)
            except BaseException as error:
                if not acquired:
                    try:
                        await self.event_store.save_trace(kind='conversation_wait',scene_id=scene_id,ref_id=burst.id,
                            payload={'source_event_ids':list(burst.source_event_ids),
                                'state':'cancelled' if isinstance(error,asyncio.CancelledError) else 'failed',
                                'error':_error_text(error),'error_type':type(error).__name__,
                                'error_phase':'cognition_slot_wait',
                                'cognition_slot_wait_ms':round((time.monotonic()-wait_started)*1000,2)})
                    except Exception:
                        logger.exception('Could not record conversation slot wait: stimulus=%s',burst.id)
                raise

    async def _run_conversation(self, actor, burst: Stimulus) -> None:
        scene_id = actor.scene_id
        resume_packet=next((event.metadata['conversation_resume'] for event in burst.events
                            if event.metadata.get('conversation_resume')),None)
        resume=ConversationResume.model_validate(resume_packet['state']) if resume_packet else None
        resume_event_id=next((event.id for event in burst.events if event.metadata.get('conversation_resume')),None)
        episode_id = resume.episode_id if resume else f"conversation:{uuid.uuid4().hex}"
        mailbox = EpisodeMailbox(episode_id, scene_id, actor.session.version,
                                 origin_stimulus_id=burst.source_event_ids[0] if burst.source_event_ids else None)
        mailbox.origin_mode = burst.origin_mode
        mailbox.source_started_at = min((event.timestamp for event in burst.events), default=self.clock())
        if resume:
            mailbox.messages_committed=resume.messages_committed
            mailbox.next_checkpoint=resume.next_checkpoint
            mailbox.handled_source_ids.update(resume.source_event_ids)
        if not await actor.acquire_episode_lease(episode_id, mailbox):
            raise SceneCommitConflict(f"Concurrent conversation in {scene_id}")
        started = time.monotonic()
        trace: dict[str, Any] = {'checkpoints':[]}
        if resume:trace['resumed_from']={'loop_id':resume_packet['loop_id'],'send_event_id':resume_packet['send_event_id'],
                                        'model_calls_used':resume.model_calls_used,'tool_calls_used':resume.tool_calls_used}
        other_resume_ids: set[str] = set()
        observed, revision = actor.session.last_observed_event_rowid, actor.session.knowledge_revision
        source_ids: list[str] = []
        delivered_ids: set[str] = set()
        decision: GateDecision | None = None
        outcome: EpisodeOutcome | None = None
        self.metrics.inc_social("cognition_attempts")
        try:
            # The lease is held from here, so the snapshot belongs inside: it
            # reads the database and the ceiling, and an exception between the
            # two used to leave the scene unable to start another turn for the
            # rest of the process.
            session,other_resume_ids = await self._conversation_snapshot(actor,resume_event_id=resume_event_id)
            trace['wake_sources'] = [wake.model_dump() for wake in session.pending_wakes]
            coverage_before = {wake.event_id: wake.observation for wake in session.pending_wakes}
            observed, revision = session.last_observed_event_rowid, session.knowledge_revision
            if not resume and not session.pending_wakes:
                # Current eligibility filtered away everything that woke this
                # turn — a ceiling reached while the burst waited, or a scene
                # switched off. Old history alone is nobody's question, and
                # asking the model about it costs the same as a real turn.
                return
            if resume and resume.runtime_started_at!=self._started_at:
                raise SceneCommitConflict('Suspended conversation belongs to a previous process; review is required, no request is resent')
            read_started = time.monotonic()
            try:
                events, observed, source_ids, recent_ids = await self._read_initial_window(session, burst.source_event_ids)
            finally:
                trace['initial_source_reads_ms'] = round((time.monotonic()-read_started)*1000, 2)
            # What the initial window already handed over, so a later step does
            # not offer the same uncovered stretch a second time.
            initial = set(source_ids)
            offered = {wake.event_id: tuple(wake.observation.ranges) if wake.observation else ()
                       for wake in session.pending_wakes if wake.event_id in initial}
            if resume:
                anchors=await self.event_store.events_by_ids(scene_id,
                    [*resume.source_event_ids,resume_packet['send_event_id']],observed)
                anchors=await self.event_store.project_reply_context(scene_id,anchors,through_rowid=observed)
                events=list({event.id:event for event in [*anchors,*events]}.values())
                events.sort(key=lambda event:event.metadata['_rowid'])
                source_ids=list(dict.fromkeys([*source_ids,*(event.id for event in anchors)]))
            if any(_is_shadow_input(event) for event in events if event.id in source_ids):
                mailbox.origin_mode = "shadow"
            def input_prepared(provided_ids, read_ids):
                delivered_ids.update(provided_ids)
                delivered_ids.update(read_ids)
                mailbox.interaction_actors.update(wake.actor_id for wake in actor.session.pending_wakes
                    if wake.event_id in provided_ids | read_ids and wake.actor_id != self.bot_actor_id
                    and wake.actor_id.startswith('user:'))

            async def observe(*, provided_ranges=None):
                nonlocal observed, source_ids
                if mailbox.is_cancelled() or not self._running or not self.scene_policy.enabled(scene_id):
                    raise asyncio.CancelledError()
                current,reserved = await self._conversation_snapshot(actor,resume_event_id=resume_event_id)
                other_resume_ids.update(reserved)
                if current.knowledge_revision != revision:
                    raise SceneCommitConflict("Knowledge changed during conversation; rebuild from the next real input")
                # The durable pending originals are the backlog. A scan cutoff
                # alone cannot remember omitted messages or long-message gaps.
                unread = []
                coverage = {}
                batch = {}
                for wake in sorted(current.pending_wakes, key=lambda item: item.rowid):
                    span = OriginalCoverage.model_validate(provided_ranges[wake.event_id]) if (
                        provided_ranges and wake.event_id in provided_ranges) else None
                    if span is not None and span.complete:
                        continue
                    prior = wake.observation
                    if span is not None:
                        prior = prior.merged_with(span) if prior is not None else span
                    if prior is not None and prior.complete:
                        continue
                    # Offering the same uncovered stretch again on the next step
                    # would reinstall facts, preferences and a second copy of
                    # the footer without adding a word anybody said, and move
                    # the prefix while doing it. It waits for real progress:
                    # either coverage advances or the next turn picks it up.
                    handed = tuple(prior.ranges) if prior is not None else ()
                    if offered.get(wake.event_id) == handed:
                        continue
                    unread.append(wake.event_id)
                    coverage[wake.event_id] = prior
                    batch[wake.event_id] = handed
                    if len(unread) >= self.config.conversation_read_batch_limit:
                        break
                observed = current.last_observed_event_rowid
                if not unread:
                    return None
                additions = await self.event_store.events_by_ids(scene_id, unread, observed)
                additions = await self.event_store.project_reply_context(scene_id, additions, through_rowid=observed)
                from len_bot.cognition.input_window import original_remainder
                additions = [original_remainder(event, coverage[event.id]) for event in additions]
                offered.update({event.id: batch[event.id] for event in additions})
                source_ids = list(dict.fromkeys([*source_ids, *(event.id for event in additions)]))
                if any(_is_shadow_input(event) for event in additions):
                    mailbox.origin_mode = "shadow"
                return {"session": current, "events": additions, "through_rowid": observed,
                        "source_event_ids": list(source_ids)}

            async def commit(candidate: EpisodeOutcome, *, read_event_ids=None) -> GateDecision:
                nonlocal decision, outcome, source_ids,revision
                outcome = candidate
                if read_event_ids is not None:
                    source_ids = sorted(read_event_ids)
                trace.update({"through_event_rowid": observed, "knowledge_revision": revision,
                              "source_event_ids": list(source_ids),
                              "proposed_outcome": candidate.model_dump(mode="json")})
                decision = await actor.commit_turn(candidate, observed, source_ids, revision, mailbox, self.runtime_gate)
                self._last_gate_decision = decision
                if decision.accepted:
                    if self.memory_index and decision.committed_proposal.committed_memories:
                        self._spawn_background_task(self._index_memories(
                            list(decision.committed_proposal.committed_memories), scene_id=scene_id))
                    outcome = decision.committed_proposal.outcome
                    revision=decision.scene_session.knowledge_revision
                    decision.scene_session.pending_wakes=[wake for wake in decision.scene_session.pending_wakes
                                                          if wake.event_id not in other_resume_ids]
                    trace['checkpoints'].append({'index':outcome.checkpoint_index,'gate':decision.record(),
                        'result':outcome.model_dump(mode='json'),'references':trace.get('references')})
                return decision

            async def publish(checkpoint_decision):
                try:
                    await self.runtime_gate.publish_committed(checkpoint_decision,mailbox)
                finally:
                    for checkpoint in trace['checkpoints']:
                        if checkpoint['gate']['commit_event_id']==checkpoint_decision.commit_event_id:
                            checkpoint['gate']=checkpoint_decision.record()
                self.metrics.inc_social('cognition_committed')
                if checkpoint_decision.committed_proposal.resolved_loop_ids:
                    self.metrics.inc_social('openloops_resolved',len(checkpoint_decision.committed_proposal.resolved_loop_ids))
                if checkpoint_decision.actions_enqueued:self.metrics.inc_social('gate_action')
                if checkpoint_decision.committed_proposal.outcome.handled_source_event_ids:
                    await self.job_runner.on_input_handled(scene_id)
                wake = checkpoint_decision.committed_proposal.outcome.wake_decision
                if wake and wake.decision == 'confirm':
                    await self.receive_event(Event(id='wake:' + checkpoint_decision.commit_event_id,
                        event_type=EventType.SCENE_WAKE_CONFIRMED, scene_id=scene_id, actor_id='system:sleep',
                        timestamp=self.clock(), payload={'request_event_id': wake.request_event_id,
                            'source_event_id': wake.source_event_id, 'commit_event_id': checkpoint_decision.commit_event_id},
                        metadata={'conversation_excluded': True}), _internal=True)

            from len_bot.runtime.sleep_policy import is_asleep
            if is_asleep(session, self.config_store.current.time, self.clock()):
                from len_bot.runtime.wake_confirmation import run_wake_confirmation
                outcome = await run_wake_confirmation(self, session, events, episode_id,
                    commit=commit, publish=publish, input_prepared=input_prepared, trace=trace)
            elif self.mock_turn_handler is not None:
                input_prepared({event.id for event in events},{event.id for event in events})
                outcome = await self.mock_turn_handler(session, events)
                if not isinstance(outcome, EpisodeOutcome):
                    raise TypeError("mock_turn_handler must return EpisodeOutcome")
                decision = await commit(outcome, read_event_ids={event.id for event in events})
                if not decision.accepted:
                    raise CommitConflict(decision.reason)
                await publish(decision)
            else:
                outcome = await self.social_core.run(
                    session, events, observed, episode_id, source_ids, observe=observe, commit=commit, trace=trace,
                    input_prepared=input_prepared, requester_qq_uid=mailbox.requester_qq_uid, recent_event_ids=recent_ids,
                    publish=publish,resume=resume,mailbox=mailbox,
                    save_segment=actor.save_conversation_segment if mailbox.origin_mode == 'live' else None,
                )
            if decision is None:
                raise RuntimeError("Conversation finished without a terminal commit")
            progressed = any(
                (coverage_before.get(ident).merged_with(span) if coverage_before.get(ident) is not None else span)
                != coverage_before.get(ident)
                for ident, span in mailbox.provided_original_ranges.items()
                if ident in coverage_before)
            await self.burst_assembler.discard_provided(scene_id,
                {ident for ident, span in mailbox.provided_original_ranges.items() if span.complete})
            await self._preserve_unhandled_bursts(burst, actor.session, mailbox.handled_source_ids,
                                                  delivered_ids, observation_progress=progressed)
            self.metrics.inc_social("social_cognition")
            self.metrics.inc_social("social_would_speak" if mailbox.messages_committed else "intentional_silence")
            await self._save_conversation_trace(scene_id, episode_id, burst, trace, outcome, decision)
        except asyncio.CancelledError as error:
            if decision and decision.accepted:
                await self.event_store.save_trace(kind="conversation_error", scene_id=scene_id, ref_id=episode_id,
                    payload={"error": "Conversation interrupted after its transaction committed",
                             "error_type": type(error).__name__, "error_phase": "post_commit",
                             "conversation": trace, "source_event_ids": source_ids,
                             "result": decision.committed_proposal.outcome.model_dump(mode='json'),
                             "gate": self._gate_record(decision)})
            raise
        except Exception as error:
            if decision is not None and not decision.accepted:
                self.metrics.inc_social("gate_rejected")
            scheduled = self._pending_bursts.pop(scene_id, None)
            if scheduled:
                unseen = [event for event in scheduled.events if event.id not in delivered_ids]
                if unseen:
                    self._pending_bursts[scene_id] = self._burst_from_events(unseen, scheduled)
            self.metrics.inc_social("cognition_failed")
            if isinstance(error, (SceneCommitConflict, CommitConflict)):
                self.metrics.inc_social("stale_outcomes_rejected")
            logger.error("Conversation failed in %s: %s", scene_id, _error_text(error))
            await self.event_store.save_trace(
                kind="conversation_error", scene_id=scene_id, ref_id=episode_id,
                payload={"error": _error_text(error), "error_type": type(error).__name__, "conversation": trace,
                         "error_phase": "after_checkpoint" if trace['checkpoints'] or resume else "pre_commit",
                         "result": decision.committed_proposal.outcome.model_dump(mode='json')
                             if decision and decision.accepted else None,
                         "source_event_ids": source_ids, "observed_rowid": observed,
                         "gate": self._gate_record(decision), "elapsed_ms": round((time.monotonic() - started) * 1000)},
            )
        finally:
            # Unconditional and first. Releasing is a local assignment; the
            # bookkeeping under it is a database write that can fail or be
            # cancelled, and a lease lost that way is never recovered — the
            # scene simply stops taking turns.
            actor.release_episode_lease(episode_id)
            self.metrics.record_latency("cognition_total", time.monotonic() - started)
            await self.event_store.set_model_call_disposition(episode_id,
                'expression' if mailbox.messages_committed else 'silence' if trace['checkpoints'] else 'rejected')

    async def _preserve_unhandled_bursts(self, current: Stimulus, session: SceneSession, handled_ids, delivered_ids, *, observation_progress=False) -> None:
        pending = self._pending_bursts.pop(current.scene_id, None)
        merged = self._merge_bursts(current, pending) if pending else current
        if handled_ids or observation_progress:
            # Progress consumes at least one finite source. Any other request,
            # including one read but not handled, gets the existing next turn.
            remaining = await self.event_store.events_by_ids(session.scene_id,
                [wake.event_id for wake in session.pending_wakes if handled_ids
                 or wake.observation is not None and not wake.observation.complete
                 or wake.event_id not in delivered_ids], session.last_observed_event_rowid)
        else:
            # An empty completion cannot repeatedly buy a fresh budget. Only
            # genuinely new input not supplied to this attempt may wake again.
            # Wake confirmation only marks human sources as delivered; a
            # TASK_REVIEW (or any other runtime wake) still counts as supplied.
            pending_ids = {wake.event_id for wake in session.pending_wakes}
            supplied = {event.id for event in current.events} | set(current.source_event_ids)
            remaining = [event for event in merged.events
                         if event.id in pending_ids and event.id not in delivered_ids
                         and event.id not in supplied]
        remaining = await self._eligible_conversation_events(remaining, session.last_observed_event_rowid, session=session)
        now = self.clock()
        for event in remaining:
            if event.metadata.get('attention_reasons') == ['sample_opportunity']:
                event.metadata['attention_due_at'] = max(now, session.attention_sample_at or now)
            await self.burst_assembler.ingest(event)

    @staticmethod
    def _burst_from_events(events: list[Event], origin: Stimulus) -> Stimulus:
        return Stimulus(
            scene_id=origin.scene_id,
            stimulus_type=StimulusType.SOCIAL_MESSAGE_BURST if len(events) > 1 else origin.stimulus_type,
            source_event_ids=[event.id for event in events], actor_id=events[-1].actor_id,
            combined_text="\n".join(f"{event.actor_id}: {event.raw_text}" for event in events if event.raw_text),
            has_mention_bot=any(event.is_mention_bot for event in events),
            has_reply_bot=any(event.is_reply_bot for event in events),
            origin_mode=origin.origin_mode, timestamp=events[-1].timestamp, events=events,
        )

    @classmethod
    def _merge_bursts(cls, earlier: Stimulus, later: Stimulus) -> Stimulus:
        events = list({event.id: event for event in [*earlier.events, *later.events]}.values())
        events.sort(key=lambda event: event.metadata.get("_rowid", 0))
        origin = later.model_copy(update={"origin_mode": "shadow" if "shadow" in {earlier.origin_mode, later.origin_mode} else "live"})
        return cls._burst_from_events(events, origin)

    @staticmethod
    def _gate_record(decision: GateDecision | None):
        return decision.record() if decision is not None else None

    async def _save_conversation_trace(self, scene_id, episode_id, burst, trace, outcome, decision):
        committed = decision.committed_proposal
        await self.event_store.save_trace(
            kind="conversation", scene_id=scene_id, ref_id=episode_id,
            payload={
                "burst": {"id": burst.id, "source_event_ids": burst.source_event_ids},
                "conversation": trace, "result": outcome.model_dump(mode="json"), "gate": self._gate_record(decision),
                "durable_effects": {
                    "tasks": [task.id for task in committed.committed_tasks] if committed else [],
                    "memories": [memory.id for memory in committed.committed_memories] if committed else [],
                    "resolved_loops": committed.resolved_loop_ids if committed else [],
                },
                "actions_enqueued": decision.actions_enqueued,
            },
        )
