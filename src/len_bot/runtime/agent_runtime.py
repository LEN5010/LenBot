import asyncio
import logging
import time
import uuid
from typing import Optional, Callable, Awaitable, Any
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType, Stimulus
from len_bot.events.store import EventStore
from len_bot.events.builder import StimulusBuilder
from len_bot.scenes.manager import SceneManager
from len_bot.attention.engine import AttentionEngine
from len_bot.attention.models import AttentionDisposition, AttentionResult
from len_bot.cognition.assembler import ContextAssembler
from len_bot.cognition.mailbox import EpisodeMailbox
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

logger = logging.getLogger(__name__)

class AgentRuntime:
    def __init__(
        self,
        config: RuntimeConfig,
        send_adapter: Optional[Callable[[ActionItem], Awaitable[bool]]] = None,
        mock_pi_handler: Optional[Callable] = None
    ):
        self.config = config
        self.bot_actor_id = f"user:{config.bot_qq}"
        
        self.event_store = EventStore(config.db_path)
        self.memory_store: Optional[MemoryStore] = None
        self.memory_gate: Optional[MemoryGate] = None
        self.reflection_engine: Optional[ReflectionEngine] = None

        self.action_queue = ActionQueue(
            event_store=self.event_store,
            send_adapter=send_adapter,
            on_action_event=self._on_action_event,
            bot_actor_id=self.bot_actor_id
        )
        self.scheduler = TaskScheduler(
            event_store=self.event_store,
            emit_event=self.receive_event,
            sweep_interval=5.0
        )
        self.open_loop_manager = OpenLoopManager(self.event_store)
        self.runtime_gate = RuntimeGate(
            event_store=self.event_store,
            action_queue=self.action_queue,
            scheduler=self.scheduler
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
        self.pi_core = PiAgentCore(config, mock_handler=mock_pi_handler)
        self.episode_manager = EpisodeManager(
            scene_manager=self.scene_manager,
            context_assembler=self.context_assembler,
            pi_core=self.pi_core,
            event_store=self.event_store
        )

        self._cognition_semaphore = asyncio.Semaphore(2)  # Max 2 concurrent episodes (§93)
        self._last_attention_result: Optional[AttentionResult] = None
        self._last_gate_decision: Optional[GateDecision] = None
        self._started_at = time.time()
        self._onebot_adapter = None
        self._running = False
        self._maintenance_task: Optional[asyncio.Task] = None
        self._background_tasks: set[asyncio.Task] = set()

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

        saved_model = await self.event_store.get_dynamic_config("model_config")
        if saved_model:
            self.config.openai_base_url = saved_model.get("openai_base_url", self.config.openai_base_url)
            self.config.default_model = saved_model.get("default_model", self.config.default_model)
            self.config.deliberate_model = saved_model.get("deliberate_model", self.config.deliberate_model)

        saved_social = await self.event_store.get_dynamic_config("social_config")
        if saved_social:
            self.config.monitored_keywords = saved_social.get("monitored_keywords", self.config.monitored_keywords)
            self.config.bot_cooldown_seconds = saved_social.get("bot_cooldown_seconds", self.config.bot_cooldown_seconds)
            if "speaking_budget_base_threshold" in saved_social:
                self.attention_engine.speaking_budget.base_threshold = saved_social["speaking_budget_base_threshold"]
            if "interest_topics" in saved_social:
                self.attention_engine.interest_model.topics = saved_social["interest_topics"]

        self._running = True
        await self.action_queue.start()
        await self.scheduler.start()
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def stop(self) -> None:
        self._running = False
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
        await self.action_queue.stop()
        await self.event_store.close()

    async def _maintenance_loop(self) -> None:
        """Periodic background heartbeat for OpenLoop GC and social decay (§109)."""
        while self._running:
            try:
                await asyncio.sleep(60.0)
                if not self._running:
                    break

                # 1. Sweep expired Open Loops past absolute TTL
                await self.open_loop_manager.sweep_ttl_expiration()

                # 2. Check scene-level decay for all active scene actors
                for actor in list(self.scene_manager._actors.values()):
                    if actor.state:
                        await self.open_loop_manager.check_scene_decay(actor.state)

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

        # Micro-reflection trigger when conversation block completes (§58 & ADR-0011)
        if (
            self.reflection_engine
            and state
            and state.bot_engagement in ("observing", "idle")
            and getattr(state, "intervening_messages_since_bot", 0) == 5
        ):
            async def _reflect():
                try:
                    recent = await self.event_store.get_recent_events(state.scene_id, limit=20)
                    await self.reflection_engine.run_micro_reflection(state.scene_id, recent)
                except Exception as e:
                    logger.warning("Micro-reflection failed on scene %s: %s", state.scene_id, e)
            self._spawn_background_task(_reflect())

    async def _on_action_event(self, event: Event) -> None:
        """Called by ActionQueue on MESSAGE_SENT or MESSAGE_SEND_FAILED."""
        await self.scene_manager.dispatch_event(event)

    async def _on_stimulus(self, stimulus: Stimulus) -> None:
        """Callback invoked when StimulusBuilder produces a Stimulus."""
        scene_state = self.scene_manager.get_scene_state(stimulus.scene_id)
        open_loops = await self.event_store.get_active_open_loops(stimulus.scene_id)

        # 1. Attention Engine Evaluation
        att_res = self.attention_engine.evaluate(stimulus, scene_state, open_loops)
        self._last_attention_result = att_res

        logger.info(
            "Attention on Scene %s: %s (reason: %s)",
            stimulus.scene_id,
            att_res.disposition.value,
            att_res.reason
        )

        if att_res.disposition == AttentionDisposition.DROP:
            return
        elif att_res.disposition == AttentionDisposition.OBSERVE:
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
            return
        elif att_res.disposition == AttentionDisposition.WAKE:
            # 2. Trigger Cognitive Episode under concurrency semaphore as a background task
            # Scene Actor must NEVER block for LLM inference (ADR-0004)!
            async def _wake_coro():
                async with self._cognition_semaphore:
                    await self._run_wake_episode(stimulus, scene_state, open_loops)
            self._spawn_background_task(_wake_coro())

    async def _run_wake_episode(self, stimulus: Stimulus, scene_state, open_loops) -> None:
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

            outcome, _ = await self.episode_manager.run_episode(
                stimulus=stimulus,
                scene_state=scene_state,
                raw_events=raw_events,
                active_open_loops=open_loops,
                allowed_scopes=allowed_scopes,
                relevant_memories=relevant_memories,
                mailbox=mailbox
            )

            # Submit proposal through SceneActor single-writer serialization point (P0-2)
            gate_decision = await actor.submit_proposal(
                episode_id=episode_id,
                outcome=outcome,
                mailbox=mailbox,
                runtime_gate=self.runtime_gate
            )
            self._last_gate_decision = gate_decision
            logger.info("Gate decision on Scene %s: %s (%s)", stimulus.scene_id, gate_decision.disposition, gate_decision.reason)
        finally:
            actor.release_episode_lease(episode_id)
