import asyncio
from len_bot.events.models import Event
from len_bot.scenes.actor import SceneActor


class SceneManager:
    def __init__(self, bot_actor_id, event_store, on_state_updated=None, attention_policy=None):
        self.bot_actor_id = bot_actor_id
        self.event_store = event_store
        self.on_state_updated = on_state_updated
        self.attention_policy = attention_policy
        self._actors = {}
        self._lock = asyncio.Lock()

    async def get_or_create_actor(self, scene_id):
        async with self._lock:
            if scene_id not in self._actors:
                actor = SceneActor(scene_id, self.bot_actor_id, self.event_store, self.on_state_updated,
                                   attention_policy=self.attention_policy)
                await actor.start()
                self._actors[scene_id] = actor
            return self._actors[scene_id]

    async def dispatch_event(self, event: Event):
        (await self.get_or_create_actor(event.scene_id)).post_event(event)

    def get_session(self, scene_id):
        actor = self._actors.get(scene_id)
        return actor.session if actor else None

    def has_active_episode(self, scene_id):
        actor = self._actors.get(scene_id)
        return bool(actor and actor.has_active_episode())

    async def stop(self):
        async with self._lock:
            for actor in self._actors.values():
                await actor.stop()
            self._actors.clear()
