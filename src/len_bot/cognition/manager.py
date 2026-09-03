import uuid
import logging
from typing import Any, Optional
from len_bot.events.models import Stimulus, Event
from len_bot.scenes.models import SceneState
from len_bot.scenes.manager import SceneManager
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.assembler import ContextAssembler
from len_bot.cognition.pi_core import PiAgentCore
from len_bot.cognition.models import EpisodeOutcome

from len_bot.tools.retrieval import RetrievalToolkit

logger = logging.getLogger(__name__)

class EpisodeManager:
    def __init__(
        self,
        scene_manager: SceneManager,
        context_assembler: ContextAssembler,
        pi_core: PiAgentCore,
        event_store: Optional[Any] = None,
        memory_store: Optional[Any] = None,
        plugin_host: Optional[Any] = None
    ):
        self.scene_manager = scene_manager
        self.context_assembler = context_assembler
        self.pi_core = pi_core
        self.event_store = event_store
        self.memory_store = memory_store
        self.plugin_host = plugin_host

    async def run_episode(
        self,
        stimulus: Stimulus,
        scene_state: SceneState,
        raw_events: list[Event],
        active_open_loops: list[dict[str, Any]],
        allowed_scopes: list[str],
        relevant_memories: Optional[list[Any]] = None,
        mailbox: Optional[EpisodeMailbox] = None,
        ambient_items: Optional[list[Any]] = None,
        actor_profile: Optional[dict[str, Any]] = None,
        actor_memories: Optional[list[Any]] = None
    ) -> tuple[EpisodeOutcome, dict[str, Any], EpisodeMailbox]:
        managed_internally = False
        actor = await self.scene_manager.get_or_create_actor(stimulus.scene_id)
        if mailbox is None:
            episode_id = f"ep_{uuid.uuid4().hex[:12]}"
            base_version = scene_state.version
            mailbox = EpisodeMailbox(episode_id, stimulus.scene_id, base_version)
            actor.acquire_episode_lease(episode_id, mailbox)
            managed_internally = True

        try:
            # 2. Assemble prompt package
            messages = self.context_assembler.assemble(
                stimulus=stimulus,
                scene_state=scene_state,
                raw_events=raw_events,
                active_open_loops=active_open_loops,
                allowed_scopes=allowed_scopes,
                relevant_memories=relevant_memories,
                ambient_items=ambient_items,
                actor_profile=actor_profile,
                actor_memories=actor_memories
            )

            # 3. Instantiate ambient RetrievalToolkit (ADR-0006 & ADR-0010 & ADR-0011)
            toolkit = None
            if self.event_store:
                toolkit = RetrievalToolkit(
                    event_store=self.event_store,
                    allowed_scopes=allowed_scopes,
                    default_scene_id=stimulus.scene_id,
                    memory_store=self.memory_store,
                    plugin_host=self.plugin_host
                )

            # 4. Execute Pi Agent Core with tools
            outcome, step_trace = await self.pi_core.execute_episode(messages, mailbox, toolkit=toolkit)
            return outcome, step_trace, mailbox
        finally:
            if managed_internally:
                actor.release_episode_lease(mailbox.episode_id)
