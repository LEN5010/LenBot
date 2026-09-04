import asyncio
from typing import Optional, Callable, Awaitable
from len_bot.events.models import Event
from len_bot.scenes.models import SceneState
from len_bot.scenes.actor import SceneActor
from len_bot.events.store import EventStore
from len_bot.cognition.session import GroupAgentSession

class SceneManager:
    def __init__(
        self,
        bot_actor_id: str,
        event_store: EventStore,
        on_state_updated: Optional[Callable[[SceneState, Event], Awaitable[None]]] = None
    ):
        self.bot_actor_id = bot_actor_id
        self.event_store = event_store
        self.on_state_updated = on_state_updated
        self._actors: dict[str, SceneActor] = {}
        self._lock = asyncio.Lock()

    async def get_or_create_actor(self, scene_id: str) -> SceneActor:
        async with self._lock:
            actor = self._actors.get(scene_id)
            if actor is None:
                actor = SceneActor(
                    scene_id=scene_id,
                    bot_actor_id=self.bot_actor_id,
                    event_store=self.event_store,
                    on_state_updated=self.on_state_updated
                )
                await actor.start()
                self._actors[scene_id] = actor
            return actor

    async def dispatch_event(self, event: Event) -> None:
        actor = await self.get_or_create_actor(event.scene_id)
        actor.post_event(event)

    def get_scene_state(self, scene_id: str) -> Optional[SceneState]:
        actor = self._actors.get(scene_id)
        return actor.state if actor else None

    def get_group_session(self, scene_id: str) -> Optional[GroupAgentSession]:
        actor = self._actors.get(scene_id)
        return actor.group_session if actor else None

    def has_active_episode(self, scene_id: str) -> bool:
        actor = self._actors.get(scene_id)
        return actor.has_active_episode() if actor else False

    async def stop(self) -> None:
        async with self._lock:
            for actor in self._actors.values():
                await actor.stop()
            self._actors.clear()
