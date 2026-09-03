import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Optional, Callable, Awaitable, Any
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.events.store import EventStore
from len_bot.events.builder import StimulusBuilder
from len_bot.scenes.manager import SceneManager
from len_bot.attention.engine import AttentionEngine
from len_bot.attention.models import AttentionDisposition, AttentionResult
from len_bot.cognition.assembler import ContextAssembler
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.models import FinalDisposition
from len_bot.cognition.pi_core import PiAgentCore
from len_bot.cognition.manager import EpisodeManager
from len_bot.actions.models import ActionItem
from len_bot.actions.queue import ActionQueue
from len_bot.runtime.gate import RuntimeGate, GateDecision
from len_bot.scheduler.engine import TaskScheduler
from len_bot.state.open_loops import OpenLoopManager

from len_bot.memory.store import MemoryStore
from len_bot.memory.gate import MemoryGate
from len_bot.memory.reflection import ReflectionEngine
from len_bot.state.ambient import AmbientStore
from len_bot.cognition.providers import ProviderConfig, ProviderRegistry, RouteTarget, RoutingConfig
from len_bot.cognition.router import CognitiveTier
from len_bot.runtime.metrics import RuntimeMetrics
from len_bot.plugins import PluginHost

logger = logging.getLogger(__name__)

class AgentRuntime:
    def __init__(
        self,
        config: RuntimeConfig,
        send_adapter: Optional[Callable[[ActionItem], Awaitable[bool]]] = None,
        mock_pi_handler: Optional[Callable] = None
    ):
        self.config = config
        self.mock_pi_handler = mock_pi_handler
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
        self.scheduler = TaskScheduler(
            event_store=self.event_store,
            emit_event=self.receive_event,
            sweep_interval=5.0
        )
        self.open_loop_manager = OpenLoopManager(self.event_store)
        self.ambient_store = AmbientStore()
        self.metrics = RuntimeMetrics()
        self.runtime_gate = RuntimeGate(
            event_store=self.event_store,
            action_queue=self.action_queue,
            scheduler=self.scheduler,
            ambient_store=self.ambient_store,
            metrics=self.metrics
        )

        self.scene_manager = SceneManager(
            bot_actor_id=self.bot_actor_id,
            event_store=self.event_store,
            on_state_updated=self._on_scene_event_committed
        )
        
        self.stimulus_builder = StimulusBuilder(
            config=config,
            on_stimulus=self._on_stimulus
        )
        self.attention_engine = AttentionEngine(config)
        
        self.context_assembler = ContextAssembler(config)
        self.provider_registry = ProviderRegistry()
        self.pi_core = PiAgentCore(
            config,
            registry=self.provider_registry,
            metrics=self.metrics,
            mock_handler=mock_pi_handler
        )
        self.episode_manager = EpisodeManager(
            scene_manager=self.scene_manager,
            context_assembler=self.context_assembler,
            pi_core=self.pi_core,
            event_store=self.event_store,
            plugin_host=self.plugin_host
        )

        self._cognition_semaphore = asyncio.Semaphore(2)  # Max 2 concurrent episodes (§93)
        self._last_attention_result: Optional[AttentionResult] = None
        self._last_gate_decision: Optional[GateDecision] = None
        self._started_at = time.time()
        self._onebot_adapter = None
        self._running = False
        self._maintenance_task: Optional[asyncio.Task] = None
        self._background_tasks: set[asyncio.Task] = set()
        self._reflection_timers: dict[str, asyncio.TimerHandle] = {}

    def _spawn_background_task(self, coro: Awaitable[Any]) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def start(self) -> None:
        await self.event_store.initialize()
        self.memory_store = MemoryStore(self.event_store._db, write_lock=self.event_store._write_lock)
        await self.memory_store.initialize()
        self.memory_gate = MemoryGate(self.memory_store, self.event_store)
        try:
            self.provider_registry.resolve(CognitiveTier.NORMAL)
            has_live_provider = True
        except LookupError:
            has_live_provider = False
        if self.mock_pi_handler is None and has_live_provider:
            # Production with a real provider: wire the LLM reflector (ADR-0019 §10.5).
            # Deterministic fallback remains only for explicit mock/test or keyless modes.
            from len_bot.memory.reflector import LLMReflector

            def _resolve_reflection_route():
                res = self.provider_registry.resolve(CognitiveTier.NORMAL)
                return res.client, res.model

            reflector = LLMReflector(resolver=_resolve_reflection_route)
            self.reflection_engine = ReflectionEngine(self.memory_store, self.memory_gate, llm_reflector=reflector)
        else:
            self.reflection_engine = ReflectionEngine(self.memory_store, self.memory_gate)
        self.runtime_gate.memory_gate = self.memory_gate
        self.episode_manager.memory_store = self.memory_store

        # Load dynamic configurations from database if present
        saved_persona = await self.event_store.get_dynamic_config("persona_config")
        if saved_persona:
            self.config.identity_name = saved_persona.get("identity_name", self.config.identity_name)
            self.config.identity_persona = saved_persona.get("identity_persona", self.config.identity_persona)
            self.config.bot_qq = saved_persona.get("bot_qq", self.config.bot_qq)
            self.bot_actor_id = f"user:{self.config.bot_qq}"
            self.action_queue.bot_actor_id = self.bot_actor_id
            self.scene_manager.bot_actor_id = self.bot_actor_id
            for actor in self.scene_manager._actors.values():
                actor.bot_actor_id = self.bot_actor_id

        # Provider Registry (ADR-0020): load persisted providers+routing; if absent,
        # ONE-TIME migrate the legacy single-provider model_config, then persist.
        saved_providers = await self.event_store.get_dynamic_config("provider_config")
        if saved_providers and saved_providers.get("routing"):
            providers = [ProviderConfig(**p) for p in saved_providers.get("providers", [])]
            routing = RoutingConfig(**saved_providers["routing"])
            await self.provider_registry.apply_update(providers, routing)
        else:
            legacy = await self.event_store.get_dynamic_config("model_config") or {}
            seed_provider = ProviderConfig(
                id="default",
                base_url=legacy.get("openai_base_url", self.config.openai_base_url),
                api_key=self.config.openai_api_key
            )
            seed_routing = RoutingConfig(
                normal=RouteTarget(provider_id="default", model=legacy.get("default_model", self.config.default_model)),
                deliberate=RouteTarget(provider_id="default", model=legacy.get("deliberate_model", self.config.deliberate_model)),
            )
            await self.provider_registry.apply_update([seed_provider], seed_routing)
            await self.event_store.save_dynamic_config("provider_config", self.provider_registry.export())
            logger.info("Migrated legacy model_config into provider_config (one-time, ADR-0020)")

        saved_social = await self.event_store.get_dynamic_config("social_config")
        if saved_social:
            self.config.monitored_keywords = saved_social.get("monitored_keywords", self.config.monitored_keywords)
            self.config.bot_cooldown_seconds = saved_social.get("bot_cooldown_seconds", self.config.bot_cooldown_seconds)
            if "speaking_budget_base_threshold" in saved_social:
                self.attention_engine.speaking_budget.base_threshold = saved_social["speaking_budget_base_threshold"]
            if "interest_topics" in saved_social:
                self.attention_engine.interest_model.topics = saved_social["interest_topics"]

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
        """Periodic background heartbeat for OpenLoop GC, ambient sweep and memory decay."""
        while self._running:
            try:
                await asyncio.sleep(max(0.05, self.config.maintenance_interval_seconds))
                if not self._running:
                    break

                # 1. Sweep expired Open Loops past absolute TTL
                await self.open_loop_manager.sweep_ttl_expiration()

                # 2. Check scene-level decay for all active scene actors
                for actor in list(self.scene_manager._actors.values()):
                    if actor.state:
                        await self.open_loop_manager.check_scene_decay(actor.state)

                # 3. Expire ambient retained items past TTL (ADR-0018)
                self.ambient_store.sweep()

                # 4. Temporal decay of stale epistemic beliefs (ADR-0015/0019)
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
        await self.stimulus_builder.ingest(event)

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
            state = self.scene_manager.get_scene_state(scene_id)
            if state and state.bot_engagement == "active":
                # Bot is mid-conversation; the block isn't complete. Postpone one window.
                self._schedule_quiet_window_reflection(state)
                return

            cursor_rowid = await self.memory_store.get_reflection_cursor(scene_id)
            events = await self.event_store.get_events_since(scene_id, after_rowid=cursor_rowid)
            if not events:
                return

            record = await self.reflection_engine.run_micro_reflection(scene_id, events)
            if record is not None:
                max_rowid = max(int(e.metadata.get("_rowid", 0)) for e in events)
                await self.memory_store.set_reflection_cursor(scene_id, max_rowid)
                logger.info("Reflection cursor on scene %s advanced to rowid %s", scene_id, max_rowid)
        except Exception as e:
            logger.warning("Quiet-window reflection failed on scene %s: %s", scene_id, e)

    async def _on_action_event(self, event: Event) -> None:
        """Called by ActionQueue on MESSAGE_SENT or MESSAGE_SEND_FAILED."""
        if event.event_type == EventType.MESSAGE_SENT:
            self.metrics.inc_social("visible_messages")
        await self.scene_manager.dispatch_event(event)

    async def _on_stimulus(self, stimulus: Stimulus) -> None:
        """Callback invoked when StimulusBuilder produces a Stimulus."""
        scene_state = self.scene_manager.get_scene_state(stimulus.scene_id)
        open_loops = await self.event_store.get_active_open_loops(stimulus.scene_id)

        if stimulus.stimulus_type in (StimulusType.SINGLE_MESSAGE, StimulusType.SOCIAL_MESSAGE_BURST):
            self.metrics.inc_social("human_messages")

        # 1. Attention Engine Evaluation
        att_res = self.attention_engine.evaluate(stimulus, scene_state, open_loops, now=stimulus.timestamp)
        self._last_attention_result = att_res
        self.metrics.inc_social(att_res.disposition.value.lower())  # observe / track / wake

        logger.info(
            "Attention on Scene %s: %s (reason: %s)",
            stimulus.scene_id,
            att_res.disposition.value,
            att_res.reason
        )

        if att_res.disposition == AttentionDisposition.DROP:
            self._spawn_background_task(self.event_store.save_trace(
                kind="attention", scene_id=stimulus.scene_id,
                ref_id=stimulus.id,
                payload={"stimulus_type": stimulus.stimulus_type.value, "actor_id": stimulus.actor_id,
                         "text": stimulus.combined_text[:120], "disposition": "drop", "reason": att_res.reason}
            ))
            return
        elif att_res.disposition == AttentionDisposition.OBSERVE:
            self._spawn_background_task(self.event_store.save_trace(
                kind="attention", scene_id=stimulus.scene_id,
                ref_id=stimulus.id,
                payload={"stimulus_type": stimulus.stimulus_type.value, "actor_id": stimulus.actor_id,
                         "text": stimulus.combined_text[:120], "disposition": "observe", "reason": att_res.reason}
            ))
            return
        elif att_res.disposition == AttentionDisposition.TRACK:
            if att_res.soft_annotation:
                annotation_event = Event(
                    event_type=EventType.STATE_ANNOTATION,
                    scene_id=stimulus.scene_id,
                    actor_id="system:attention",
                    timestamp=time.time(),
                    metadata={"soft_annotation": att_res.soft_annotation}
                )
                await self.scene_manager.dispatch_event(annotation_event)
            self._spawn_background_task(self.event_store.save_trace(
                kind="attention", scene_id=stimulus.scene_id,
                ref_id=stimulus.id,
                payload={"stimulus_type": stimulus.stimulus_type.value, "actor_id": stimulus.actor_id,
                         "text": stimulus.combined_text[:120], "disposition": "track", "reason": att_res.reason}
            ))
            return
        elif att_res.disposition == AttentionDisposition.WAKE:
            self._spawn_background_task(self.event_store.save_trace(
                kind="attention", scene_id=stimulus.scene_id,
                ref_id=stimulus.id,
                payload={"stimulus_type": stimulus.stimulus_type.value, "actor_id": stimulus.actor_id,
                         "text": stimulus.combined_text[:120], "disposition": "wake", "reason": att_res.reason}
            ))
            # 2. Trigger Cognitive Episode under concurrency semaphore as a background task
            # Scene Actor must NEVER block for LLM inference (ADR-0004)!
            async def _wake_coro():
                async with self._cognition_semaphore:
                    await self._run_wake_episode(stimulus, scene_state, open_loops, att_res)
            self._spawn_background_task(_wake_coro())

    async def _save_episode_trace(
        self,
        stimulus: Stimulus,
        episode_id: str,
        attention: AttentionResult,
        step_trace: dict,
        outcome,
        gate_decision: GateDecision
    ) -> None:
        """ADR-0022: one durable row = the full Event→Attention→Cognition→Gate→effects chain."""
        committed = gate_decision.committed_proposal
        payload = {
            "stimulus": {
                "id": stimulus.id,
                "type": stimulus.stimulus_type.value,
                "actor_id": stimulus.actor_id,
                "text": stimulus.combined_text[:200],
                "source_event_ids": stimulus.source_event_ids,
            },
            "attention": {"disposition": attention.disposition.value.lower(), "reason": attention.reason},
            "cognition": {
                "mode": step_trace.get("mode"),
                "steps": step_trace.get("steps", []),
                "interim_injections": step_trace.get("interim_injections", 0),
                "follow_ups": step_trace.get("follow_ups", 0),
                "escalations": step_trace.get("escalations", []),
            },
            "outcome": {
                "disposition": outcome.disposition.value,
                "thought": outcome.thought[:300],
                "messages": len(outcome.message_proposals),
                "tasks": len(outcome.task_proposals),
                "memories": len(outcome.memory_proposals),
                "resolve_loops": len(outcome.resolve_open_loop_ids),
                "retained_items": len(outcome.retained_item_proposals),
            },
            "gate": {"disposition": gate_decision.disposition.value, "reason": gate_decision.reason},
            "durable_effects": {
                "tasks": [t.id for t in committed.committed_tasks] if committed else [],
                "resolved_loops": committed.resolved_loop_ids if committed else [],
                "memories": [m.id for m in committed.committed_memories] if committed else [],
            },
            "actions_enqueued": gate_decision.actions_enqueued,
        }
        try:
            await self.event_store.save_trace(kind="episode", scene_id=stimulus.scene_id, ref_id=episode_id, payload=payload)
        except Exception as e:
            logger.warning("Failed saving episode trace on scene %s: %s", stimulus.scene_id, e)

    async def _run_wake_episode(self, stimulus: Stimulus, scene_state, open_loops, att_res: Optional[AttentionResult] = None) -> None:
        actor = await self.scene_manager.get_or_create_actor(stimulus.scene_id)
        if scene_state is None:
            scene_state = actor.state

        # P0.2: Enforce single-scene mutual exclusion and span mailbox through gate commit
        episode_id = f"ep_{uuid.uuid4().hex[:12]}"
        base_version = scene_state.version if scene_state else 0
        mailbox = EpisodeMailbox(episode_id, stimulus.scene_id, base_version)

        acquired = actor.acquire_episode_lease(episode_id, mailbox)
        if not acquired:
            logger.info("Scene %s already has an active cognitive episode; skipping concurrent trigger", stimulus.scene_id)
            return

        try:
            raw_events = await self.event_store.get_recent_events(stimulus.scene_id, limit=30)
            allowed_scopes = [stimulus.scene_id, "global-safe"]

            relevant_memories = []
            if self.memory_store:
                relevant_memories = await self.memory_store.query_memories(allowed_scopes=allowed_scopes, limit=5)

            # Person Card (ADR-0019 §11.1): sender snapshot from the actor's latest
            # event + subject-scoped memories for the current actor.
            actor_profile = None
            for e in reversed(raw_events):
                if e.actor_id == stimulus.actor_id and e.payload.get("sender"):
                    actor_profile = e.payload["sender"]
                    break
            actor_memories = []
            if self.memory_store:
                actor_memories = await self.memory_store.query_memories(
                    allowed_scopes=allowed_scopes, subject=stimulus.actor_id, limit=3
                )

            # Ambient retained items relevant to the current stimulus (ADR-0018, Goal 7)
            ambient_items = self.ambient_store.match(text=stimulus.combined_text, scope=stimulus.scene_id)

            outcome, step_trace, _mailbox = await self.episode_manager.run_episode(
                stimulus=stimulus,
                scene_state=scene_state,
                raw_events=raw_events,
                active_open_loops=open_loops,
                allowed_scopes=allowed_scopes,
                relevant_memories=relevant_memories,
                mailbox=mailbox,
                ambient_items=ambient_items,
                actor_profile=actor_profile,
                actor_memories=actor_memories
            )

            # Submit proposal through SceneActor single-writer serialization point (P0-2)
            gate_decision = await actor.submit_proposal(
                episode_id=episode_id,
                outcome=outcome,
                mailbox=mailbox,
                runtime_gate=self.runtime_gate
            )
            self._last_gate_decision = gate_decision
            if gate_decision.disposition == FinalDisposition.SILENCE:
                self.metrics.inc_social("wake_silence")
            else:
                self.metrics.inc_social("gate_action")
            if gate_decision.disposition == FinalDisposition.SILENCE and mailbox.is_cancelled():
                self.metrics.inc_social("cancellations_honored")
            logger.info("Gate decision on Scene %s: %s (%s)", stimulus.scene_id, gate_decision.disposition, gate_decision.reason)

            # ADR-0022: persist the full behavior chain for the Control Plane Trace view
            await self._save_episode_trace(
                stimulus=stimulus,
                episode_id=episode_id,
                attention=att_res,
                step_trace=step_trace,
                outcome=outcome,
                gate_decision=gate_decision
            )
        finally:
            actor.release_episode_lease(episode_id)
