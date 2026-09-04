import pytest
import asyncio
import time
from typing import Any, Optional
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.events.models import Event, EventType
from len_bot.actions.models import ActionItem, ActionType
from len_bot.actions.queue import ActionQueue
from len_bot.events.store import EventStore
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal
from len_bot.plugins.models import PluginManifest, PluginPermission, PluginType
from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.testing.social import social_result

class LotterySensoryPlugin(BasePlugin):
    """Goal 6: Social participant plugin that emits sensory events rather than sending messages directly."""
    def __init__(self):
        super().__init__(PluginManifest(
            id="lottery_plugin",
            name="群抽奖互动插件",
            plugin_type=PluginType.SENSORY,
            permissions=[PluginPermission.EMIT_EVENT],
            enabled=True
        ))
        self.context: Optional[PluginContext] = None

    async def on_load(self, context: PluginContext) -> None:
        self.context = context

    async def trigger_lottery(self, scene_id: str, creator_id: str, prize: str) -> None:
        # Invariant B: emit sensory event into event bus! Never prompt-and-send directly!
        event = Event(
            event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id=scene_id,
            actor_id=creator_id,
            timestamp=time.time(),
            payload={"raw_text": f"[CQ:at,qq=12345678] 发起抽奖：{prize}，两小时后开奖"}
        )
        await self.context.emit_event(event)

class FaultyToolPlugin(BasePlugin):
    """Goal 7: Plugin with crashing and hanging tools to verify sandbox fault isolation."""
    def __init__(self):
        super().__init__(PluginManifest(
            id="faulty_plugin",
            name="故障沙箱测试插件",
            plugin_type=PluginType.TOOL,
            permissions=[PluginPermission.REGISTER_TOOL],
            enabled=True,
            timeout_seconds=0.2  # 200ms timeout for sandbox test
        ))

    async def on_load(self, context: PluginContext) -> None:
        async def crash_handler(args: dict[str, Any]) -> str:
            raise ZeroDivisionError("Simulated tool fatal divide by zero!")

        async def hang_handler(args: dict[str, Any]) -> str:
            await asyncio.sleep(5.0)
            return "Should never reach here"

        context.register_tool(
            name="crash_tool",
            description="A tool that throws an unhandled exception",
            parameters={"type": "object", "properties": {}},
            handler=crash_handler
        )
        context.register_tool(
            name="hang_tool",
            description="A tool that hangs infinitely",
            parameters={"type": "object", "properties": {}},
            handler=hang_handler
        )

class SafetyFilterPlugin(BasePlugin):
    """Goal 7: Action interceptor plugin that blocks or sanitizes outbound actions."""
    def __init__(self):
        super().__init__(PluginManifest(
            id="safety_filter",
            name="敏感词安全拦截器",
            plugin_type=PluginType.INTERCEPTOR,
            permissions=[PluginPermission.INTERCEPT_ACTION],
            enabled=True
        ))

    async def on_load(self, context: PluginContext) -> None:
        async def intercept_outbound(action: ActionItem) -> Optional[ActionItem]:
            if "违禁敏感词" in action.content:
                # Reject and drop action
                return None
            if "敏感前缀:" in action.content:
                # Sanitize and rewrite content
                action.content = action.content.replace("敏感前缀:", "[安全脱敏]:")
            return action

        context.register_action_interceptor(intercept_outbound)

class UnprivilegedPlugin(BasePlugin):
    def __init__(self):
        super().__init__(PluginManifest(
            id="unprivileged_plugin",
            name="无权限插件",
            plugin_type=PluginType.TOOL,
            permissions=[],  # Zero permissions granted!
            enabled=True
        ))

@pytest.mark.asyncio
async def test_goal6_sensory_plugin_social_participant(tmp_path):
    """
    Goal 6: Plugin acts as sensory provider emitting events into runtime bus.
    Bot responds socially through cognition rather than plugin bypassing runtime.
    """
    db_file = str(tmp_path / "goal6_plugins.db")
    sent_messages = []

    async def mock_adapter(action: ActionItem) -> bool:
        sent_messages.append(action.content)
        return True

    async def mock_social_core(messages):
        last_msg = messages[-1]["content"] if messages else ""
        if "抽奖" in last_msg:
            return social_result(
                reason="有人发起了抽奖，自然接一句",
                content="好耶！开抽开抽，两小时后见分晓~",
            )
        return social_result(reason="无须发言")

    config = RuntimeConfig(bot_qq=12345678, db_path=db_file, debounce_idle_ms=50, debounce_max_ms=100)
    runtime = AgentRuntime(config, send_adapter=mock_adapter, mock_social_handler=mock_social_core)
    await runtime.start()

    # Load sensory plugin
    lottery_plugin = LotterySensoryPlugin()
    await runtime.plugin_host.load_plugin(lottery_plugin)

    scene_id = "group:lottery_scene"
    await runtime.scene_manager.get_or_create_actor(scene_id)

    # Trigger lottery through plugin
    await lottery_plugin.trigger_lottery(scene_id, "user:organizer", "限量版 LenBot 贴纸")
    await asyncio.sleep(0.3)

    # Verify message was sent by Bot's persona through ActionQueue
    assert len(sent_messages) == 1
    assert "好耶！开抽开抽" in sent_messages[0]

    await runtime.stop()

@pytest.mark.asyncio
async def test_plugin_permission_gating(tmp_path):
    """
    Verifies that plugins cannot invoke context capabilities without explicit permissions.
    """
    db_file = str(tmp_path / "perm_plugins.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    unprivileged = UnprivilegedPlugin()
    await runtime.plugin_host.load_plugin(unprivileged)
    ctx = runtime.plugin_host._plugin_contexts["unprivileged_plugin"]

    # 1. Attempt emit_event without permission -> PermissionError
    ev = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="g1", actor_id="u1", timestamp=time.time(), payload={})
    with pytest.raises(PermissionError) as exc_emit:
        await ctx.emit_event(ev)
    assert "emit_event" in str(exc_emit.value)

    # 2. Attempt register_tool without permission -> PermissionError
    with pytest.raises(PermissionError) as exc_tool:
        ctx.register_tool("unauth_tool", "desc", {}, lambda args: "res")
    assert "register_tool" in str(exc_tool.value)

    # 3. Attempt register_action_interceptor without permission -> PermissionError
    with pytest.raises(PermissionError) as exc_inter:
        ctx.register_action_interceptor(lambda act: act)
    assert "intercept_action" in str(exc_inter.value)

    await runtime.stop()

@pytest.mark.asyncio
async def test_goal7_plugin_crash_and_timeout_sandbox_isolation(tmp_path):
    """
    Goal 7: Plugin crash or timeout is strictly isolated and never crashes the Runtime or PiAgentCore.
    """
    db_file = str(tmp_path / "goal7_fault.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    faulty_plugin = FaultyToolPlugin()
    await runtime.plugin_host.load_plugin(faulty_plugin)

    # 1. Verify tools are exposed
    tool_defs = runtime.plugin_host.get_tool_definitions()
    tool_names = [t["function"]["name"] for t in tool_defs]
    assert "crash_tool" in tool_names
    assert "hang_tool" in tool_names

    # 2. Call crashing tool -> returns clean error string, zero uncaught exception
    crash_res = await runtime.plugin_host.execute_tool("crash_tool", {})
    assert "ZeroDivisionError" in crash_res
    assert "execution failed" in crash_res

    # 3. Call hanging tool -> times out after 0.2s cleanly, zero hang
    t0 = time.time()
    hang_res = await runtime.plugin_host.execute_tool("hang_tool", {})
    duration = time.time() - t0
    assert duration < 1.0
    assert "timed out" in hang_res

    # 4. Runtime and scene actor continue operating normally!
    scene_id = "group:resilience"
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    assert actor.state.scene_id == scene_id

    # 5. Dynamic Unload & Cleanup
    await runtime.plugin_host.unload_plugin("faulty_plugin")
    assert runtime.plugin_host.has_tool("crash_tool") is False
    assert runtime.plugin_host.has_tool("hang_tool") is False

    not_found_res = await runtime.plugin_host.execute_tool("crash_tool", {})
    assert "not found" in not_found_res

    await runtime.stop()

@pytest.mark.asyncio
async def test_action_interceptor_filtering(tmp_path):
    """
    Tests Action Interceptor pre-flight safety filtering:
    - Dangerous content is intercepted and dropped.
    - Sensitive content is sanitized before network transmission.
    """
    db_file = str(tmp_path / "action_filter.db")
    sent_messages = []

    async def mock_adapter(action: ActionItem) -> bool:
        sent_messages.append(action.content)
        return True

    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config, send_adapter=mock_adapter)
    await runtime.start()

    filter_plugin = SafetyFilterPlugin()
    await runtime.plugin_host.load_plugin(filter_plugin)

    # 1. Enqueue action with normal text -> sent successfully
    act_normal = ActionItem(action_type=ActionType.SEND_GROUP_MESSAGE, scene_id="group:test", content="你好世界！")
    runtime.action_queue.enqueue(act_normal)
    await asyncio.sleep(0.1)
    assert len(sent_messages) == 1
    assert sent_messages[0] == "你好世界！"

    # 2. Enqueue action with blocked text -> dropped by interceptor
    act_blocked = ActionItem(action_type=ActionType.SEND_GROUP_MESSAGE, scene_id="group:test", content="这是一条含有违禁敏感词的内容")
    runtime.action_queue.enqueue(act_blocked)
    await asyncio.sleep(0.1)
    assert len(sent_messages) == 1  # Not sent!

    # 3. Enqueue action with content requiring sanitization -> sanitized and sent
    act_sanitized = ActionItem(action_type=ActionType.SEND_GROUP_MESSAGE, scene_id="group:test", content="敏感前缀:用户电话13800000000")
    runtime.action_queue.enqueue(act_sanitized)
    await asyncio.sleep(0.1)
    assert len(sent_messages) == 2
    assert "[安全脱敏]:用户电话13800000000" in sent_messages[1]

    await runtime.stop()

@pytest.mark.asyncio
async def test_action_interceptor_exception_fails_closed(tmp_path):
    """Interceptor crash must drop the unsanitized action (fail closed) and drain the queue."""
    db_file = str(tmp_path / "interceptor_fail_closed.db")
    store = EventStore(db_file)
    await store.initialize()

    sent_messages: list[str] = []
    async def mock_adapter(action: ActionItem) -> bool:
        sent_messages.append(action.content)
        return True

    async def crashing_interceptor(action: ActionItem) -> Optional[ActionItem]:
        raise RuntimeError("Simulated interceptor crash")

    queue = ActionQueue(store, send_adapter=mock_adapter, action_interceptor=crashing_interceptor)
    await queue.start()
    try:
        queue.enqueue(ActionItem(action_type=ActionType.SEND_GROUP_MESSAGE, scene_id="group:test", content="未脱敏内容"))
        await asyncio.wait_for(queue._queue.join(), timeout=1.0)

        assert sent_messages == []
    finally:
        await queue.stop()
        await store.close()

@pytest.mark.asyncio
async def test_action_interceptor_drop_drains_queue(tmp_path):
    """Interceptor returning None must block the action without hanging queue.join()."""
    db_file = str(tmp_path / "interceptor_drop.db")
    store = EventStore(db_file)
    await store.initialize()

    sent_messages: list[str] = []
    async def mock_adapter(action: ActionItem) -> bool:
        sent_messages.append(action.content)
        return True

    async def blocking_interceptor(action: ActionItem) -> Optional[ActionItem]:
        return None

    queue = ActionQueue(store, send_adapter=mock_adapter, action_interceptor=blocking_interceptor)
    await queue.start()
    try:
        queue.enqueue(ActionItem(action_type=ActionType.SEND_GROUP_MESSAGE, scene_id="group:test", content="违禁内容"))
        await asyncio.wait_for(queue._queue.join(), timeout=1.0)

        assert sent_messages == []
    finally:
        await queue.stop()
        await store.close()
