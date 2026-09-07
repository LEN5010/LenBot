"""Runtime lifecycle and the single event-owned conversation/work handoff."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
import logging
import re
import time
from typing import Any
import uuid

from len_bot.actions.models import ActionItem, DeliveryResult
from len_bot.actions.queue import ActionQueue
from len_bot.cognition.agent_loop import CommitConflict
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition
from len_bot.cognition.providers import ProviderConfig, ProviderRegistry, RoutingConfig
from len_bot.cognition.social_core import SocialCognitionCore
from len_bot.config import RuntimeConfig
from len_bot.events.builder import BurstAssembler
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.events.store import EventStore, ReflectionConflictError
from len_bot.media.service import MediaService
from len_bot.memory.reflection import ReflectionEngine
from len_bot.memory.reflector import LLMReflector
from len_bot.memory.store import MemoryStore
from len_bot.plugins import PluginHost
from len_bot.runtime.gate import GateDecision, RuntimeGate
from len_bot.runtime.job_runner import InformationJobRunner
from len_bot.runtime.metrics import RuntimeMetrics
from len_bot.scenes.actor import SceneCommitConflict
from len_bot.scenes.manager import SceneManager
from len_bot.scenes.models import SceneSession
from len_bot.scheduler.engine import TaskScheduler
from len_bot.state.open_loops import OpenLoopManager

logger = logging.getLogger(__name__)
_READ_BATCH_LIMIT = 200
_HISTORY_LIMIT = 12_000


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
    ):
        self.config, self.clock = config, clock
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
        self.reflection_engine: ReflectionEngine | None = None
        self.provider_registry = ProviderRegistry()
        self.provider_configuration_error: str | None = None
        self.plugin_host = PluginHost(runtime=self)
        self.metrics = RuntimeMetrics()
        self.shadow_mode = True
        self.allowed_scenes = {"group:126300994"}
        self.shadow_would_send_log: deque[dict] = deque(maxlen=500)
        self.action_queue = ActionQueue(
            self.event_store, send_adapter=send_adapter, on_action_event=self._on_action_event,
            bot_actor_id=self.bot_actor_id, action_interceptor=self.prepare_outbound_action,
            shadow_probe=lambda: self.shadow_mode, shadow_recorder=self._record_shadow_action,
        )
        self.action_queue.scene_shadow_probe = self.is_scene_shadow
        self.action_queue.pacing = config.message_pacing
        self.action_queue.validate_before_send = self.validate_outbound_action
        self.scheduler = TaskScheduler(self.event_store, self.receive_event, sweep_interval=5.0, metrics=self.metrics)
        self.scheduler.jobs_enabled_probe = self._work_enabled
        self.open_loop_manager = OpenLoopManager(self.event_store)
        self.runtime_gate = RuntimeGate(
            self.event_store, self.action_queue, scheduler=self.scheduler, metrics=self.metrics,
            origin_mode_provider=lambda: "shadow" if self.shadow_mode else "live",
            bot_actor_id=self.bot_actor_id,
        )
        self.runtime_gate.scene_shadow_probe = self.is_scene_shadow
        self.runtime_gate.jobs_enabled_probe = self._work_enabled
        self.scene_manager = SceneManager(self.bot_actor_id, self.event_store, self._on_scene_event_committed)
        self.burst_assembler = BurstAssembler(config, self._on_burst, clock=clock)
        self.social_core = SocialCognitionCore(self)
        self.job_runner = InformationJobRunner(self)
        self.media_service = MediaService(self)
        self._cognition_semaphore = asyncio.Semaphore(2)
        self._last_gate_decision: GateDecision | None = None
        self._started_at = self.clock()
        self._onebot_adapter = None
        self._running = False
        self._maintenance_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self._reflection_timers: dict[str, asyncio.TimerHandle] = {}
        self._reflecting_scenes: set[str] = set()
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
            provider["id"] == profile["provider_id"] and provider["enabled"]
            for provider in snapshot["providers"]
        ))

    def _work_enabled(self) -> bool:
        return self.config.jobs_enabled and self.has_model_profile("work")

    def update_bot_identity(self, bot_qq: int) -> None:
        self.config.bot_qq = bot_qq
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
        self.memory_store = MemoryStore(self.event_store._db, self.event_store._write_lock, clock=self.clock)
        await self.memory_store.initialize()
        await self._load_configuration()
        self.reflection_engine = ReflectionEngine(
            self.memory_store,
            llm_reflector=LLMReflector(
                lambda: self.provider_registry.resolve("work"), memory_store=self.memory_store,
            ),
        )
        await self._start_workers(recover=True)

    async def _load_configuration(self) -> None:
        saved = await self.event_store.get_dynamic_config("onebot_config") or {}
        for field in (
            "onebot_connection_mode", "onebot_action_transport", "onebot_ws_url",
            "onebot_http_url", "onebot_access_token", "ws_host", "ws_port",
        ):
            if field in saved:
                setattr(self.config, field, saved[field])
        persona = await self.event_store.get_dynamic_config("persona_config") or {}
        for field in ("character_context", "identity_name", "identity_core", "identity_persona", "conversation_style", "address_names"):
            if field in persona:
                setattr(self.config, field, persona[field])
        if "bot_qq" in persona:
            self.update_bot_identity(persona["bot_qq"])

        saved = await self.event_store.get_dynamic_config("provider_config")
        if saved:
            try:
                providers = [ProviderConfig.model_validate(provider) for provider in saved.get("providers", [])]
                await self.provider_registry.apply_update(providers, None)
                routing = RoutingConfig.model_validate(saved["routing"]) if saved.get("routing") else None
                await self.provider_registry.apply_update(providers, routing)
            except (ValueError, TypeError, KeyError):
                self.provider_configuration_error = "保存的模型配置不符合 conversation/work 契约，请在面板明确配置。"
                logger.warning(self.provider_configuration_error)
        shadow = await self.event_store.get_dynamic_config("shadow_config")
        if shadow is not None:
            self.shadow_mode = bool(shadow["enabled"])
        scenes = await self.event_store.get_dynamic_config("delivery_scenes")
        if scenes is not None:
            self.allowed_scenes = set(scenes["scene_ids"])

    async def _start_workers(self, *, recover: bool) -> None:
        self.job_runner = InformationJobRunner(self)
        self._running = True
        restored = await self.event_store.recover_social_work() if recover else []
        await self.action_queue.start()
        await self._load_builtin_plugins()
        self._ingestion_ready.set()
        await self.scheduler.start()
        for scene_id in restored:
            actor = await self.scene_manager.get_or_create_actor(scene_id)
            self._schedule_quiet_window_reflection(actor.session)
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def _load_builtin_plugins(self) -> None:
        from len_bot.plugins.builtin import BUILTIN_PLUGINS

        saved = await self.event_store.get_dynamic_config("plugins_state") or {}
        for plugin_id, factory in BUILTIN_PLUGINS.items():
            plugin = factory()
            state = saved.get(plugin_id, {})
            plugin.manifest.config = state.get("config", dict(plugin.manifest.default_config))
            plugin.manifest.enabled = state.get("enabled", True)
            try:
                await self.plugin_host.load_plugin(plugin)
            except Exception:
                logger.exception("Failed to load builtin plugin %s", plugin_id)

    async def save_plugin_state(self) -> None:
        state = {plugin_id: {"enabled": plugin.manifest.enabled, "config": plugin.manifest.config}
                 for plugin_id, plugin in self.plugin_host._plugins.items()}
        await self.event_store.save_dynamic_config("plugins_state", state)

    async def set_shadow_mode(self, enabled: bool) -> None:
        async with self.config_update_lock:
            if enabled:
                self.shadow_mode = True
            await self.event_store.save_dynamic_config("shadow_config", {"enabled": bool(enabled)})
            self.shadow_mode = bool(enabled)

    def is_scene_shadow(self, scene_id: str) -> bool:
        return not self.action_queue.simulated and scene_id not in self.allowed_scenes

    async def set_delivery_scenes(self, scene_ids: list[str]) -> None:
        if any(not re.fullmatch(r"group:[1-9][0-9]*", scene_id) for scene_id in scene_ids):
            raise ValueError("实发群名单必须是有效的 QQ 群号")
        scenes = set(scene_ids)
        async with self.config_update_lock:
            await self.event_store.save_dynamic_config("delivery_scenes", {"scene_ids": sorted(scenes)})
            self.allowed_scenes = scenes

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
        for timer in self._reflection_timers.values():
            timer.cancel()
        self._reflection_timers.clear()
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
        self._reflecting_scenes.clear()
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
            if self.shadow_mode or self.is_scene_shadow(event.scene_id):
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
        await self._ingestion_ready.wait()
        if not self._running:
            raise RuntimeError("Runtime is not running")
        actor = await self.scene_manager.get_or_create_actor(scene_id)
        session = actor.session.model_copy(deep=True)
        episode_id = f"operator:{uuid.uuid4().hex}"
        mailbox = EpisodeMailbox(episode_id, scene_id, session.version)
        mailbox.origin_mode = "shadow" if self.shadow_mode or self.is_scene_shadow(scene_id) else "live"
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
        return decision

    async def prepare_outbound_action(self, action: ActionItem):
        action = await self.plugin_host.intercept_action(action)
        return await self.media_service.prepare_action(action) if action else None

    async def validate_outbound_action(self, action: ActionItem) -> None:
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
            # A new human message supersedes an in-flight cognition attempt.
            # Keep the event in the mailbox and pending burst; cancelling here
            # prevents a long model call from finishing against an obsolete
            # snapshot and repeatedly retrying a stale work delivery.
            actor = self.scene_manager._actors.get(event.scene_id)
            if self.mock_turn_handler is None and actor is not None and actor._active_mailbox is not None:
                actor._active_mailbox.cancel("新消息已到达，保留未读输入并重新结合最新语境")
                running_turn = self._conversation_tasks.get(event.scene_id)
                if running_turn is not None and running_turn is not asyncio.current_task():
                    running_turn.cancel()
        await self.job_runner.on_event(event)
        job_due = event.event_type == EventType.TASK_DUE and event.payload.get("payload", {}).get("kind") == "agent_job"
        if not job_due:
            await self.burst_assembler.ingest(event)
        await self.scheduler.on_event(event)
        if human or event.event_type == EventType.MESSAGE_SENT:
            self._schedule_quiet_window_reflection(session)

    def _can_reflect(self) -> bool:
        return bool(self.reflection_engine and self.mock_turn_handler is None and self.has_model_profile("work"))

    def _schedule_quiet_window_reflection(self, session: SceneSession) -> None:
        if not self._running or not self._can_reflect():
            return
        previous = self._reflection_timers.pop(session.scene_id, None)
        if previous:
            previous.cancel()
        scene_id = session.scene_id

        def fire():
            self._reflection_timers.pop(scene_id, None)
            if self._running:
                self._spawn_background_task(self._quiet_window_reflect(scene_id))

        self._reflection_timers[scene_id] = asyncio.get_running_loop().call_later(
            max(0.05, self.config.reflection_quiet_window_seconds), fire,
        )

    async def _quiet_window_reflect(self, scene_id: str) -> None:
        if scene_id in self._reflecting_scenes or not self._can_reflect():
            return
        self._reflecting_scenes.add(scene_id)
        try:
            while self._running and self._can_reflect():
                actor = await self.scene_manager.get_or_create_actor(scene_id)
                if actor.has_active_episode():
                    self._schedule_quiet_window_reflection(actor.session)
                    return
                cursor = await self.memory_store.get_reflection_cursor(scene_id)
                events = await self.event_store.get_unreflected_events(scene_id, after_rowid=cursor, limit=30)
                if not events:
                    return
                cutoff = events[-1].metadata["_rowid"]
                revision = actor.session.knowledge_revision
                people = {event.actor_id for event in events}
                context = {
                    "bot_qq": self.config.bot_qq, "bot_actor_id": self.bot_actor_id, "now": self.clock(),
                    "participants": {key: value.model_dump() for key, value in actor.session.participants.items() if key in people},
                    "tasks": await self.event_store.scene_tasks(scene_id),
                    "jobs": await self.event_store.list_jobs(scene_id),
                }
                result = await self.reflection_engine.reflect_on_events(scene_id, events, context)
                shadow = self.shadow_mode or self.is_scene_shadow(scene_id) or any(_is_shadow_input(event) for event in events)
                review_event = Event(
                    event_type=EventType.REFLECTION_RECORDED, scene_id=scene_id, actor_id="system:reflection",
                    timestamp=self.clock(), metadata={"needs_review": bool(result.review_items)},
                    payload={
                        "review_items": [item.model_dump() for item in result.review_items],
                        "raw_text": "反思核对：" + "；".join(item.summary for item in result.review_items) if result.review_items else "",
                        "origin_mode": "shadow" if shadow else "live",
                    },
                )
                await actor.commit_reflection(
                    proposals=result.memory_proposals, source_event_ids=[event.id for event in events],
                    new_cursor_rowid=cutoff, review_event=review_event,
                    expected_cursor_rowid=cursor, expected_revision=revision,
                )
                await self.event_store.save_trace(
                    kind="reflection", scene_id=scene_id, ref_id=review_event.id,
                    payload={"cognition": result.trace, "source_event_ids": [event.id for event in events],
                             "through_event_rowid": cutoff, "result": result.model_dump(mode="json")},
                )
                self._record_model_metrics(result.trace)
        except ReflectionConflictError:
            session = self.scene_manager.get_session(scene_id)
            if session is not None:
                self._schedule_quiet_window_reflection(session)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("Reflection failed in %s", scene_id)
            await self.event_store.save_trace(
                kind="reflection_error", scene_id=scene_id, ref_id=f"reflection:{uuid.uuid4().hex}",
                payload={"error": str(error), "error_type": type(error).__name__,
                         "cognition": getattr(error, "trace", {})},
            )
        finally:
            self._reflecting_scenes.discard(scene_id)

    async def _on_burst(self, burst: Stimulus) -> None:
        if not self._running:
            return
        if self.mock_turn_handler is None and not self.has_model_profile("conversation"):
            return
        if self.shadow_mode or self.is_scene_shadow(burst.scene_id):
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

    async def _read_initial_window(self, session: SceneSession) -> tuple[list[Event], int, list[str]]:
        unread = await self.event_store.get_events_since(session.scene_id, session.last_cognized_event_rowid, limit=_READ_BATCH_LIMIT)
        unread = [event for event in unread if event.metadata["_rowid"] <= session.last_observed_event_rowid]
        cutoff = unread[-1].metadata["_rowid"] if unread else session.last_observed_event_rowid
        events = await self.event_store.get_recent_events(session.scene_id, limit=_HISTORY_LIMIT, through_rowid=cutoff)
        events = await self.event_store.project_reply_context(session.scene_id, events, through_rowid=cutoff)
        return events, cutoff, [event.id for event in unread]

    async def _run_conversation_loop(self, scene_id: str) -> None:
        while self._running and scene_id in self._pending_bursts:
            burst = self._pending_bursts.pop(scene_id)
            actor = await self.scene_manager.get_or_create_actor(scene_id)
            if burst.events and all(
                event.metadata.get("_rowid", 0) <= actor.session.last_cognized_event_rowid for event in burst.events
            ):
                continue
            async with self._cognition_semaphore:
                await self._run_conversation(actor, burst)

    async def _run_conversation(self, actor, burst: Stimulus) -> None:
        scene_id = actor.scene_id
        episode_id = f"conversation:{uuid.uuid4().hex}"
        mailbox = EpisodeMailbox(episode_id, scene_id, actor.session.version,
                                 origin_stimulus_id=burst.source_event_ids[0] if burst.source_event_ids else None)
        mailbox.origin_mode = burst.origin_mode
        mailbox.source_started_at = min((event.timestamp for event in burst.events), default=self.clock())
        if not actor.acquire_episode_lease(episode_id, mailbox):
            raise SceneCommitConflict(f"Concurrent conversation in {scene_id}")
        started = time.monotonic()
        trace: dict[str, Any] = {}
        session = actor.session.model_copy(deep=True)
        observed, revision = session.last_observed_event_rowid, session.knowledge_revision
        source_ids: list[str] = []
        decision: GateDecision | None = None
        outcome: EpisodeOutcome | None = None
        self.metrics.inc_social("cognition_attempts")
        try:
            events, observed, source_ids = await self._read_initial_window(session)
            if any(_is_shadow_input(event) for event in events if event.metadata["_rowid"] > session.last_cognized_event_rowid):
                mailbox.origin_mode = "shadow"
            mailbox.acknowledge_through(observed)

            async def observe():
                nonlocal observed, source_ids
                if mailbox.is_cancelled() or not self._running:
                    raise asyncio.CancelledError()
                current = actor.session.model_copy(deep=True)
                if current.knowledge_revision != revision:
                    raise SceneCommitConflict("Knowledge changed during conversation; rebuild from the next real input")
                if current.last_observed_event_rowid == observed:
                    return None
                additions = await self.event_store.get_events_since(scene_id, observed, limit=_READ_BATCH_LIMIT)
                additions = [event for event in additions if event.metadata["_rowid"] <= current.last_observed_event_rowid]
                cutoff = additions[-1].metadata["_rowid"] if additions else observed
                additions = await self.event_store.project_reply_context(scene_id, additions, through_rowid=cutoff)
                observed = cutoff
                source_ids = list(dict.fromkeys([*source_ids, *(event.id for event in additions)]))
                if any(_is_shadow_input(event) for event in additions):
                    mailbox.origin_mode = "shadow"
                mailbox.acknowledge_through(observed)
                return {"session": current, "events": additions, "through_rowid": observed,
                        "source_event_ids": list(source_ids)}

            async def commit(candidate: EpisodeOutcome) -> GateDecision:
                nonlocal decision, outcome
                outcome = candidate
                trace.update({"through_event_rowid": observed, "knowledge_revision": revision,
                              "source_event_ids": list(source_ids),
                              "proposed_outcome": candidate.model_dump(mode="json")})
                decision = await actor.commit_turn(candidate, observed, source_ids, revision, mailbox, self.runtime_gate)
                self._last_gate_decision = decision
                self.metrics.inc_social("cognition_committed" if decision.accepted else "gate_rejected")
                if decision.actions_enqueued:
                    self.metrics.inc_social("gate_action")
                if decision.accepted:
                    self._preserve_unread_bursts(burst, observed)
                return decision

            if self.mock_turn_handler is not None:
                outcome = await self.mock_turn_handler(session, events)
                if not isinstance(outcome, EpisodeOutcome):
                    raise TypeError("mock_turn_handler must return EpisodeOutcome")
                decision = await commit(outcome)
                if not decision.accepted:
                    raise CommitConflict(decision.reason)
            else:
                outcome = await self.social_core.run(
                    session, events, observed, episode_id, source_ids, observe=observe, commit=commit, trace=trace,
                )
            if decision is None:
                raise RuntimeError("Conversation finished without a terminal commit")
            self.metrics.inc_social("social_cognition")
            self.metrics.inc_social("social_would_speak" if outcome.disposition == FinalDisposition.ACTION else "intentional_silence")
            await self._save_conversation_trace(scene_id, episode_id, burst, trace, outcome, decision)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if mailbox.is_cancelled() and mailbox.cancellation_reason() == "新消息已到达，保留未读输入并重新结合最新语境":
                # This is deliberate supersession, not a failed model turn.
                # The new event remains pending and will start the next burst.
                self.metrics.inc_social("stale_outcomes_rejected")
                return
            self.metrics.inc_social("cognition_failed")
            if isinstance(error, (SceneCommitConflict, CommitConflict)):
                self.metrics.inc_social("stale_outcomes_rejected")
            logger.exception("Conversation failed in %s", scene_id)
            await self.event_store.save_trace(
                kind="conversation_error", scene_id=scene_id, ref_id=episode_id,
                payload={"error": str(error), "error_type": type(error).__name__, "conversation": trace,
                         "source_event_ids": source_ids, "observed_rowid": observed,
                         "gate": self._gate_record(decision), "elapsed_ms": round((time.monotonic() - started) * 1000)},
            )
        finally:
            self._record_model_metrics(trace)
            self.metrics.record_latency("cognition_total", time.monotonic() - started)
            actor.release_episode_lease(episode_id)

    def _preserve_unread_bursts(self, current: Stimulus, through_rowid: int) -> None:
        pending = self._pending_bursts.pop(current.scene_id, None)
        merged = self._merge_bursts(current, pending) if pending else current
        remaining = [event for event in merged.events if event.metadata.get("_rowid", 0) > through_rowid]
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
        return None if decision is None else {
            "accepted": decision.accepted, "disposition": decision.disposition.value,
            "reason": decision.reason, "action_ids": decision.action_ids,
        }

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

    def _record_model_metrics(self, trace: dict) -> None:
        total_ms = 0
        for step in trace.get("steps", []):
            if "provider_id" not in step or "model" not in step:
                continue
            role = step.get("role", "conversation")
            if "latency_ms" in step:
                usage = step.get("usage", {})
                self.metrics.record_call(
                    role, step["provider_id"], step["model"], step["latency_ms"] / 1000,
                    usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                )
                total_ms += step["latency_ms"]
            elif step.get("failure_reason"):
                self.metrics.record_error(role, step["provider_id"], step["model"], step["failure_reason"])
        if total_ms:
            self.metrics.record_latency("model_total", total_ms / 1000)
