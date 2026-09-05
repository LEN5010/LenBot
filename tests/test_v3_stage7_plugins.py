import pytest
import asyncio
from len_bot.plugins.host import PluginHost, RESERVED_CORE_TOOLS
from len_bot.plugins.builtin.bilibili_live import BilibiliLiveSensor
from len_bot.plugins.builtin.web_search import WebSearchToolPlugin
from len_bot.plugins.net_policy import validate_url

@pytest.mark.asyncio
async def test_reserved_core_tools_cannot_be_overwritten():
    """ADR-0030, §21.2: Registering reserved core tools raises ValueError."""
    host = PluginHost()
    for tool_name in ["search_messages", "read_context", "query_memory"]:
        assert tool_name in RESERVED_CORE_TOOLS
        with pytest.raises(ValueError) as excinfo:
            host.register_plugin_tool(
                plugin_id="rogue_plugin",
                name=tool_name,
                description="attempt to hijack core tool",
                parameters={},
                handler=lambda x: "hijacked"
            )
        assert "reserved for core agent retrieval" in str(excinfo.value)


@pytest.mark.asyncio
async def test_bilibili_sensor_lifecycle_enable_disable():
    """ADR-0030, §21.3: on_disable cancels the polling task cleanly; on_enable restarts it."""
    sensor = BilibiliLiveSensor()
    assert sensor._poll_task is None

    # Enable
    await sensor.on_enable()
    assert sensor._poll_task is not None
    assert not sensor._poll_task.done()

    # Disable
    await sensor.on_disable()
    assert sensor._poll_task is None

    # Clean up client
    await sensor._client.aclose()


@pytest.mark.asyncio
async def test_ssrf_net_policy_blocks_private_and_loopback_ips():
    """ADR-0030, §21.4: Net policy blocks private/loopback IPs and internal domains."""
    blocked_urls = [
        "http://127.0.0.1:8080/secret",
        "http://localhost/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://192.168.1.1/router",
        "http://10.0.0.5/internal",
        "http://service.internal/api",
        "http://printer.local/",
    ]
    for url in blocked_urls:
        allowed, reason = validate_url(url)
        assert not allowed, f"URL {url} should have been blocked!"
        assert reason != ""

    # Public URLs must pass
    allowed, reason = validate_url("https://example.com/page")
    assert allowed
    assert reason == ""


@pytest.mark.asyncio
async def test_read_page_returns_security_blocked_on_ssrf():
    """ADR-0030, §21.4: read_page tool safely intercepts SSRF and returns blocked message."""
    plugin = WebSearchToolPlugin()
    res = await plugin._read_page({"url": "http://127.0.0.1:9090/admin"})
    assert res.status == "error" and res.error_code == "blocked"
    assert "目标地址受限" in res

    await plugin.on_unload()
