import asyncio
import json
import gzip

import httpx
import pytest

from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.plugins.base import BasePlugin
from len_bot.plugins.models import PluginManifest, PluginPermission
from len_bot.plugins.builtin.web_search import WebSearchToolPlugin
from len_bot.tools.retrieval import RetrievalToolkit
from len_bot.tools.results import ToolResult


@pytest.mark.asyncio
@pytest.mark.parametrize("media_type,body,expected", [
    ("text/plain", "first\n" + "middle\n" * 1200 + "THE END", "THE END"),
    ("application/json", '{"result": [1, 2], "note": "JSON evidence"}', "JSON evidence"),
    ("text/html", '<html><head><title>Article title</title><script>SECRET_SCRIPT</script></head><body><nav>Navigation</nav><article><h1>Article</h1>' + '<p>Researchers compared two methods and documented their observations with a reproducible protocol.</p>' * 12 + '<p>THE END <a href="https://example.org/source">Source</a></p></article></body></html>', "THE END"),
    ("text/html", '<html><body><main><h1>Forum discussion</h1>' + '<p>A participant describes a configuration problem and another explains a possible workaround.</p>' * 10 + '</main></body></html>', "workaround"),
])
async def test_generic_read_preserves_body_and_tail(media_type, body, expected, monkeypatch):
    monkeypatch.setattr("len_bot.tools.http.validate_url", lambda url: (True, ""))
    monkeypatch.setattr("len_bot.plugins.builtin.web_search.validate_url", lambda url: (True, ""))
    plugin = WebSearchToolPlugin()
    await plugin._client.aclose()
    plugin._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": media_type}, text=body)))
    try:
        result = await plugin._read_page({"url": "https://example.org/article"})
        assert result.status == "ok"
        assert expected in result.content
        assert "SECRET_SCRIPT" not in result.content
        assert result.sources[0].url == "https://example.org/article"
    finally:
        await plugin.on_unload()


@pytest.mark.asyncio
async def test_redirect_scope_size_and_unsupported(monkeypatch):
    monkeypatch.setattr("len_bot.tools.http.validate_url", lambda url: (not url.startswith("http://127."), "private"))
    monkeypatch.setattr("len_bot.plugins.builtin.web_search.validate_url", lambda url: (True, ""))
    visited = []
    def respond(req):
        visited.append(str(req.url))
        if req.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        if req.url.path == "/large":
            return httpx.Response(200, text="x" * 2_000_001)
        if req.url.path == "/empty":
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><script>app()</script></html>")
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")
    plugin = WebSearchToolPlugin()
    await plugin._client.aclose()
    plugin._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        for path in ("redirect", "large"):
            result = await plugin._read_page({"url": f"https://example.org/{path}"})
            assert result.status == "error"
        assert not any("127.0.0.1" in url for url in visited)
        result = await plugin._read_page({"url": 'https://example.org/empty'})
        assert result.status == 'unsupported'
        result = await plugin._read_page({"url": 'https://example.org/pdf'})
        assert result.status == 'error'  # Invalid PDF bytes, not an unsupported format.
    finally:
        await plugin.on_unload()


@pytest.mark.asyncio
async def test_compressed_http_body_is_decoded_once(monkeypatch):
    monkeypatch.setattr("len_bot.tools.http.validate_url", lambda url: (True, ""))
    monkeypatch.setattr("len_bot.plugins.builtin.web_search.validate_url", lambda url: (True, ""))
    plugin = WebSearchToolPlugin()
    await plugin._client.aclose()
    plugin._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(
        200, headers={"content-type": "text/plain; charset=utf-8", "content-encoding": "gzip"},
        content=gzip.compress("压缩网页正文".encode()))))
    try:
        result = await plugin._read_page({"url": "https://example.org/text"})
        assert result.status == "ok" and result.content == "压缩网页正文"
    finally:
        await plugin.on_unload()


class TestToolPlugin(BasePlugin):
    __test__ = False
    def __init__(self):
        super().__init__(PluginManifest(id="test_tool", name="test", permissions=[PluginPermission.REGISTER_TOOL]))
        self.calls = 0
        self.active = 0
        self.peak = 0
    async def on_load(self, context):
        async def handler(args):
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            return ToolResult(content="start " + "body " * 3000 + " tail", evidence_kind="external")
        context.register_tool("lookup_document", "Look up public documents", {"type": "object", "properties": {}}, handler, read_only=True, deferred=True)
        context.register_tool("unknown_effect", "legacy", {"type": "object", "properties": {}}, handler)


@pytest.mark.asyncio
async def test_discovery_parallel_cache_paging_and_scoped_durability(tmp_path):
    runtime = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "db")))
    await runtime.start()
    plugin = TestToolPlugin()
    try:
        await runtime.plugin_host.load_plugin(plugin)
        kit = RetrievalToolkit(runtime.event_store, ["group:a"], "group:a", plugin_host=runtime.plugin_host,
                               read_only_only=True, on_observation=runtime.commit_tool_observation)
        assert "lookup_document" not in [x["function"]["name"] for x in kit.get_tool_definitions()]
        await kit.execute("tool_search", {"query": "document"})
        assert "lookup_document" in [x["function"]["name"] for x in kit.get_tool_definitions()]
        denied = await kit.execute_result("unknown_effect", {})
        assert denied.error_code == "capability_denied" and plugin.calls == 0
        results = await kit.execute_many([("lookup_document", {"n": n}) for n in range(4)])
        assert plugin.peak > 1 and plugin.peak <= 3
        first = ToolResult.model_validate_json(results[0])
        assert first.truncated and first.next_offset
        again = await kit.execute_result("lookup_document", {"n": 0})
        assert again.cached and plugin.calls == 4
        await kit.execute_result("lookup_document", {"n": 0, "refresh": True})
        assert plugin.calls == 5
        raw = await runtime.event_store.read_tool_observation(first.result_id, ["group:a"])
        assert raw.content.endswith("tail")
        assert await runtime.event_store.read_tool_observation(first.result_id, ["group:b"]) is None
        page = await kit.execute_result("read_tool_result", {"result_id": first.result_id, "offset": 12000})
        assert page.content.endswith("tail")
        assert await runtime.event_store.event_exists(first.observation_event_id, "group:a")
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_web_pixels_and_observation_commit_together_in_the_requested_scene(tmp_path,monkeypatch):
    import io
    import sqlite3
    from PIL import Image
    monkeypatch.setattr('len_bot.tools.http.validate_url',lambda url:(True,''))
    output=io.BytesIO();picture=Image.new('RGB',(30,20),'blue');picture.save(output,format='PNG')
    pdf=io.BytesIO();picture.save(pdf,format='PDF')
    runtime=AgentRuntime(RuntimeConfig(db_path=str(tmp_path/'media.db')))
    await runtime.start()
    await runtime.media_service._client.aclose()
    runtime.media_service._client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req:httpx.Response(200,headers={'content-type':'application/pdf' if req.url.path.endswith('.pdf') else 'image/png'},
            content=pdf.getvalue() if req.url.path.endswith('.pdf') else output.getvalue())))
    kit=RetrievalToolkit(runtime.event_store,['group:a'],'group:a',read_only_only=True,
        media_service=runtime.media_service,on_observation=runtime.commit_tool_observation)
    try:
        result=await kit.execute_result('read_web_media',{'url':'https://example.org/chart.png'})
        assert result.status=='ok' and len(result.attachments)==1
        asset=await runtime.event_store.get_media(result.attachments[0],['group:a'])
        assert not asset['curated'] and asset['source_event_id']==result.observation_event_id
        assert await runtime.event_store.get_media(asset['id'],['group:b']) is None
        assert await runtime.event_store.read_tool_observation(result.result_id,['group:b']) is None
        rendered=await kit.execute_result('read_web_media',{'url':'https://example.org/report.pdf','page':1})
        assert rendered.status=='ok' and rendered.coverage=='pdf_page' and rendered.attachments
        await runtime.event_store._db.execute("CREATE TRIGGER reject_observation BEFORE INSERT ON tool_observations BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with pytest.raises(sqlite3.IntegrityError):
            await kit.execute_result('read_web_media',{'url':'https://example.org/chart2.png'})
        assert len(await runtime.event_store.list_media(['group:a']))==2
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_search_outage_is_not_external_evidence_or_a_retry_loop(tmp_path,monkeypatch):
    monkeypatch.setattr('len_bot.tools.http.validate_url',lambda url:(True,''))
    monkeypatch.setattr('len_bot.plugins.builtin.web_search.validate_url',lambda url:(True,''))
    runtime=AgentRuntime(RuntimeConfig(db_path=str(tmp_path/'search.db')))
    await runtime.start()
    plugin=runtime.plugin_host._plugins['web_search_tool']
    await plugin._client.aclose()
    plugin._client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req:
        httpx.Response(202,text='<html>Verification required</html>') if req.url.host=='www.bing.com'
        else httpx.Response(200,headers={'content-type':'text/plain'},text='Published model documentation')))
    kit=RetrievalToolkit(runtime.event_store,['group:a'],'group:a',read_only_only=True,plugin_host=runtime.plugin_host,
        on_observation=runtime.commit_tool_observation)
    try:
        invalid=await kit.execute_result('web_search',{'query':''})
        assert invalid.error_code=='invalid_arguments'
        assert 'web_search' in {d['function']['name'] for d in kit.get_tool_definitions()}
        failed=await kit.execute_result('web_search',{'query':'recent model'})
        assert failed.status=='unsupported'
        assert 'web_search' not in {d['function']['name'] for d in kit.get_tool_definitions()}
        with pytest.raises(ValueError,match='外部查询'):
            kit.validate_conclusion_sources([],[])
        kit.validate_conclusion_sources([failed.result_id],['发布时间尚未核实'])
        body=await kit.execute_result('read_page',{'url':'https://example.org/model'})
        kit.validate_conclusion_sources([body.result_id],[])
    finally:
        await runtime.stop()
