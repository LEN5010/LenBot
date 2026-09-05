"""Transcript replay through the production actor, core, gate and shadow queue."""
import asyncio
import tempfile
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.cognition.social_core import SocialCognitionCore


async def drain(runtime):
    """Quiesce actor/cognition/action work, without waiting for future timers."""
    for _ in range(20):
        for actor in list(runtime.scene_manager._actors.values()):
            await actor._queue.join()
        for scene_id in list(runtime.burst_assembler._buffers):
            await runtime.burst_assembler.flush_scene(scene_id)
        running = list(runtime._social_tasks.values())
        if running:
            await asyncio.gather(*running)
        await runtime.action_queue._queue.join()
        for actor in list(runtime.scene_manager._actors.values()):
            await actor._queue.join()
        await asyncio.sleep(0)  # deliver task-done callbacks, not a wall-clock delay
        if not runtime._social_tasks and not runtime._social_pending and not runtime.burst_assembler._buffers:
            return
    raise RuntimeError("Replay did not quiesce")


class ReplayLab:
    def __init__(self, config: RuntimeConfig, social_core, tool_mode="mock", tool_results=None):
        if tool_mode not in ("mock", "real"):
            raise ValueError("tool_mode must be mock or real")
        self.config, self.social_core = config, social_core
        self.tool_mode = tool_mode
        self.tool_results = tool_results or {}
        self.last_metrics = {}
        self.last_tasks = []
        self.last_traces = []

    async def run(self, events: list[Event], until: float | None = None) -> list[dict]:
        ordered = sorted(events, key=lambda e: (e.timestamp, e.metadata.get("_rowid", 0)))
        clock_value = ordered[0].timestamp if ordered else 0.0
        with tempfile.TemporaryDirectory(prefix="lenbot-replay-") as directory:
            config = self.config.model_copy(update={
                "db_path": directory + "/replay.db", "max_ingest_lag_seconds": float("inf"),
                "reflection_quiet_window_seconds": 1e9, "debounce_idle_ms": 1_000_000,
            })
            async def forbidden_send(action):
                raise AssertionError("Replay attempted physical delivery")
            runtime = AgentRuntime(config, send_adapter=forbidden_send,
                                   mock_social_handler=self.social_core.mock_handler,
                                   clock=lambda: clock_value)
            await runtime.start()
            try:
                await runtime.set_shadow_mode(True)
                await runtime.scheduler.stop()
                runtime.provider_registry = self.social_core.registry
                runtime.social_core = SocialCognitionCore(
                    config, self.social_core.registry, metrics=runtime.metrics,
                    mock_handler=self.social_core.mock_handler,
                )
                if self.tool_mode == "mock":
                    async def mock_tool(name, arguments):
                        return self.tool_results.get(name, "回放未提供这个工具的结果，请明确说明未知。")
                    runtime.plugin_host.execute_tool = mock_tool
                async def advance(target):
                    nonlocal clock_value
                    for _ in range(100):
                        tasks = await runtime.event_store.get_pending_tasks(max_due_at=target)
                        if not tasks:
                            clock_value = target
                            return
                        clock_value = max(clock_value, tasks[0]["due_at"])
                        await runtime.scheduler.run_due(clock_value)
                        await drain(runtime)
                    raise RuntimeError("Replay exceeded scheduled wake ceiling")

                for index, source in enumerate(ordered):
                    if source.event_type not in {
                        EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED,
                        EventType.MESSAGE_SENT, EventType.LIVE_STARTED, EventType.LIVE_ENDED,
                        EventType.USER_JOINED,
                    }:
                        continue
                    await advance(source.timestamp)
                    event = source.model_copy(deep=True)
                    event.metadata.pop("_rowid", None)
                    await runtime.receive_event(event)
                    actor = await runtime.scene_manager.get_or_create_actor(event.scene_id)
                    await actor._queue.join()
                    next_time = ordered[index + 1].timestamp if index + 1 < len(ordered) else float("inf")
                    if next_time - event.timestamp >= self.config.debounce_idle_ms / 1000 or event.is_mention_bot or event.is_reply_bot:
                        await drain(runtime)
                await drain(runtime)
                if until is not None:
                    await advance(until)
                    await drain(runtime)
                self.last_metrics = runtime.metrics.snapshot()
                self.last_traces = await runtime.event_store.query_traces(limit=10000)
                self.last_tasks = [task for scene in runtime.scene_manager._actors
                                   for task in await runtime.event_store.scene_tasks(scene)]
                rows = []
                for entry in reversed(self.last_traces):
                    p = entry["payload"]
                    if entry["kind"] != "social_cognition":
                        continue
                    result = p["result"]
                    rows.append({
                        "event_id": p["burst"]["source_event_ids"][-1],
                        "timestamp": entry["created_at"], "actor_id": p["burst"]["actor_id"],
                        "text": p["burst"]["text"], "decision": result["decision"]["action"],
                        "reason": result["decision"]["reason"], "understanding": result["perception"]["summary"],
                        "would_send": [m["content"] for m in result["message_proposals"]]
                                      if p["gate"]["accepted"] else [],
                        "trace": p,
                    })
                return rows
            finally:
                await runtime.stop()
