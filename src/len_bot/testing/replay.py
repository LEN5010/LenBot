"""Transcript replay through the production actor, core, gate and shadow queue."""
import asyncio
import tempfile
import copy
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal
from len_bot.actions.models import DeliveryResult, DeliveryStatus
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.cognition.social_core import SocialCognitionCore


@dataclass
class ReplayInjection:
    checkpoint: Literal["before_model", "after_model", "before_tool", "after_tool", "before_send", "after_send"]
    events: list[Event] = field(default_factory=list)
    occurrence: int = 1


class ReplayFailure(RuntimeError):
    pass


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
        await runtime.scheduler.run_due(runtime.clock())
        job_tasks = list(runtime.job_runner._tasks.values())
        if job_tasks:
            await asyncio.gather(*job_tasks)
        await runtime.action_queue._queue.join()
        for actor in list(runtime.scene_manager._actors.values()):
            await actor._queue.join()
        await asyncio.sleep(0)  # deliver task-done callbacks, not a wall-clock delay
        if not runtime._social_tasks and not runtime._social_pending and not runtime.burst_assembler._buffers and not runtime.job_runner._tasks:
            return
    raise RuntimeError("Replay did not quiesce")


class ReplayLab:
    def __init__(self, config: RuntimeConfig, social_core, tool_mode="mock", tool_results=None, voice_examples=None,
                 delivery_mode="shadow", injections=None, strict=False, max_model_calls=200):
        if tool_mode not in ("mock", "real"):
            raise ValueError("tool_mode must be mock or real")
        self.config, self.social_core = config, social_core
        self.tool_mode = tool_mode
        self.tool_results = tool_results or {}
        self.voice_examples = voice_examples or []
        if delivery_mode not in {"shadow", "simulated"}:
            raise ValueError("Replay delivery must be shadow or simulated")
        self.delivery_mode, self.injections, self.strict = delivery_mode, injections or [], strict
        self.max_model_calls = max_model_calls
        self.model_mode = "mock" if social_core.mock_handler or getattr(social_core.registry, "is_simulated", False) else "real"
        self.last_checkpoints, self.last_deliveries, self.last_observations = [], [], []
        self.last_run = {}
        self.last_metrics = {}
        self.last_tasks = []
        self.last_traces = []
        self.last_sessions = {}
        self.last_memories = []
        self.seed_memory_ids = []

    async def run(self, events: list[Event], until: float | None = None, *,
                  history: list[Event] | None = None, memories: list[dict] | None = None) -> list[dict]:
        ordered = sorted(events, key=lambda e: (e.timestamp, e.metadata.get("_rowid", 0)))
        history = sorted(history or [], key=lambda e: (e.timestamp, e.metadata.get("_rowid", 0)))
        if history and (not ordered or history[-1].timestamp > ordered[0].timestamp):
            raise ValueError("Historical context must precede the evaluation cutoff")
        if {e.id for e in history} & {e.id for e in ordered}:
            raise ValueError("History and evaluated events must be disjoint")
        clock_value = ordered[0].timestamp if ordered else 0.0
        self.last_checkpoints, self.last_deliveries, self.last_observations = [], [], []
        counts, fired = Counter(), set()
        injected_ids = [event.id for injection in self.injections for event in injection.events]
        if len(set(injected_ids)) != len(injected_ids) or set(injected_ids) & {e.id for e in [*ordered, *history]}:
            raise ValueError("Injection event IDs must be unique and disjoint")
        if any(injection.occurrence < 1 for injection in self.injections):
            raise ValueError("Injection occurrence must be positive")
        with tempfile.TemporaryDirectory(prefix="lenbot-replay-") as directory:
            config = self.config.model_copy(update={
                "db_path": directory + "/replay.db", "max_ingest_lag_seconds": float("inf"),
                "reflection_quiet_window_seconds": 1e9, "debounce_idle_ms": 1_000_000,
                "message_pacing": False,
            })
            async def forbidden_send(action):
                raise AssertionError("Replay attempted physical delivery")
            sent_count = 0
            async def simulated_send(action):
                nonlocal sent_count
                sent_count += 1
                return DeliveryResult(status=DeliveryStatus.SENT, transport="replay_simulated", message_id=f"sim-{sent_count}")
            runtime = AgentRuntime(config, send_adapter=simulated_send if self.delivery_mode == "simulated" else forbidden_send,
                                   mock_social_handler=self.social_core.mock_handler,
                                   clock=lambda: clock_value)
            await runtime.start()
            try:
                await runtime.set_shadow_mode(self.delivery_mode == "shadow")
                runtime.action_queue.simulated = self.delivery_mode == "simulated"
                async def checkpoint(name, detail):
                    nonlocal clock_value
                    counts[name] += 1
                    self.last_checkpoints.append({"checkpoint": name, "occurrence": counts[name], "time": clock_value, **copy.deepcopy(detail)})
                    if name == "before_model" and counts[name] > self.max_model_calls:
                        raise ReplayFailure("Evaluation model-call budget exhausted")
                    for index, injection in enumerate(self.injections):
                        if index in fired or injection.checkpoint != name or injection.occurrence != counts[name]:
                            continue
                        fired.add(index)
                        for source in injection.events:
                            event = source.model_copy(deep=True)
                            clock_value = max(clock_value, event.timestamp)
                            event.metadata["replay_injected"] = True
                            event.metadata["_replay_checkpoint"] = name
                            event.metadata.pop("_rowid", None)
                            await runtime.receive_event(event)
                            actor = await runtime.scene_manager.get_or_create_actor(event.scene_id)
                            await actor._queue.join()
                runtime.evaluation_hook = checkpoint
                runtime.action_queue.checkpoint = checkpoint
                for example in self.voice_examples:
                    await runtime.event_store.add_voice_example(
                        scene_id=example["scene_id"], content=example["content"],
                        context=example["context"], tag=example["tag"],
                    )
                await runtime.scheduler.stop()
                runtime.provider_registry = self.social_core.registry
                runtime.social_core = SocialCognitionCore(
                    config, self.social_core.registry, metrics=runtime.metrics,
                    mock_handler=self.social_core.mock_handler,
                )
                runtime.social_core.checkpoint = checkpoint
                if self.tool_mode == "mock":
                    async def mock_tool(name, arguments):
                        result = self.tool_results.get(name)
                        if result is None:
                            from len_bot.tools.results import ToolResult
                            return str(ToolResult.failure("回放未提供这个工具的结果，事实未知。", "fixture_missing"))
                        if isinstance(result, Exception):
                            raise result
                        return result
                    runtime.plugin_host.execute_tool = mock_tool
                # Historical bot speech is input only, never a generated delivery
                # or a response to this version. No cognition/metrics for the seed.
                for source in history:
                    actor = await runtime.scene_manager.get_or_create_actor(source.scene_id)
                    callback = actor.on_state_updated
                    actor.on_state_updated = None
                    try:
                        historical = source.model_copy(deep=True)
                        historical.metadata.pop("_rowid", None)
                        historical.metadata["replay_historical"] = True
                        actor.post_event(historical)
                        await actor._queue.join()
                    finally:
                        actor.on_state_updated = callback
                from len_bot.memory.models import MemoryItem
                from len_bot.cognition.session import SocialMemoryCandidate
                from len_bot.cognition.mailbox import EpisodeMailbox
                from len_bot.testing.social import social_result
                known_history = {e.id: e for e in history}
                seeds = {}
                for data in memories or []:
                    item = MemoryItem.model_validate(data)
                    if (item.created_at > clock_value or item.last_confirmed_at > clock_value
                            or not item.evidence or any(eid not in known_history or
                                known_history[eid].scene_id != item.scope for eid in item.evidence)):
                        raise ValueError("Seed memory must contain only same-scene pre-cutoff evidence")
                    seeds.setdefault(item.scope, []).append(SocialMemoryCandidate(**{
                        key: item.model_dump(mode="json")[key] for key in
                        ("subject", "kind", "key", "value", "temporal", "certainty", "evidence", "human_readable_assertion")}))
                # Materialize prior memory proposals through the selected version's
                # commit path, including its working references/receipts. Merely
                # inserting L2 rows loses that context and invalidates comparisons.
                self.seed_memory_ids = []
                for scope, candidates in seeds.items():
                    actor = await runtime.scene_manager.get_or_create_actor(scope)
                    seed = social_result(reason="回放前文中的历史认识，不是本次模型输出")
                    seed.memory_candidates = candidates
                    evidence = list(dict.fromkeys(eid for m in candidates for eid in m.evidence))
                    mailbox = EpisodeMailbox("replay_history_seed", scope, actor.state.version)
                    actor.acquire_episode_lease(mailbox.episode_id, mailbox)
                    try:
                        decision = await actor.commit_cognitive_turn(seed, actor.group_session.last_observed_event_rowid,
                            evidence, "replay_history_seed", mailbox.episode_id, mailbox, runtime.runtime_gate,
                            social_revision=actor.group_session.social_revision)
                        if not decision or not decision.accepted:
                            raise ValueError("Historical memory proposals could not be restored")
                        self.seed_memory_ids.extend(m.id for m in decision.committed_proposal.committed_memories)
                    finally:
                        actor.release_episode_lease(mailbox.episode_id)
                async def advance(target):
                    nonlocal clock_value
                    target = max(clock_value, target)
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
                    event.metadata["replay_input"] = True
                    if event.event_type == EventType.MESSAGE_SENT:
                        if self.delivery_mode == "simulated":
                            continue
                        event.metadata["replay_historical"] = True
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
                self.last_sessions = {scene: actor.group_session.model_dump(mode="json")
                                      for scene, actor in runtime.scene_manager._actors.items()}
                self.last_memories = [item.model_dump(mode="json") for item in
                    await runtime.memory_store.query_memories(list(self.last_sessions), include_superseded=True, limit=1000)]
                self.last_deliveries = [event.model_dump(mode="json") for scene in self.last_sessions
                    for event in await runtime.event_store.get_recent_events(scene, limit=12000)
                    if event.event_type in {EventType.MESSAGE_SENT, EventType.MESSAGE_SEND_FAILED, EventType.ACTION_SHADOWED}
                    and not event.metadata.get("replay_historical")]
                self.last_observations = [item for scene in self.last_sessions
                    for item in await runtime.event_store.list_tool_observations(scene, 500)]
                failures = [trace for trace in self.last_traces if trace["kind"] in {"social_cognition_error", "agent_job_error"}]
                try:
                    vision_configured = bool(runtime.provider_registry.resolve_vision())
                except (LookupError, AttributeError):
                    vision_configured = False
                self.last_run = {"model_mode": self.model_mode,
                    "tool_mode": self.tool_mode, "delivery_mode": self.delivery_mode,
                    "completed": not failures and len(fired) == len(self.injections),
                    "unfired_injections": [i for i in range(len(self.injections)) if i not in fired],
                    "checkpoint_counts": dict(counts), "human_response_to_candidate": None,
                    "failure_count": len(failures), "capabilities": ["tools", "interleaving"]
                        + (["jobs"] if getattr(runtime, "job_runner", None) else [])
                        + (["media"] if getattr(runtime, "media_service", None) else [])
                        + (["vision"] if vision_configured else [])}
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
                if self.strict and not self.last_run["completed"]:
                    raise ReplayFailure("Replay incomplete; failures and checkpoints preserved")
                return rows
            except Exception as error:
                self.last_traces = await runtime.event_store.query_traces(limit=10000)
                self.last_run.update({"completed": False, "error": str(error),
                    "model_mode": self.model_mode,
                    "tool_mode": self.tool_mode, "delivery_mode": self.delivery_mode})
                raise
            finally:
                await runtime.stop()
