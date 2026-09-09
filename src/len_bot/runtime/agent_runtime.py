"""Runtime lifecycle and the single event-owned conversation/work handoff."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
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
from len_bot.cognition.social_core import SocialCognitionCore
from len_bot.config import RuntimeConfig
from len_bot.config_store import ConfigStore, RootConfig
from len_bot.events.builder import BurstAssembler
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.events.store import EventStore
from len_bot.memory.history import HistoryConflictError
from len_bot.media.service import MediaService
from len_bot.memory.reflection import ReflectionEngine
from len_bot.memory.reflector import LLMReflector
from len_bot.memory.store import MemoryStore
from len_bot.plugins import PluginHost
from len_bot.runtime.gate import GateDecision, RuntimeGate
from len_bot.runtime.job_runner import InformationJobRunner
from len_bot.runtime.metrics import RuntimeMetrics
from len_bot.runtime.attention import AttentionPolicy, HUMAN_INPUTS
from len_bot.runtime.scene_policy import ScenePolicy, conversation_visible
from len_bot.runtime.plugin_interactions import classify_event, handle_calendar_command, handle_live_announcement, validate_native_origin
from len_bot.scenes.actor import SceneCommitConflict
from len_bot.scenes.manager import SceneManager
from len_bot.scenes.models import SceneSession
from len_bot.scheduler.engine import TaskScheduler
from len_bot.state.open_loops import OpenLoopManager

logger = logging.getLogger(__name__)


def _is_shadow_input(event: Event) -> bool:
    source_types = {
        EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED,
        EventType.TASK_DUE, EventType.TASK_REVIEW, EventType.AGENT_JOB_FINISHED,
        EventType.AGENT_JOB_PROGRESS, EventType.REFLECTION_RECORDED,
        EventType.MESSAGE_SEND_FAILED, EventType.LIVE_STARTED, EventType.LIVE_ENDED,
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
        self.history_engine: ReflectionEngine | None = None
        self.provider_registry = ProviderRegistry()
        self.provider_configuration_error: str | None = None
        self.plugin_host = PluginHost(runtime=self)
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
        self.runtime_gate.validate_native_origin = lambda mailbox, scene: validate_native_origin(self, mailbox, scene)
        self.attention_policy = AttentionPolicy(config, clock)
        if attention_random is not None:
            self.attention_policy.random_source = attention_random
        self.scene_manager = SceneManager(self.bot_actor_id, self.event_store, self._on_scene_event_committed,
                                          attention_policy=self.attention_policy,
                                          classify_event=lambda event, cutoff: classify_event(self, event, cutoff))
        self.burst_assembler = BurstAssembler(config, self._on_burst, clock=clock)
        self.social_core = SocialCognitionCore(self)
        self.job_runner = InformationJobRunner(self)
        self.media_service = MediaService(self)
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

    def _spawn_background_task(self, coroutine: Awaitable[Any]) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def has_model_profile(self, role: str) -> bool:
        snapshot = self.provider_registry.snapshot()
        profile = (snapshot.get("routing") or {}).get(role)
        return bool(profile and any(
            provider["id"] == profile["provider_id"] and provider["enabled"] and provider["api_key_masked"]
            for provider in snapshot["providers"]
        ))

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
        if job['model_steps']>=self.config.job_max_steps:
            return 'This work has no remaining model steps; its spent budget is not reset by resume'
        if job['elapsed_seconds']>=self.config.job_max_seconds:
            return 'This work has no remaining execution time; its spent budget is not reset by resume'
        if job['execution_status']=='partial' and job['tool_calls']>=self.config.job_max_tool_calls:
            return 'This partial work has no remaining read-tool budget; continuing does not reset its counters'
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
        await self._load_configuration()
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

    async def update_runtime_settings(self, values: dict, *, live: bool) -> None:
        from len_bot.config import EXECUTION_BUDGET_FIELDS
        async with self.config_update_lock:
            data = self.config_store.current.model_dump()
            data["runtime"].update(values)
            candidate = RootConfig.model_validate(data)
            self.config_store.save(candidate)
            live_keys = set(values) if live else set(values) & EXECUTION_BUDGET_FIELDS
            if live_keys:
                self.config = self.config.model_copy(update={key: getattr(candidate.runtime, key) for key in live_keys})
                self.attention_policy.config = self.config
                self.burst_assembler.config = self.config
            if not live and any(getattr(candidate.runtime, key) != getattr(self.config, key)
                                for key in values if key not in live_keys):
                self.restart_required = True

    async def _start_workers(self, *, recover: bool) -> None:
        self.job_runner = InformationJobRunner(self)
        self._running = True
        restored = await self.event_store.recover_social_work() if recover else []
        await self.action_queue.start()
        await self._load_builtin_plugins()
        self._ingestion_ready.set()
        await self.scheduler.start()
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

    async def _load_builtin_plugins(self) -> None:
        from len_bot.plugins.builtin import get_builtin_plugins

        settings = self.config_store.current.plugins
        factories = get_builtin_plugins()
        if set(settings) != set(factories):
            raise ValueError("plugins must explicitly configure every installed builtin plugin")
        for plugin_id, factory in factories.items():
            state = settings[plugin_id]
            if state.config is None:
                continue
            if plugin_id in {'asoul_calendar', 'asoul_dynamics'} and self.config_store.current.time is None:
                continue  # Explicitly unconfigured and disabled; RootConfig refuses enablement.
            kwargs = {'config': state.config, 'enabled': state.enabled}
            if plugin_id in {'asoul_calendar', 'asoul_dynamics', 'bilibili_live_sensor', 'group_summary'}:
                kwargs['config'] = state.parsed_config
            if plugin_id in {'asoul_calendar', 'asoul_dynamics'}:
                kwargs.update(time_settings=self.config_store.current.time, members=self.config_store.current.members)
            plugin = factory(**kwargs)
            await self.plugin_host.load_plugin(plugin)

    async def update_plugin_settings(self, plugin_id, *, enabled=None, values=None):
        async with self.config_update_lock:
            data = self.config_store.current.model_dump()
            state = data["plugins"][plugin_id]
            if enabled is not None:
                state["enabled"] = enabled
            if values is not None:
                state["config"] = {**(state['config'] or {}), **values}
            candidate = RootConfig.model_validate(data)
            self.config_store.save(candidate)
            if self.plugin_host.has_plugin(plugin_id):
                self.plugin_host.set_plugin_config(plugin_id, candidate.plugins[plugin_id].config)
            if enabled is True and self.plugin_host.has_plugin(plugin_id):
                await self.plugin_host.enable_plugin(plugin_id)
            elif enabled is False:
                await self.plugin_host.disable_plugin(plugin_id)
            if values is not None:
                self.restart_required = True
            if not self.plugin_host.has_plugin(plugin_id):
                self.restart_required = True

    async def set_shadow_mode(self, enabled: bool) -> None:
        async with self.config_update_lock:
            data = self.config_store.current.model_dump()
            data["delivery"]["shadow"] = enabled
            candidate = RootConfig.model_validate(data)
            self.config_store.save(candidate)
            self.shadow_mode = enabled

    async def update_root_settings(self, section: str, values) -> None:
        if section not in {'access', 'time', 'members'}:
            raise ValueError('Unknown settings section')
        async with self.config_update_lock:
            data = self.config_store.current.model_dump()
            data[section] = values
            self.config_store.save(RootConfig.model_validate(data))
            if section in {'time', 'members'}:
                self.restart_required = True

    async def update_scene_settings(self, scene_id: str, values: dict) -> None:
        async with self.config_update_lock:
            data = self.config_store.current.model_dump()
            data['scenes'][scene_id] = values
            self.config_store.save(RootConfig.model_validate(data))
        actor = self.scene_manager._actors.get(scene_id)
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
            try:
                await self.runtime_gate.publish_committed(decision, mailbox)
            finally:
                await self.event_store.save_trace(kind='conversation', scene_id=scene_id, ref_id=episode_id,
                    payload={'operator_control': True, 'source_event_ids': evidence,
                             'result': decision.committed_proposal.outcome.model_dump(mode='json'),
                             'gate': self._gate_record(decision)})
        return decision

    async def prepare_outbound_action(self, action: ActionItem) -> ActionItem:
        return await self.media_service.prepare_action(action)

    async def validate_outbound_action(self, action: ActionItem) -> None:
        if action.output_kind == 'chat':
            if not self.scene_policy.chat_allowed(action.scene_id, action.requester_qq_uid):
                raise ValueError('本群已停用或请求者没有普通对话资格')
        else:
            await validate_native_origin(self, action, action.scene_id)
        if any(segment.type == 'at_all' for segment in action.segments) and (
                action.output_kind != 'announcement' or not self.scene_policy.scene(action.scene_id).mention_all):
            raise ValueError('本群当前公告未开启全体提及')
        for segment in action.segments:
            if segment.type == "image" and (
                not self.config.media_enabled
                or await self.event_store.get_media(segment.asset_id, [action.scene_id, "global-safe"]) is None
            ):
                raise ValueError("图片已停用或不在本场景中")

    async def _on_action_event(self, event: Event) -> None:
        if event.event_type == EventType.MESSAGE_SENT:
            self.metrics.inc_social("simulated_messages" if event.metadata.get("simulated") else "visible_messages")
        await self.receive_event(event, _internal=True)

    async def _on_scene_event_committed(self, session: SceneSession, event: Event) -> None:
        if not self._running:
            return
        human = event.event_type in {EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED} and event.actor_id != self.bot_actor_id
        if human:
            self.metrics.inc_social("human_messages")
        if event.metadata.get('interaction') == 'calendar_command':
            if event.metadata.get('command_allowed'):
                self._spawn_background_task(handle_calendar_command(self, event))
            return
        if event.event_type == EventType.LIVE_STARTED and event.payload.get('notification'):
            if self.scene_policy.announcement_allowed(event.scene_id, event.payload['member']):
                self._spawn_background_task(handle_live_announcement(self, event))
            await self.scheduler.on_event(event)
            return
        if not self.scene_policy.enabled(event.scene_id):
            return
        await self.job_runner.on_event(event)
        job_due = event.event_type == EventType.TASK_DUE and event.payload.get("payload", {}).get("kind") == "agent_job"
        if not job_due and event.metadata.get("attention_reasons"):
            await self.burst_assembler.ingest(event)
        await self.scheduler.on_event(event)
        if (human or event.event_type == EventType.MESSAGE_SENT) and conversation_visible(event):
            self._schedule_history_maintenance(session)
            if self._can_maintain_history():
                self._spawn_background_task(self._maintain_history(session.scene_id, quiet=False))

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
        if not self._can_maintain_history():
            raise ValueError('maintenance profile is not configured')
        batch = await self.event_store.load_history_batch(batch_id)
        if batch.scene_id in self._maintaining_history_scenes:
            raise ValueError('History maintenance is already running for this scene')
        actor = await self.scene_manager.get_or_create_actor(batch.scene_id)
        if actor.has_active_episode():
            raise ValueError('Conversation is running; retry maintenance when this turn has finished')
        batch = await self.event_store.retry_history_batch(batch_id)
        self._spawn_background_task(self._maintain_history(batch.scene_id, retry_batch=batch))

    async def _maintain_history(self, scene_id: str, *, quiet=True, retry_batch=None) -> None:
        if scene_id in self._maintaining_history_scenes or not self._can_maintain_history() or not self.scene_policy.maintenance_allowed(scene_id):
            return
        self._maintaining_history_scenes.add(scene_id)
        batch = retry_batch
        try:
            while self._running and self._can_maintain_history() and self.scene_policy.maintenance_allowed(scene_id):
                actor = await self.scene_manager.get_or_create_actor(scene_id)
                if actor.has_active_episode():
                    self._schedule_history_maintenance(actor.session)
                    return
                if batch is None:
                    batch = await self.event_store.begin_history_batch(scene_id,
                        target_tokens=self.config.history_target_tokens, min_tokens=self.config.history_min_tokens,
                        quiet=quiet)
                if batch is None:
                    return
                revision = actor.session.knowledge_revision
                context = {'bot_qq':self.config.bot_qq, 'bot_actor_id':self.bot_actor_id, 'now':self.clock()}
                result = await self.history_engine.maintain_batch(scene_id, batch, context)
                review_event = Event(
                    event_type=EventType.REFLECTION_RECORDED, scene_id=scene_id, actor_id='system:maintenance',
                    timestamp=self.clock(), metadata={'needs_review':bool(result.review_items)},
                    payload={'batch_id':batch.id,
                        'review_items':[item.model_dump() for item in result.review_items],
                        'raw_text':'历史核对：'+'；'.join(item.summary for item in result.review_items) if result.review_items else '',
                        'origin_mode':'shadow' if self.shadow_mode else 'live'})
                await actor.commit_history(batch_id=batch.id, proposals=result.memory_proposals,
                    summary=result.summary, key_event_ids=result.key_event_ids,
                    review_event=review_event, expected_revision=revision)
                await self.event_store.save_trace(kind='history_maintenance', scene_id=scene_id, ref_id=batch.id,
                    payload={'cognition':result.trace, 'source_event_ids':batch.source_event_ids,
                             'result':result.model_dump(mode='json')})
                batch = None
                # A caught-up small tail only runs after the actual quiet window.
                quiet = False
        except asyncio.CancelledError:
            # A pending batch remains unconfirmed across interruption.
            raise
        except Exception as error:
            if batch is not None:
                await self.event_store.fail_history_batch(batch.id, type(error).__name__)
            logger.exception('History maintenance failed in %s', scene_id)
            await self.event_store.save_trace(kind='history_maintenance_error', scene_id=scene_id,
                ref_id=batch.id if batch else 'history:'+uuid.uuid4().hex,
                payload={'error':str(error),'error_type':type(error).__name__,
                         'cognition':getattr(error,'trace',{})})
        finally:
            self._maintaining_history_scenes.discard(scene_id)

    async def _on_burst(self, burst: Stimulus) -> None:
        if not self._running or not self.scene_policy.enabled(burst.scene_id):
            return
        if self.mock_turn_handler is None and not self.has_model_profile("conversation"):
            return
        actor = await self.scene_manager.get_or_create_actor(burst.scene_id)
        allowed = await self._eligible_conversation_events(burst.events, actor.session.last_observed_event_rowid)
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

    async def _read_initial_window(self, session: SceneSession, preferred_ids=()) -> tuple[list[Event], int, list[str]]:
        """Fetch candidates only; ConversationContext owns all request packing."""
        cutoff = session.last_observed_event_rowid
        preferred = set(preferred_ids)
        sources = sorted(session.pending_wakes,
            key=lambda wake:(wake.event_id not in preferred,not wake.certain,-wake.rowid))[:self.config.conversation_read_batch_limit]
        source_ids = [wake.event_id for wake in sources]
        required = await self.event_store.events_by_ids(session.scene_id,source_ids,cutoff)
        recent = await self.event_store.get_recent_events(session.scene_id,limit=self.config.conversation_history_limit,through_rowid=cutoff,conversation_only=True)
        events = sorted({event.id:event for event in [*required,*recent] if conversation_visible(event)}.values(),
                        key=lambda event:event.metadata['_rowid'])
        events = await self.event_store.project_reply_context(session.scene_id,events,through_rowid=cutoff)
        return events,cutoff,source_ids

    async def _eligible_conversation_events(self, events, cutoff):
        """Current eligibility controls scheduling; it never consumes a wake."""
        result = []
        for original in events:
            if (original.metadata.get('interaction') != 'chat' or not self.scene_policy.chat_allowed(
                    original.scene_id, original.metadata.get('requester_qq_uid'))):
                continue
            event = original.model_copy(deep=True)
            event.metadata['conversation_excluded'] = False
            result.append(event)
        return result

    async def _conversation_snapshot(self, actor):
        session = actor.session.model_copy(deep=True)
        pending = await self.event_store.events_by_ids(session.scene_id,
            [wake.event_id for wake in session.pending_wakes], session.last_observed_event_rowid)
        eligible = {event.id for event in await self._eligible_conversation_events(pending, session.last_observed_event_rowid)}
        session.pending_wakes = [wake for wake in session.pending_wakes if wake.event_id in eligible]
        return session

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
            async with self._cognition_semaphore:
                await self._run_conversation(actor, burst)

    async def _run_conversation(self, actor, burst: Stimulus) -> None:
        scene_id = actor.scene_id
        resume_packet=next((event.metadata['conversation_resume'] for event in burst.events
                            if event.metadata.get('conversation_resume')),None)
        resume=ConversationResume.model_validate(resume_packet['state']) if resume_packet else None
        episode_id = resume.episode_id if resume else f"conversation:{uuid.uuid4().hex}"
        mailbox = EpisodeMailbox(episode_id, scene_id, actor.session.version,
                                 origin_stimulus_id=burst.source_event_ids[0] if burst.source_event_ids else None)
        mailbox.origin_mode = burst.origin_mode
        mailbox.source_started_at = min((event.timestamp for event in burst.events), default=self.clock())
        if resume:
            mailbox.messages_committed=resume.messages_committed
            mailbox.next_checkpoint=resume.next_checkpoint
            mailbox.handled_source_ids.update(resume.source_event_ids)
        if not actor.acquire_episode_lease(episode_id, mailbox):
            raise SceneCommitConflict(f"Concurrent conversation in {scene_id}")
        started = time.monotonic()
        trace: dict[str, Any] = {'checkpoints':[]}
        if resume:trace['resumed_from']={'loop_id':resume_packet['loop_id'],'send_event_id':resume_packet['send_event_id'],
                                        'model_calls_used':resume.model_calls_used,'tool_calls_used':resume.tool_calls_used}
        session = await self._conversation_snapshot(actor)
        trace['wake_sources'] = [wake.model_dump() for wake in session.pending_wakes]
        observed, revision = session.last_observed_event_rowid, session.knowledge_revision
        source_ids: list[str] = []
        delivered_ids: set[str] = set()
        decision: GateDecision | None = None
        outcome: EpisodeOutcome | None = None
        self.metrics.inc_social("cognition_attempts")
        try:
            if resume and resume.runtime_started_at!=self._started_at:
                raise SceneCommitConflict('Suspended conversation belongs to a previous process; review is required, no request is resent')
            events, observed, source_ids = await self._read_initial_window(session, burst.source_event_ids)
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

            async def observe():
                nonlocal observed, source_ids
                if mailbox.is_cancelled() or not self._running or not self.scene_policy.enabled(scene_id):
                    raise asyncio.CancelledError()
                current = await self._conversation_snapshot(actor)
                if current.knowledge_revision != revision:
                    raise SceneCommitConflict("Knowledge changed during conversation; rebuild from the next real input")
                if current.last_observed_event_rowid == observed:
                    return None
                additions = await self.event_store.get_events_since(scene_id, observed, limit=self.config.conversation_read_batch_limit)
                additions = [event for event in additions if event.metadata["_rowid"] <= current.last_observed_event_rowid]
                cutoff = additions[-1].metadata["_rowid"] if additions else observed
                additions = await self._eligible_conversation_events(additions, cutoff)
                additions = await self.event_store.project_reply_context(scene_id, additions, through_rowid=cutoff)
                observed = cutoff
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
                    outcome = decision.committed_proposal.outcome
                    revision=decision.scene_session.knowledge_revision
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

            if self.mock_turn_handler is not None:
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
                    input_prepared=input_prepared, requester_qq_uid=mailbox.requester_qq_uid,
                    publish=publish,resume=resume,
                )
            if decision is None:
                raise RuntimeError("Conversation finished without a terminal commit")
            await self._preserve_unhandled_bursts(burst, actor.session, mailbox.handled_source_ids, delivered_ids)
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
            await self.event_store.set_model_call_disposition(episode_id,
                'expression' if mailbox.messages_committed else 'silence' if trace['checkpoints'] else 'rejected')
            self.metrics.record_latency("cognition_total", time.monotonic() - started)
            actor.release_episode_lease(episode_id)

    async def _preserve_unhandled_bursts(self, current: Stimulus, session: SceneSession, handled_ids, delivered_ids) -> None:
        pending = self._pending_bursts.pop(current.scene_id, None)
        merged = self._merge_bursts(current, pending) if pending else current
        if handled_ids:
            # Progress consumes at least one finite source. Any other request,
            # including one read but not handled, gets the existing next turn.
            remaining = await self.event_store.events_by_ids(session.scene_id,
                [wake.event_id for wake in session.pending_wakes], session.last_observed_event_rowid)
        else:
            # An empty completion cannot repeatedly buy a fresh budget. Only
            # genuinely new input not supplied to this attempt may wake again.
            pending_ids = {wake.event_id for wake in session.pending_wakes}
            remaining = [event for event in merged.events if event.id in pending_ids and event.id not in delivered_ids]
        remaining = await self._eligible_conversation_events(remaining, session.last_observed_event_rowid)
        if remaining:
            self._pending_bursts[current.scene_id] = self._burst_from_events(remaining, merged)

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
