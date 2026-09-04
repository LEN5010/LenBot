import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Optional, Callable, Awaitable, Any
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.events.store import EventStore
from len_bot.events.builder import BurstAssembler
from len_bot.scenes.manager import SceneManager
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.models import FinalDisposition
from len_bot.actions.models import ActionItem
from len_bot.actions.queue import ActionQueue
from len_bot.runtime.gate import RuntimeGate, GateDecision
from len_bot.scheduler.engine import TaskScheduler
from len_bot.state.open_loops import OpenLoopManager

from len_bot.memory.store import MemoryStore
from len_bot.memory.gate import MemoryGate
from len_bot.memory.reflection import ReflectionEngine
from len_bot.cognition.providers import ProviderConfig, ProviderRegistry, RouteTarget, RoutingConfig
from len_bot.cognition.router import CognitiveTier
from len_bot.cognition.social_core import SocialCognitionCore
from len_bot.cognition.fast_core import FastSocialCognition
from len_bot.cognition.session import FastCognitionResult, FastDecisionAction, SocialDecisionAction
from len_bot.runtime.metrics import RuntimeMetrics
from len_bot.runtime.style_guard import StyleGuard
from len_bot.plugins import PluginHost
from len_bot.tools.retrieval import RetrievalToolkit

logger = logging.getLogger(__name__)

class AgentRuntime:
    def __init__(
        self,
        config: RuntimeConfig,
        send_adapter: Optional[Callable[[ActionItem], Awaitable[bool]]] = None,
        mock_social_handler: Optional[Callable] = None,
        mock_fast_handler: Optional[Callable] = None,
    ):
        self.config = config
        self.mock_social_handler = mock_social_handler
        self.mock_fast_handler = mock_fast_handler
        self.bot_actor_id = f"user:{config.bot_qq}"
        
        self.event_store = EventStore(config.db_path)
        self.memory_store: Optional[MemoryStore] = None
        self.memory_gate: Optional[MemoryGate] = None
        self.reflection_engine: Optional[ReflectionEngine] = None

        self.plugin_host = PluginHost(runtime=self)

        self.shadow_mode = False
        self.shadow_would_send_log: deque[dict] = deque(maxlen=500)
        self.action_queue = ActionQueue(
            event_store=self.event_store,
            send_adapter=send_adapter,
            on_action_event=self._on_action_event,
            bot_actor_id=self.bot_actor_id,
            action_interceptor=self.plugin_host.intercept_action,
            shadow_probe=lambda: self.shadow_mode,
            shadow_recorder=self._record_shadow_action
        )
        self.metrics = RuntimeMetrics()
        self.scheduler = TaskScheduler(
            event_store=self.event_store,
            emit_event=self.receive_event,
            sweep_interval=5.0,
            metrics=self.metrics
        )
        self.open_loop_manager = OpenLoopManager(self.event_store)
        self.runtime_gate = RuntimeGate(
            event_store=self.event_store,
            action_queue=self.action_queue,
            scheduler=self.scheduler,
            metrics=self.metrics,
            origin_mode_provider=lambda: ("shadow" if self.shadow_mode else "live"),
            next_wake_min_interval_seconds=config.next_wake_min_interval_seconds
        )

        self.scene_manager = SceneManager(
            bot_actor_id=self.bot_actor_id,
            event_store=self.event_store,
            on_state_updated=self._on_scene_event_committed
        )
        
        self.burst_assembler = BurstAssembler(
            config=config,
            on_burst=self._on_burst,
        )
        self.provider_registry = ProviderRegistry()
        self.social_core = SocialCognitionCore(
            config=config,
            registry=self.provider_registry,
            metrics=self.metrics,
            mock_handler=mock_social_handler,
        )
        # ADR-0038: FAST one-shot social cognition + anti-slop style guard
        self.fast_core = FastSocialCognition(
            config=config,
            registry=self.provider_registry,
            metrics=self.metrics,
            mock_handler=mock_fast_handler,
        )
        self.style_guard = StyleGuard()

        self._cognition_semaphore = asyncio.Semaphore(2)  # Max 2 concurrent episodes (§93)
        self._last_gate_decision: Optional[GateDecision] = None
        self._started_at = time.time()
        self._onebot_adapter = None
        self._running = False
        self._maintenance_task: Optional[asyncio.Task] = None
        self._background_tasks: set[asyncio.Task] = set()
        self._reflection_timers: dict[str, asyncio.TimerHandle] = {}
        self._social_pending: dict[str, Stimulus] = {}
        self._social_tasks: dict[str, asyncio.Task] = {}
        self._social_active_bursts: dict[str, Stimulus] = {}
        self._social_inference_scenes: set[str] = set()
        self._direct_retry_counts: dict[str, int] = {}
        self._style_retry_counts: dict[str, int] = {}

    def _spawn_background_task(self, coro: Awaitable[Any]) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def update_bot_identity(self, bot_qq: int) -> None:
        """Apply the identity reported by the connected OneBot implementation."""
        self.config.bot_qq = bot_qq
        self.bot_actor_id = f"user:{bot_qq}"
        self.action_queue.bot_actor_id = self.bot_actor_id
        self.scene_manager.bot_actor_id = self.bot_actor_id
        for actor in self.scene_manager._actors.values():
            actor.bot_actor_id = self.bot_actor_id

    async def start(self) -> None:
        await self.event_store.initialize()
        self.memory_store = MemoryStore(self.event_store._db, write_lock=self.event_store._write_lock)
        await self.memory_store.initialize()
        self.memory_gate = MemoryGate(self.memory_store, self.event_store)
        self.runtime_gate.memory_gate = self.memory_gate

        saved_onebot = await self.event_store.get_dynamic_config("onebot_config")
        if saved_onebot:
            for field in (
                "onebot_connection_mode",
                "onebot_action_transport",
                "onebot_ws_url",
                "onebot_http_url",
                "onebot_access_token",
                "ws_host",
                "ws_port",
            ):
                if field in saved_onebot:
                    setattr(self.config, field, saved_onebot[field])

        # Load dynamic configurations from database if present
        saved_persona = await self.event_store.get_dynamic_config("persona_config")
        if saved_persona:
            self.config.identity_name = saved_persona.get("identity_name", self.config.identity_name)
            if saved_persona.get("identity_core"):
                self.config.identity_core = saved_persona["identity_core"]
            self.config.identity_persona = saved_persona.get("identity_persona", self.config.identity_persona)
            self.config.conversation_style = saved_persona.get(
                "conversation_style", self.config.conversation_style
            )
            self.update_bot_identity(saved_persona.get("bot_qq", self.config.bot_qq))

        # Provider Registry (ADR-0020): load persisted providers+routing; if absent,
        # ONE-TIME migrate the legacy single-provider model_config, then persist.
        saved_providers = await self.event_store.get_dynamic_config("provider_config")
        if saved_providers and saved_providers.get("routing"):
            providers = [ProviderConfig(**p) for p in saved_providers.get("providers", [])]
            routing = RoutingConfig(**saved_providers["routing"])
            catalog_migrated = False
            by_id = {provider.id: provider for provider in providers}
            route_targets = [routing.normal, routing.deliberate]
            if routing.fast is not None:
                route_targets.append(routing.fast)
            if routing.fallback is not None:
                route_targets.append(routing.fallback)
            for target in route_targets:
                provider = by_id.get(target.provider_id)
                if provider is not None and target.model not in provider.models:
                    provider.models.append(target.model)
                    catalog_migrated = True
            await self.provider_registry.apply_update(providers, routing)
            if catalog_migrated:
                await self.event_store.save_dynamic_config(
                    "provider_config", self.provider_registry.export()
                )
        else:
            legacy = await self.event_store.get_dynamic_config("model_config") or {}
            seed_provider = ProviderConfig(
                id="default",
                base_url=legacy.get("openai_base_url", self.config.openai_base_url),
                api_key=self.config.openai_api_key,
                models=list(dict.fromkeys([
                    legacy.get("default_model", self.config.default_model),
                    legacy.get("deliberate_model", self.config.deliberate_model),
                ])),
            )
            seed_routing = RoutingConfig(
                normal=RouteTarget(provider_id="default", model=legacy.get("default_model", self.config.default_model)),
                deliberate=RouteTarget(provider_id="default", model=legacy.get("deliberate_model", self.config.deliberate_model)),
            )
            await self.provider_registry.apply_update([seed_provider], seed_routing)
            await self.event_store.save_dynamic_config("provider_config", self.provider_registry.export())
            logger.info("Migrated legacy model_config into provider_config (one-time, ADR-0020)")

        # ADR-0028 §11: Wire LLMReflector AFTER provider_registry has loaded providers and routing
        has_live_provider = self.provider_registry.has_live_provider()
        if self.mock_social_handler is None and has_live_provider:
            from len_bot.memory.reflector import LLMReflector

            def _resolve_reflection_route():
                res = self.provider_registry.resolve(CognitiveTier.NORMAL)
                return res.client, res.model

            reflector = LLMReflector(resolver=_resolve_reflection_route)
            self.reflection_engine = ReflectionEngine(self.memory_store, self.memory_gate, llm_reflector=reflector, event_store=self.event_store)
        else:
            self.reflection_engine = ReflectionEngine(self.memory_store, self.memory_gate, event_store=self.event_store)

        # Shadow Mode (ADR-0023): hot-toggleable, persisted across restarts
        saved_shadow = await self.event_store.get_dynamic_config("shadow_config")
        if saved_shadow:
            self.shadow_mode = bool(saved_shadow.get("enabled", False))

        self._running = True
        await self.action_queue.start()
        await self.scheduler.start()
        await self._load_builtin_plugins()
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def _load_builtin_plugins(self) -> None:
        """ADR-0021: instantiate builtin plugins with persisted config & enable flags."""
        from len_bot.plugins.builtin import BUILTIN_PLUGINS
        saved = await self.event_store.get_dynamic_config("plugins_state") or {}
        for pid, factory in BUILTIN_PLUGINS.items():
            plugin = factory()
            state = saved.get(pid, {})
            plugin.manifest.config = state.get("config", dict(plugin.manifest.default_config))
            plugin.manifest.enabled = state.get("enabled", True)
            try:
                await self.plugin_host.load_plugin(plugin)
            except Exception as e:
                logger.error("Failed to load builtin plugin '%s': %s", pid, e)

    async def save_plugin_state(self) -> None:
        """Persist current plugin enable flags & config (Control Plane + restarts)."""
        state = {
            pid: {"enabled": p.manifest.enabled, "config": p.manifest.config}
            for pid, p in self.plugin_host._plugins.items()
        }
        await self.event_store.save_dynamic_config("plugins_state", state)

    async def set_shadow_mode(self, enabled: bool) -> None:
        """ADR-0023: hot-toggle Shadow Mode and persist the flag."""
        self.shadow_mode = bool(enabled)
        await self.event_store.save_dynamic_config("shadow_config", {"enabled": self.shadow_mode})
        logger.info("Shadow Mode %s", "ENABLED (no physical sends)" if enabled else "disabled")

    async def _record_shadow_action(self, action: ActionItem) -> None:
        """Shadow recorder: 'what WOULD have been sent' — observation only, no social fact."""
        self.metrics.inc_social("would_send")
        self.shadow_would_send_log.append({
            "action_id": action.id,
            "scene_id": action.scene_id,
            "action_type": action.action_type.value if hasattr(action.action_type, "value") else str(action.action_type),
            "content": action.content,
            "reply_to": action.reply_to,
            "recorded_at": time.time()
        })

    async def stop(self) -> None:
        self._running = False
        await self.burst_assembler.close()
        self._social_pending.clear()
        self._social_active_bursts.clear()
        self._social_inference_scenes.clear()
        for timer in self._reflection_timers.values():
            timer.cancel()
        self._reflection_timers.clear()
        if self._maintenance_task:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass

        for t in list(self._background_tasks):
            t.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

        await self.scheduler.stop()
        await self.scene_manager.stop()
        await self.plugin_host.unload_all()
        await self.action_queue.stop()
        await self.event_store.close()

    async def _maintenance_loop(self) -> None:
        """Periodic background heartbeat for durable open-loop and memory maintenance."""
        while self._running:
            try:
                await asyncio.sleep(max(0.05, self.config.maintenance_interval_seconds))
                if not self._running:
                    break

                # 1. Sweep expired Open Loops past absolute TTL
                await self.open_loop_manager.sweep_ttl_expiration()

                # 2. Temporal decay of stale epistemic beliefs (ADR-0015/0019)
                if self.memory_store:
                    await self.memory_store.decay_memories()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in AgentRuntime background maintenance heartbeat: %s", e)

    async def receive_event(self, event: Event) -> None:
        """Entrypoint for all inbound events. Dispatches to SceneActor (single commit authority)."""
        await self.scene_manager.dispatch_event(event)

    async def _on_scene_event_committed(self, state, event: Event) -> None:
        """Invoked by SceneActor AFTER Event and SceneState are atomically committed in SQLite."""
        if event.event_type in (EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED) and event.actor_id != self.bot_actor_id:
            self.metrics.inc_social("human_messages")

        await self.burst_assembler.ingest(event)

        # Condition-bound obligations (ADR-0018): fire tasks whose wake_event_type
        # matches this committed event. Firing takes the standard TASK_DUE path.
        await self.scheduler.on_event(event)

        # Quiet-window reflection (ADR-0019 §10.3): (re)arm the debounce timer —
        # reflection fires only after the scene stays quiet for a full window.
        if self.reflection_engine and state:
            self._schedule_quiet_window_reflection(state)

    def _schedule_quiet_window_reflection(self, state) -> None:
        loop = asyncio.get_running_loop()
        old_timer = self._reflection_timers.pop(state.scene_id, None)
        if old_timer:
            old_timer.cancel()
        delay = max(0.05, self.config.reflection_quiet_window_seconds)
        scene_id = state.scene_id

        def _fire() -> None:
            # Create the coroutine only when the timer actually fires, so a
            # cancelled timer never leaves an un-awaited coroutine behind.
            self._spawn_background_task(self._quiet_window_reflect(scene_id))

        self._reflection_timers[scene_id] = loop.call_later(delay, _fire)

    async def _quiet_window_reflect(self, scene_id: str) -> None:
        try:
            if not (self.reflection_engine and self.memory_store and self.event_store):
                return
            if self.scene_manager.has_active_episode(scene_id):
                # In-flight episode running in scene; postpone reflection (ADR-0028, §12)
                state = self.scene_manager.get_scene_state(scene_id)
                if state:
                    self._schedule_quiet_window_reflection(state)
                return

            cursor_rowid = await self.memory_store.get_reflection_cursor(scene_id)
            # ADR-0028, §10.1: Batch size 30 unreflected events
            events = await self.event_store.get_unreflected_events(scene_id, after_rowid=cursor_rowid, limit=30)
            if not events:
                return

            new_cursor_rowid = max(int(e.metadata.get("_rowid", 0)) for e in events)

            episode_record, proposals, world_patch = await self.reflection_engine.reflect_on_events(scene_id, events)
            if episode_record is not None:
                # ADR-0028, §10.2: Atomic Reflection Batch Commit
                await self.event_store.commit_reflection_batch(
                    scene_id=scene_id,
                    episode_record=episode_record,
                    proposals=proposals,
                    new_cursor_rowid=new_cursor_rowid
                )
                logger.info("Reflection batch committed on scene %s: cursor -> %s (%d events)",
                            scene_id, new_cursor_rowid, len(events))

                # ADR-0038: deferred social-world cognition rides the standard
                # event path after the episode record is durable.
                if world_patch is not None:
                    await self._submit_deferred_patch(scene_id, world_patch)

                # If there are more unreflected events, reflect on the next batch immediately
                remaining = await self.event_store.get_unreflected_events(scene_id, after_rowid=new_cursor_rowid, limit=1)
                if remaining:
                    self._spawn_background_task(self._quiet_window_reflect(scene_id))
        except Exception as e:
            logger.warning("Quiet-window reflection failed on scene %s: %s", scene_id, e)

    async def _on_action_event(self, event: Event) -> None:
        """Called by ActionQueue on MESSAGE_SENT or MESSAGE_SEND_FAILED."""
        if event.event_type == EventType.MESSAGE_SENT:
            self.metrics.inc_social("visible_messages")
        await self.scene_manager.dispatch_event(event)

    async def _on_burst(self, burst: Stimulus) -> None:
        """Every valid scene burst enters the Social Cognition Core."""
        if (
            self.social_core.mock_handler is None
            and self.fast_core.mock_handler is None
            and not self.provider_registry.has_live_provider()
        ):
            self._spawn_background_task(
                self.event_store.save_trace(
                    kind="social_cognition_error",
                    scene_id=burst.scene_id,
                    ref_id=burst.id,
                    payload={
                        "error": "No Social Core provider configured",
                        "source_event_ids": burst.source_event_ids,
                    },
                )
            )
            return
        self._queue_social_cognition(burst)

    def _queue_social_cognition(self, burst: Stimulus) -> None:
        scene_id = burst.scene_id
        pending = self._social_pending.get(scene_id)
        self._social_pending[scene_id] = (
            self._merge_social_bursts(pending, burst) if pending else burst
        )
        task = self._social_tasks.get(scene_id)
        active = self._social_active_bursts.get(scene_id)
        if (
            (burst.has_mention_bot or burst.has_reply_bot)
            and active is not None
            and scene_id in self._social_inference_scenes
            and task is not None
            and not task.done()
        ):
            self._social_pending[scene_id] = self._merge_social_bursts(
                active,
                self._social_pending[scene_id],
            )
            logger.info("Direct social event preempting in-flight cognition on scene %s", scene_id)
            task.cancel()
        if scene_id not in self._social_tasks:
            self._start_social_task(scene_id)

    def _start_social_task(self, scene_id: str) -> None:
        task = self._spawn_background_task(self._run_social_cognition_loop(scene_id))
        self._social_tasks[scene_id] = task
        task.add_done_callback(lambda _task: self._social_task_finished(scene_id))

    def _social_task_finished(self, scene_id: str) -> None:
        self._social_tasks.pop(scene_id, None)
        if self._running and scene_id in self._social_pending:
            self._start_social_task(scene_id)

    async def _run_social_cognition_loop(self, scene_id: str) -> None:
        while scene_id in self._social_pending:
            burst = self._social_pending.pop(scene_id)
            self.metrics.inc_social("bursts_total")
            self._social_active_bursts[scene_id] = burst
            actor = await self.scene_manager.get_or_create_actor(scene_id)
            episode_id = f"social_{uuid.uuid4().hex[:12]}"
            scene_state = actor.state
            mailbox = EpisodeMailbox(
                episode_id=episode_id,
                scene_id=scene_id,
                base_scene_version=scene_state.version,
                origin_stimulus_id=(burst.source_event_ids[0] if burst.source_event_ids else None),
            )
            mailbox.origin_mode = burst.origin_mode
            if not actor.acquire_episode_lease(episode_id, mailbox):
                raise RuntimeError(f"Concurrent Social Core episode in scene {scene_id}")

            try:
                session = actor.group_session.model_copy(deep=True)
                through_event_rowid = session.last_observed_event_rowid
                cognition_started = time.monotonic()
                self.metrics.record_latency("event_to_burst", max(0.0, time.time() - burst.timestamp))
                raw_events = await self.event_store.get_recent_events(scene_id, limit=12_000)
                open_loops = await self.event_store.get_active_open_loops(scene_id)
                pending_next_wake = await self.event_store.get_pending_next_wake(scene_id)
                retrieval = RetrievalToolkit(
                    event_store=self.event_store,
                    allowed_scopes=[scene_id, "global-safe"],
                    default_scene_id=scene_id,
                    memory_store=self.memory_store,
                )
                voice_examples = await self._select_voice_examples(scene_id)

                fast_result: FastCognitionResult | None = None
                core_trace: dict[str, Any] = {"mode": "live", "steps": [], "escalations": []}

                async with self._cognition_semaphore:
                    self._social_inference_scenes.add(scene_id)
                    try:
                        # ADR-0038: FAST one-shot path for casual social bursts;
                        # TASK_DUE / tool synthesis bursts go straight to FULL.
                        if self._route_cognition(burst) == "fast":
                            try:
                                fast_result, core_trace = await self._run_fast_cognition(
                                    scene_id=scene_id,
                                    session=session,
                                    burst=burst,
                                    raw_events=raw_events,
                                    open_loops=open_loops,
                                    voice_examples=voice_examples,
                                    started=cognition_started,
                                )
                                if fast_result is not None and fast_result.decision == FastDecisionAction.FULL:
                                    core_trace["escalated_to_full"] = True
                                    self.metrics.inc_social("cognition_fast_to_full")
                                    fast_result = None
                            except Exception as fast_error:
                                # Unrecoverable FAST failure escalates to FULL —
                                # never to a hallucinated reply, never to a drop.
                                logger.warning(
                                    "FAST cognition failed on %s; escalating to FULL: %s",
                                    scene_id, fast_error,
                                )
                                core_trace = {
                                    "mode": "live", "path": "fast",
                                    "fast_error": str(fast_error)[:200],
                                    "steps": [], "escalations": [],
                                }
                                self.metrics.inc_social("cognition_fast_to_full")
                                fast_result = None
                        if fast_result is None:
                            self.metrics.inc_social("cognition_full_calls")
                            self.metrics.record_latency("burst_to_request", time.monotonic() - cognition_started)
                            result, core_trace = await self.social_core.execute(
                                session=session,
                                burst=burst,
                                raw_events=raw_events,
                                active_open_loops=open_loops,
                                pending_next_wake=pending_next_wake,
                                toolkit=retrieval,
                                voice_examples=voice_examples,
                            )
                    finally:
                        self._social_inference_scenes.discard(scene_id)
                self.metrics.inc_social("social_cognition")
                self._record_model_latency(core_trace)

                if fast_result is None:
                    # ADR-0034: a next-wake TASK_DUE that saw no new human/plugin social
                    # evidence since task creation must not renew itself.
                    if result.future_attention is not None and await self._is_evidence_free_next_wake(burst):
                        logger.info(
                            "Next-wake renewal rejected on scene %s: no new external events since task creation",
                            scene_id,
                        )
                        result.future_attention = None

                mode = "shadow" if self.shadow_mode else "live"
                if fast_result is not None:
                    accepted = await actor.submit_fast_cognition(
                        result=fast_result,
                        through_event_rowid=through_event_rowid,
                        source_event_ids=burst.source_event_ids,
                        mode=mode,
                    )
                else:
                    accepted = await actor.submit_social_cognition(
                        result=result,
                        through_event_rowid=through_event_rowid,
                        source_event_ids=burst.source_event_ids,
                        mode=mode,
                    )
                if not accepted:
                    self.metrics.inc_social("stale_outcomes_rejected")
                    pending = self._social_pending.get(scene_id)
                    self._social_pending[scene_id] = (
                        self._merge_social_bursts(burst, pending) if pending else burst
                    )
                    await self._save_social_trace(
                        burst=burst,
                        episode_id=episode_id,
                        core_trace=core_trace,
                        result=fast_result if fast_result is not None else result,
                        session_commit_accepted=False,
                        gate_decision=None,
                    )
                    continue

                self._direct_retry_counts.pop("|".join(burst.source_event_ids), None)
                self._style_retry_counts.pop("|".join(burst.source_event_ids), None)

                commit_result = fast_result if fast_result is not None else result
                outcome = commit_result.to_episode_outcome(scene_id, through_event_rowid=through_event_rowid)
                gate_started = time.monotonic()
                gate_decision = await actor.submit_proposal(
                    episode_id=episode_id,
                    outcome=outcome,
                    mailbox=mailbox,
                    runtime_gate=self.runtime_gate,
                )
                self.metrics.record_latency("gate", time.monotonic() - gate_started)
                self._last_gate_decision = gate_decision

                if fast_result is not None:
                    spoke = fast_result.decision == FastDecisionAction.SPEAK
                else:
                    spoke = result.decision.action == SocialDecisionAction.SPEAK
                if spoke:
                    self.metrics.inc_social("social_would_speak")
                else:
                    self.metrics.inc_social("intentional_silence")
                if gate_decision.disposition == FinalDisposition.ACTION:
                    self.metrics.inc_social("gate_action")
                    if not burst.has_mention_bot and not burst.has_reply_bot:
                        self.metrics.inc_social(
                            "unsolicited_visible_messages",
                            gate_decision.actions_enqueued,
                        )
                    if fast_result is not None:
                        for message in fast_result.messages:
                            self.style_guard.observe(scene_id, message.content)
                    else:
                        for message in result.message_proposals:
                            self.style_guard.observe(scene_id, message.content)

                await self._save_social_trace(
                    burst=burst,
                    episode_id=episode_id,
                    core_trace=core_trace,
                    result=commit_result,
                    session_commit_accepted=True,
                    gate_decision=gate_decision,
                )
            except Exception as error:
                logger.exception("Social cognition failed on %s: %s", scene_id, error)
                await self.event_store.save_trace(
                    kind="social_cognition_error",
                    scene_id=scene_id,
                    ref_id=burst.id,
                    payload={
                        "error": str(error),
                        "source_event_ids": burst.source_event_ids,
                    },
                )
                retry_key = "|".join(burst.source_event_ids)
                if (burst.has_mention_bot or burst.has_reply_bot) and self._direct_retry_counts.get(retry_key, 0) < 1:
                    self._direct_retry_counts[retry_key] = 1
                    pending = self._social_pending.get(scene_id)
                    self._social_pending[scene_id] = (
                        self._merge_social_bursts(burst, pending) if pending else burst
                    )
                    logger.info("Direct OneBot request retained for one retry on scene %s", scene_id)
                else:
                    self._direct_retry_counts.pop(retry_key, None)
            finally:
                if self._social_active_bursts.get(scene_id) is burst:
                    self._social_active_bursts.pop(scene_id, None)
                self._social_inference_scenes.discard(scene_id)
                actor.release_episode_lease(episode_id)

    def _route_cognition(self, burst: Stimulus) -> str:
        """ADR-0038: structural depth routing — never a social judgement.

        `fast` for human chat bursts; `full` when FAST is disabled, when no FAST
        handler is available in mock mode, or for system-originated bursts that
        need planning/tool synthesis (TASK_DUE, tool completions).
        """
        if not self.config.fast_cognition_enabled or self.fast_core is None:
            return "full"
        if self.social_core.mock_handler is not None and self.fast_core.mock_handler is None:
            return "full"
        if burst.stimulus_type == StimulusType.PROACTIVE_TASK:
            return "full"
        if any(event.event_type == EventType.TOOL_COMPLETED for event in burst.events):
            return "full"
        return "fast"

    async def _select_voice_examples(self, scene_id: str) -> list[dict[str, Any]]:
        try:
            return await self.event_store.select_voice_examples(
                scene_id, self.config.voice_example_count
            )
        except Exception as error:
            logger.debug("Voice exemplar selection skipped on %s: %s", scene_id, error)
            return []

    async def _run_fast_cognition(
        self,
        scene_id: str,
        session,
        burst: Stimulus,
        raw_events,
        open_loops,
        voice_examples: list[dict[str, Any]],
        started: float,
    ) -> tuple[FastCognitionResult, dict[str, Any]]:
        self.metrics.inc_social("cognition_fast_calls")
        self.metrics.record_latency("burst_to_request", time.monotonic() - started)
        fast_result, trace = await self.fast_core.execute(
            session=session,
            burst=burst,
            raw_events=raw_events,
            active_open_loops=open_loops,
            voice_examples=voice_examples,
        )
        if fast_result.decision == FastDecisionAction.SPEAK:
            self.metrics.inc_social("cognition_fast_speak")
            fast_result = await self._style_guard_retry(
                scene_id=scene_id,
                burst=burst,
                session=session,
                raw_events=raw_events,
                open_loops=open_loops,
                voice_examples=voice_examples,
                fast_result=fast_result,
                trace=trace,
            )
        elif fast_result.decision == FastDecisionAction.SILENCE:
            self.metrics.inc_social("cognition_fast_silence")
        return fast_result, trace

    async def _style_guard_retry(
        self,
        scene_id: str,
        burst: Stimulus,
        session,
        raw_events,
        open_loops,
        voice_examples: list[dict[str, Any]],
        fast_result: FastCognitionResult,
        trace: dict[str, Any],
    ) -> FastCognitionResult:
        """ADR-0038 §7: at most one corrective FAST retry on a strong anomaly."""
        content = fast_result.messages[0].content if fast_result.messages else ""
        signal = self.style_guard.evaluate(scene_id, content)
        if not signal.strong_anomaly:
            return fast_result
        self.metrics.inc_social("style_slop_flags")
        retry_key = "|".join(burst.source_event_ids)
        if self._style_retry_counts.get(retry_key, 0) >= 1:
            return fast_result
        self._style_retry_counts[retry_key] = 1
        self.metrics.inc_social("style_retries")
        correction = (
            f"你刚才的输出被风格守卫标记({signal.describe()}),与你最近的发言太像。"
            "换一种更自然的说法:更短、更随意,或者完全换一个反应。仍然只输出符合约定的 JSON。"
        )
        try:
            retry_result, _retry_trace = await self.fast_core.execute(
                session=session,
                burst=burst,
                raw_events=raw_events,
                active_open_loops=open_loops,
                voice_examples=voice_examples,
                correction=correction,
            )
        except Exception as error:
            logger.warning("Style retry failed on %s, keeping original: %s", scene_id, error)
            return fast_result
        trace["style_retry"] = True
        return retry_result

    def _record_model_latency(self, core_trace: dict[str, Any]) -> None:
        total_ms = sum(step.get("latency_ms", 0) for step in core_trace.get("steps", []))
        if not total_ms and core_trace.get("latency_ms"):
            total_ms = core_trace["latency_ms"]
        if total_ms:
            self.metrics.record_latency("model_total", total_ms / 1000.0)

    async def _submit_deferred_patch(self, scene_id: str, patch) -> None:
        """ADR-0038: deferred reflection patch enters through the standard event
        path so the SceneActor stays the single session writer."""
        event = Event(
            event_type=EventType.SOCIAL_COGNITION_RECORDED,
            scene_id=scene_id,
            actor_id="system:reflection",
            timestamp=time.time(),
            payload={"kind": "deferred_patch", "patch": patch.model_dump(mode="json")},
            metadata={"source": "quiet_window_reflection"},
        )
        await self.receive_event(event)

    @staticmethod
    def _merge_social_bursts(earlier: Stimulus, later: Stimulus) -> Stimulus:
        chat_types = {EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED}
        if not all(event.event_type in chat_types for event in [*earlier.events, *later.events]):
            return later

        events = []
        seen_ids: set[str] = set()
        for event in [*earlier.events, *later.events]:
            if event.id not in seen_ids:
                seen_ids.add(event.id)
                events.append(event)

        actor_ids = {event.actor_id for event in events}
        if len(actor_ids) > 1:
            combined_text = "\n".join(
                f"{event.actor_id}: {event.raw_text}" for event in events if event.raw_text
            )
        else:
            combined_text = "\n".join(event.raw_text for event in events if event.raw_text)

        return Stimulus(
            scene_id=later.scene_id,
            stimulus_type=(
                StimulusType.SOCIAL_MESSAGE_BURST
                if len(events) > 1
                else later.stimulus_type
            ),
            source_event_ids=[event.id for event in events],
            actor_id=later.actor_id,
            combined_text=combined_text,
            has_mention_bot=earlier.has_mention_bot or later.has_mention_bot,
            has_reply_bot=earlier.has_reply_bot or later.has_reply_bot,
            origin_mode=later.origin_mode,
            timestamp=later.timestamp,
            events=events,
        )

    async def _is_evidence_free_next_wake(self, burst: Stimulus) -> bool:
        """Whether a next-wake TASK_DUE has no newer human/plugin social evidence."""
        if burst.stimulus_type != StimulusType.PROACTIVE_TASK or not burst.events:
            return False
        due_event = burst.events[0]
        task_payload = due_event.payload.get("payload")
        if not isinstance(task_payload, dict) or task_payload.get("kind") != "next_wake":
            return False
        created_rowid = int(task_payload.get("observed_event_rowid") or 0)
        events_after = await self.event_store.get_events_since(
            burst.scene_id, after_rowid=created_rowid, limit=200
        )
        evidence_types = {
            EventType.GROUP_MESSAGE_RECEIVED,
            EventType.PRIVATE_MESSAGE_RECEIVED,
            EventType.LIVE_STARTED,
            EventType.LIVE_ENDED,
            EventType.TOOL_COMPLETED,
            EventType.USER_JOINED,
        }
        return not any(event.event_type in evidence_types for event in events_after)

    async def _save_social_trace(
        self,
        burst: Stimulus,
        episode_id: str,
        core_trace: dict[str, Any],
        result,
        session_commit_accepted: bool,
        gate_decision: Optional[GateDecision],
    ) -> None:
        committed = gate_decision.committed_proposal if gate_decision else None
        payload = {
            "burst": {
                "id": burst.id,
                "type": burst.stimulus_type.value,
                "actor_id": burst.actor_id,
                "text": burst.combined_text[:300],
                "source_event_ids": burst.source_event_ids,
            },
            "cognition": core_trace,
            "path": core_trace.get("path", "full"),
            "result": result.model_dump(mode="json"),
            "session_commit_accepted": session_commit_accepted,
            "gate": (
                {
                    "accepted": gate_decision.accepted,
                    "disposition": gate_decision.disposition.value,
                    "reason": gate_decision.reason,
                }
                if gate_decision
                else None
            ),
            "durable_effects": {
                "tasks": [task.id for task in committed.committed_tasks] if committed else [],
                "resolved_loops": committed.resolved_loop_ids if committed else [],
                "memories": [memory.id for memory in committed.committed_memories] if committed else [],
            },
            "actions_enqueued": gate_decision.actions_enqueued if gate_decision else 0,
            "shadow": self.shadow_mode,
        }
        await self.event_store.save_trace(
            kind="social_cognition",
            scene_id=burst.scene_id,
            ref_id=episode_id,
            payload=payload,
        )
