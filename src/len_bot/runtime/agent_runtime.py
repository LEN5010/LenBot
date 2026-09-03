import asyncio
import logging
from typing import Optional, Callable, Awaitable
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType, Stimulus
from len_bot.events.store import EventStore
from len_bot.events.builder import StimulusBuilder
from len_bot.scenes.manager import SceneManager
from len_bot.attention.engine import AttentionEngine
from len_bot.attention.models import AttentionDisposition, AttentionResult
from len_bot.cognition.assembler import ContextAssembler
from len_bot.cognition.pi_core import PiAgentCore
from len_bot.cognition.manager import EpisodeManager
from len_bot.actions.models import ActionItem
from len_bot.actions.queue import ActionQueue
from len_bot.runtime.gate import RuntimeGate, GateDecision
from len_bot.scheduler.engine import TaskScheduler
from len_bot.state.open_loops import OpenLoopManager

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
        self.action_queue = ActionQueue(
            event_store=self.event_store,
            send_adapter=send_adapter,
            on_action_event=self._on_action_event
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
            event_store=self.event_store
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

    async def start(self) -> None:
        await self.event_store.initialize()
        await self.action_queue.start()
        await self.scheduler.start()

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.scene_manager.stop()
        await self.action_queue.stop()
        await self.event_store.close()

    async def receive_event(self, event: Event) -> None:
        """Entrypoint for all inbound events from adapters, webhooks, or sensors."""
        # 1. Append immutable event to EventStore
        await self.event_store.append_event(event)
        
        # 2. Dispatch to single-writer Scene Actor
        await self.scene_manager.dispatch_event(event)

        # 3. Feed to Stimulus Builder (sliding debounce)
        await self.stimulus_builder.ingest(event)

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
            if att_res.soft_annotation and scene_state:
                scene_state.soft_annotations.update(att_res.soft_annotation)
            return
        elif att_res.disposition == AttentionDisposition.WAKE:
            # 2. Trigger Cognitive Episode under concurrency semaphore
            async with self._cognition_semaphore:
                await self._run_wake_episode(stimulus, scene_state, open_loops)

    async def _run_wake_episode(self, stimulus: Stimulus, scene_state, open_loops) -> None:
        if scene_state is None:
            actor = await self.scene_manager.get_or_create_actor(stimulus.scene_id)
            scene_state = actor.state

        raw_events = await self.event_store.get_recent_events(stimulus.scene_id, limit=30)
        allowed_scopes = [stimulus.scene_id, "global-safe"]

        outcome, mailbox = await self.episode_manager.run_episode(
            stimulus=stimulus,
            scene_state=scene_state,
            raw_events=raw_events,
            active_open_loops=open_loops,
            allowed_scopes=allowed_scopes
        )

        gate_decision = await self.runtime_gate.evaluate_and_commit(
            outcome=outcome,
            mailbox=mailbox,
            current_scene_state=scene_state
        )
        self._last_gate_decision = gate_decision
        logger.info("Gate decision on Scene %s: %s (%s)", stimulus.scene_id, gate_decision.disposition, gate_decision.reason)
