import asyncio
import time
from typing import Callable, Optional, Awaitable
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.config import RuntimeConfig

class BurstBuffer:
    def __init__(self, actor_id: str, scene_id: str):
        self.actor_id = actor_id
        self.scene_id = scene_id
        self.events: list[Event] = []
        self.first_event_at: float = time.time()
        self.last_event_at: float = time.time()
        self.timer_task: Optional[asyncio.Task] = None

class StimulusBuilder:
    def __init__(
        self,
        config: RuntimeConfig,
        on_stimulus: Callable[[Stimulus], Awaitable[None]]
    ):
        self.config = config
        self.on_stimulus = on_stimulus
        # Key: (scene_id, actor_id) -> BurstBuffer
        self._buffers: dict[tuple[str, str], BurstBuffer] = {}
        self._lock = asyncio.Lock()

    def _is_urgent(self, event: Event) -> bool:
        if event.is_mention_bot or event.is_reply_bot:
            return True
        urgent_keywords = ["不用了", "不用查了", "取消", "别查了", "闭嘴"]
        return any(k in event.raw_text for k in urgent_keywords)

    async def ingest(self, event: Event) -> None:
        now = time.time()

        # 1. Historical Ingestion Gate: suppress stimulus for stale/historical events
        if (now - event.timestamp) > self.config.max_ingest_lag_seconds:
            return

        # 2. Non-chat events (TASK_DUE, etc.) flush immediately as dedicated stimulus
        if event.event_type == EventType.TASK_DUE:
            stimulus = Stimulus(
                scene_id=event.scene_id,
                stimulus_type=StimulusType.PROACTIVE_TASK,
                source_event_ids=[event.id],
                actor_id=event.actor_id,
                combined_text=event.raw_text,
                origin_mode=event.payload.get("origin_mode", "live"),
                timestamp=event.timestamp
            )
            await self.on_stimulus(stimulus)
            return

        # 2b. Plugin fact events (ADR-0018): flush immediately, never debounced.
        # Attention treats PLUGIN_FACT as plain OBSERVE — facts alone never wake cognition.
        if event.event_type in (EventType.LIVE_STARTED, EventType.LIVE_ENDED):
            stimulus = Stimulus(
                scene_id=event.scene_id,
                stimulus_type=StimulusType.PLUGIN_FACT,
                source_event_ids=[event.id],
                actor_id=event.actor_id,
                combined_text=event.raw_text,
                timestamp=event.timestamp
            )
            await self.on_stimulus(stimulus)
            return

        if event.event_type not in (EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED):
            return

        # Do not ingest Bot's own messages (prevent echo loops from OneBot adapter)
        if event.actor_id == f"user:{self.config.bot_qq}":
            return

        key = (event.scene_id, event.actor_id)
        
        stimulus_to_dispatch = None
        async with self._lock:
            # 3. Check Immediate Flush Triggers
            if self._is_urgent(event):
                # Flush existing buffer for this actor if any, plus current event
                buffer = self._buffers.pop(key, None)
                events = (buffer.events if buffer else []) + [event]
                if buffer and buffer.timer_task:
                    buffer.timer_task.cancel()
                stimulus_to_dispatch = self._create_stimulus(event.scene_id, event.actor_id, events)
            else:
                # 4. Debounce Sliding Idle Window
                buffer = self._buffers.get(key)
                if buffer is None:
                    buffer = BurstBuffer(event.actor_id, event.scene_id)
                    self._buffers[key] = buffer

                buffer.events.append(event)
                buffer.last_event_at = now

                # If reached max wait cap, flush now
                if (now - buffer.first_event_at) * 1000 >= self.config.debounce_max_ms:
                    self._buffers.pop(key, None)
                    if buffer.timer_task:
                        buffer.timer_task.cancel()
                    stimulus_to_dispatch = self._create_stimulus(event.scene_id, event.actor_id, buffer.events)
                else:
                    # Otherwise reset idle timer
                    if buffer.timer_task:
                        buffer.timer_task.cancel()

                    idle_seconds = self.config.debounce_idle_ms / 1000.0
                    buffer.timer_task = asyncio.create_task(self._wait_and_flush(key, idle_seconds))

        if stimulus_to_dispatch:
            await self.on_stimulus(stimulus_to_dispatch)

    async def _wait_and_flush(self, key: tuple[str, str], delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            stimulus_to_dispatch = None
            async with self._lock:
                buffer = self._buffers.pop(key, None)
                if buffer and buffer.events:
                    stimulus_to_dispatch = self._create_stimulus(buffer.scene_id, buffer.actor_id, buffer.events)
            if stimulus_to_dispatch:
                await self.on_stimulus(stimulus_to_dispatch)
        except asyncio.CancelledError:
            pass

    def _create_stimulus(self, scene_id: str, actor_id: str, events: list[Event]) -> Stimulus:
        combined = "\n".join(e.raw_text for e in events if e.raw_text)
        has_at = any(e.is_mention_bot for e in events)
        has_reply = any(e.is_reply_bot for e in events)
        stype = StimulusType.SOCIAL_MESSAGE_BURST if len(events) > 1 else StimulusType.SINGLE_MESSAGE
        return Stimulus(
            scene_id=scene_id,
            stimulus_type=stype,
            source_event_ids=[e.id for e in events],
            actor_id=actor_id,
            combined_text=combined,
            has_mention_bot=has_at,
            has_reply_bot=has_reply,
            timestamp=events[-1].timestamp if events else time.time()
        )
