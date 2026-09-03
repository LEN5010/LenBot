import pytest
import asyncio
import time
import httpx
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.events.models import Event, EventType
from len_bot.plugins.builtin.bilibili_live import BilibiliLiveSensor, LIVE_API_URL


@pytest.mark.asyncio
async def test_builtin_plugins_loaded_with_manifest_and_persisted_toggle(tmp_path):
    """
    ADR-0021: builtin plugins load at start with default config, declarative
    manifest surfaces (config_schema / emitted_events / registered_tools), and
    their enable state persists across restarts.
    """
    db_file = str(tmp_path / "plugins.db")
    config = RuntimeConfig(bot_qq=1, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    plugins = {p["id"]: p for p in runtime.plugin_host.status_snapshot()}
    assert set(plugins) == {"bilibili_live_sensor", "web_search_tool"}

    sensor = plugins["bilibili_live_sensor"]
    assert "LIVE_STARTED" in sensor["emitted_events"]
    assert sensor["config_schema"]["properties"]["room_ids"]["type"] == "array"

    tool = plugins["web_search_tool"]
    assert set(tool["registered_tools"]) == {"web_search", "read_page"}
    assert runtime.plugin_host.has_tool("web_search")

    # Disable the sensor, persist, restart → still disabled
    await runtime.plugin_host.disable_plugin("bilibili_live_sensor")
    await runtime.save_plugin_state()
    state = await runtime.event_store.get_dynamic_config("plugins_state")
    assert state["bilibili_live_sensor"]["enabled"] is False
    await runtime.stop()

    runtime2 = AgentRuntime(RuntimeConfig(bot_qq=1, db_path=db_file))
    await runtime2.start()
    sensor2 = {p["id"]: p for p in runtime2.plugin_host.status_snapshot()}["bilibili_live_sensor"]
    assert sensor2["enabled"] is False
    assert sensor2["state"] == "disabled"
    await runtime2.stop()


@pytest.mark.asyncio
async def test_bilibili_sensor_emits_live_events_into_bus(tmp_path, monkeypatch):
    """
    Goal 5 sensor path (ADR-0021): a room transition 0→1 emits LIVE_STARTED and
    1→0 emits LIVE_ENDED into the runtime bus, where the SceneActor commits them
    as immutable facts. No message is ever sent — the sensor cannot notify.
    """
    db_file = str(tmp_path / "sensor.db")
    config = RuntimeConfig(bot_qq=12345678, db_path=db_file)
    runtime = AgentRuntime(config)
    await runtime.start()

    scene_id = "group:live"
    sensor = runtime.plugin_host._plugins["bilibili_live_sensor"]
    sensor.manifest.config = {"scene_id": scene_id, "room_ids": [777], "interval_seconds": 10}
    # Stop the background loop: manual polling drives the deterministic scenario
    sensor._poll_task.cancel()
    try:
        await sensor._poll_task
    except asyncio.CancelledError:
        pass

    # Fake the Bilibili API: first poll offline, second online, third offline
    responses = [
        {"code": 0, "data": {"live_status": 0, "title": "T1"}},
        {"code": 0, "data": {"live_status": 1, "title": "直播中：决赛日"}},
        {"code": 0, "data": {"live_status": 0, "title": "T1"}},
    ]

    async def fake_get(url, params=None):
        return httpx.Response(200, json=responses.pop(0), request=httpx.Request("GET", url))

    monkeypatch.setattr(sensor._client, "get", fake_get)

    await sensor._poll_once()  # offline → nothing
    await sensor._poll_once()  # 0→1 → LIVE_STARTED
    await sensor._poll_once()  # 1→0 → LIVE_ENDED
    await asyncio.sleep(0.2)

    # Fact events committed as immutable raw history through the SceneActor
    events = await runtime.event_store.get_recent_events(scene_id, limit=5)
    started = [e for e in events if e.event_type == EventType.LIVE_STARTED]
    ended = [e for e in events if e.event_type == EventType.LIVE_ENDED]
    assert started and "决赛日" in started[0].raw_text
    assert ended

    await runtime.stop()


@pytest.mark.asyncio
async def test_web_search_tool_executes_via_host_sandbox(tmp_path, monkeypatch):
    """
    ADR-0021: web_search executes through the PluginHost sandbox (timeout +
    fault isolation) and parses DuckDuckGo results into evidence lines.
    Network failures surface as error strings — never as exceptions.
    """
    db_file = str(tmp_path / "websearch.db")
    runtime = AgentRuntime(RuntimeConfig(bot_qq=1, db_path=db_file))
    await runtime.start()

    fake_html = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">标题A</a>
      <a class="result__snippet" href="#">这是摘要A</a>
    </div>
    <div class="result">
      <a class="result__a" href="https://example.org/b">标题B</a>
      <a class="result__snippet" href="#">这是摘要B</a>
    </div>
    """

    class FakeResponse:
        status_code = 200
        text = fake_html
        def raise_for_status(self): pass
        def json(self): return {}

    async def fake_get(url, params=None):
        return FakeResponse()

    plugin = runtime.plugin_host._plugins["web_search_tool"]
    monkeypatch.setattr(plugin._client, "get", fake_get)

    result = await runtime.plugin_host.execute_tool("web_search", {"query": "test"})
    assert "标题A" in result
    assert "https://example.com/a" in result  # uddg unwrapped
    assert "摘要B" in result
    assert result.startswith("Error:") is False

    # Error path: crash → sandbox returns error string
    async def failing_get(url, params=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(plugin._client, "get", failing_get)
    err_result = await runtime.plugin_host.execute_tool("web_search", {"query": "test"})
    assert err_result.startswith("Error:")

    # Health tracking recorded the crash
    status = {p["id"]: p for p in runtime.plugin_host.status_snapshot()}["web_search_tool"]
    assert status["error_count"] >= 1
    assert status["last_error"]

    await runtime.stop()


@pytest.mark.asyncio
async def test_unconfigured_sensor_stays_inert(tmp_path):
    """A sensor without scene_id/room_ids never emits — config is required to act."""
    db_file = str(tmp_path / "inert.db")
    runtime = AgentRuntime(RuntimeConfig(bot_qq=1, db_path=db_file))
    await runtime.start()

    sensor = runtime.plugin_host._plugins["bilibili_live_sensor"]
    sensor.manifest.config = {"scene_id": "", "room_ids": [], "interval_seconds": 10}
    await sensor._poll_once()  # must not raise nor emit

    events = await runtime.event_store.get_recent_events("group:anything", limit=5)
    assert events == []

    await runtime.stop()
