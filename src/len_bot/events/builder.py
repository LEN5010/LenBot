import asyncio
import time
from collections.abc import Awaitable, Callable

from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType, Stimulus, StimulusType


class BurstBuffer:
    def __init__(self, scene_id: str, first_arrived_at: float):
        self.scene_id = scene_id
        self.events: list[Event] = []
        self.first_arrived_at = first_arrived_at
        self.timer_task: asyncio.Task[None] | None = None


class BurstAssembler:
    """Coalesce adjacent scene events without making social judgements."""

    _CHAT_EVENT_TYPES = {
        EventType.GROUP_MESSAGE_RECEIVED,
        EventType.PRIVATE_MESSAGE_RECEIVED,
    }
    _IMMEDIATE_EVENT_TYPES = {
        EventType.TASK_DUE,
        EventType.TASK_REVIEW,
        EventType.REFLECTION_RECORDED,
        EventType.LIVE_STARTED,
        EventType.LIVE_ENDED,
        EventType.TOOL_COMPLETED,
        EventType.AGENT_JOB_FINISHED,
        EventType.AGENT_JOB_PROGRESS,
        EventType.USER_JOINED,
        EventType.MESSAGE_SEND_FAILED,
    }

    def __init__(
        self,
        config: RuntimeConfig,
        on_burst: Callable[[Stimulus], Awaitable[None]],
        clock=time.time,
    ):
        self.clock = clock
        self.config = config
        self.on_burst = on_burst
        self._buffers: dict[str, BurstBuffer] = {}
        self._lock = asyncio.Lock()

    async def ingest(self, event: Event) -> None:
        now = self.clock()
        if event.metadata.get("obsolete_task_wake"):
            return
        if event.metadata.get("obsolete_job_result"):
            return
        if event.event_type == EventType.REFLECTION_RECORDED and not event.metadata.get('needs_review'):
            return

        bursts: list[Stimulus] = []
        async with self._lock:
            if event.event_type in self._IMMEDIATE_EVENT_TYPES:
                pending = self._take_buffer(event.scene_id)
                if pending:
                    bursts.append(self._create_burst(pending.events))
                bursts.append(self._create_immediate_burst(event))
            elif event.event_type in self._CHAT_EVENT_TYPES:
                if event.actor_id == f"user:{self.config.bot_qq}":
                    return
                buffer = self._buffers.get(event.scene_id)
                if buffer is None:
                    buffer = BurstBuffer(event.scene_id, now)
                    self._buffers[event.scene_id] = buffer
                buffer.events.append(event)

                if event.is_mention_bot or event.is_reply_bot:
                    self._take_buffer(event.scene_id)
                    bursts.append(self._create_burst(buffer.events))
                elif (now - buffer.first_arrived_at) * 1000 >= self.config.debounce_max_ms:
                    self._take_buffer(event.scene_id)
                    bursts.append(self._create_burst(buffer.events))
                else:
                    if buffer.timer_task:
                        buffer.timer_task.cancel()
                    delay = self.config.debounce_idle_ms / 1000.0
                    buffer.timer_task = asyncio.create_task(
                        self._wait_and_flush(event.scene_id, delay)
                    )
            else:
                return

        for burst in bursts:
            await self.on_burst(burst)

    async def flush_scene(self, scene_id: str) -> None:
        burst: Stimulus | None = None
        async with self._lock:
            buffer = self._take_buffer(scene_id)
            if buffer:
                burst = self._create_burst(buffer.events)
        if burst:
            await self.on_burst(burst)

    async def close(self) -> None:
        async with self._lock:
            for buffer in self._buffers.values():
                if buffer.timer_task:
                    buffer.timer_task.cancel()
            self._buffers.clear()

    async def _wait_and_flush(self, scene_id: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            burst: Stimulus | None = None
            async with self._lock:
                buffer = self._take_buffer(scene_id)
                if buffer:
                    burst = self._create_burst(buffer.events)
            if burst:
                await self.on_burst(burst)
        except asyncio.CancelledError:
            return

    def _take_buffer(self, scene_id: str) -> BurstBuffer | None:
        buffer = self._buffers.pop(scene_id, None)
        if (
            buffer
            and buffer.timer_task
            and buffer.timer_task is not asyncio.current_task()
        ):
            buffer.timer_task.cancel()
        return buffer

    @staticmethod
    def _create_burst(events: list[Event]) -> Stimulus:
        actor_ids = {event.actor_id for event in events}
        if len(actor_ids) > 1:
            combined = "\n".join(
                f"{event.actor_id}: {event.raw_text}"
                for event in events
                if event.raw_text
            )
        else:
            combined = "\n".join(event.raw_text for event in events if event.raw_text)

        return Stimulus(
            scene_id=events[0].scene_id,
            stimulus_type=(
                StimulusType.SOCIAL_MESSAGE_BURST
                if len(events) > 1
                else StimulusType.SINGLE_MESSAGE
            ),
            source_event_ids=[event.id for event in events],
            actor_id=events[-1].actor_id,
            combined_text=combined,
            has_mention_bot=any(event.is_mention_bot for event in events),
            has_reply_bot=any(event.is_reply_bot for event in events),
            timestamp=events[-1].timestamp,
            origin_mode="shadow" if any(e.metadata.get("delivery_origin")=="shadow" for e in events) else "live",
            events=events,
        )

    @staticmethod
    def _create_immediate_burst(event: Event) -> Stimulus:
        burst_type = (
            StimulusType.PROACTIVE_TASK
            if event.event_type == EventType.TASK_DUE
            else StimulusType.PLUGIN_FACT
        )
        return Stimulus(
            scene_id=event.scene_id,
            stimulus_type=burst_type,
            source_event_ids=[event.id],
            actor_id=event.actor_id,
            combined_text=event.raw_text,
            origin_mode="shadow" if event.metadata.get("delivery_origin")=="shadow" else event.payload.get("origin_mode", "live"),
            timestamp=event.timestamp,
            events=[event],
        )
