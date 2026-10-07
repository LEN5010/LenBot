"""Plugin model contract, actual source permissions and discovery lifecycle."""
import json
from pathlib import Path

import pytest

from len_bot.next.chat.session import Chat
from len_bot.next.models.client import ChatModel, ToolCall
from len_bot.next.persona.profile import Persona
from len_bot.next.plugin_testing import PluginTest
from len_bot.next.plugins.manifest import parse_manifest

SCENE = 'onebot:group:80001'
OTHER = 'onebot:group:80002'
OWNER = 'onebot:70001'


@pytest.fixture
def package(tmp_path):
    directory = tmp_path / 'contract'
    directory.mkdir()
    manifest = (Path(__file__).parent / 'fixtures/plugins/sample/plugin.toml').read_text()
    manifest = manifest.replace('name = "sample"', 'name = "contract"')
    manifest += '\n[model]\ninstructions = "tools.md"\n'
    (directory / 'plugin.toml').write_text(manifest)
    (directory / 'tools.md').write_text('共享流程：查询返回 JSON；只有回执 sent 表示送达。')
    (directory / '__init__.py').write_text('''from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from len_bot.next.plugin import Plugin, Invocation, tool

class SearchRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    action: Literal['search']
    query: str = Field(description='搜索词')

class ReadRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    action: Literal['read']
    title: str = Field(description='真实业务标题字段')

Request = Annotated[SearchRequest | ReadRequest, Field(discriminator='action')]

class Contract(Plugin):
    def unavailable_tools(self, scene):
        return {} if self.ctx.config['show_details'] else {'echo_data': '查询功能未开启'}

    @tool('echo_data', '完整说明第一句。第二句保留参数语义。', summary='查询协议样本')
    async def echo(self, ctx: Invocation,
                   value: Annotated[JsonValue, Field(description='原样返回 JSON', examples=[{'ok': True}])]) -> JsonValue:
        return value

    @tool('request_data', '按明确 action 校验的请求。', summary='类型化请求样本')
    async def request(self, ctx: Invocation, request: Annotated[Request, Field(description='搜索或详情请求')]) -> dict:
        return request.model_dump()

    @tool('legacy_text', '旧工具第一句。旧工具第二句也保留。')
    async def text(self, ctx: Invocation) -> str:
        return '旧字符串结果'

    @tool('owner_source', '使用真实消息发送者检查主人权限。', summary='主人操作样本', needs_source=True)
    async def owner(self, ctx: Invocation) -> dict:
        ctx.require_owner()
        return {'requester': ctx.message.sender.uid, 'source': ctx.message.platform_message_id}

    @tool('bad_result', '协议边界拒绝任意 Python 对象。', summary='无效结果样本')
    async def invalid(self, ctx: Invocation):
        return object()
''')
    return directory


def persona(tools='all'):
    return Persona(id='synthetic', name='合成角色', brief='隔离测试', behavior='正常对话',
                   self_reference=['我'], aliases=[], tools=tools, skills=[], styles=[],
                   voice='简短', boundaries='合成协议', examples=[])


@pytest.mark.asyncio
async def test_json_values_and_parameter_metadata_use_real_host_boundary(package):
    async with PluginTest(package, now=lambda: 1791298800) as bot:
        preview = {item['name']: item for item in bot.preview_tools()}
        assert preview['echo_data']['summary'] == '查询协议样本'
        assert preview['echo_data']['description'].endswith('第二句保留参数语义。')
        field = preview['echo_data']['parameters']['properties']['value']
        assert field['description'] == '原样返回 JSON' and field['examples'] == [{'ok': True}]
        for value in ({'中文': [1, True, None]}, [False, 2.5], None, 3, False):
            assert json.loads(await bot.tool('echo_data', {'value': value})) == value
        assert await bot.tool('legacy_text', {}) == '旧字符串结果'
        assert preview['legacy_text']['summary'] == '旧工具第一句。旧工具第二句也保留。'
        with pytest.raises(ValueError):
            await bot.tool('echo_data', {'value': object()})
        with pytest.raises(ValueError):
            await bot.tool('bad_result', {})


@pytest.mark.asyncio
async def test_source_is_a_real_message_in_current_scene_not_supplied_requester(package):
    async with PluginTest(package, scenes=[SCENE, OTHER], owners=[OWNER], now=lambda: 1234567890) as bot:
        owner = bot.add_message('请进行操作', sender=OWNER)
        stranger = bot.add_message('操作', sender='onebot:70002')
        other = bot.add_message('另一群的请求', sender=OWNER, scene=OTHER)
        own = bot.add_message('机器人自己', sender=bot.host.bot_id)
        assert owner.time == 1234567890
        result = json.loads(await bot.tool('owner_source', {'source_message_id': owner.platform_message_id}))
        assert result == {'requester': OWNER, 'source': owner.platform_message_id}
        with pytest.raises(PermissionError, match='主人'):
            await bot.tool('owner_source', {'source_message_id': stranger.platform_message_id})
        for source in (other.platform_message_id, own.platform_message_id, OWNER, '不存在'):
            with pytest.raises(ValueError, match='真实请求消息'):
                await bot.tool('owner_source', {'source_message_id': source})
        for args in ({}, {'requester': OWNER}, {'source_message_id': owner.platform_message_id, 'requester': OWNER}):
            with pytest.raises(ValueError):
                await bot.tool('owner_source', args)


@pytest.mark.asyncio
async def test_shared_guide_tracks_discovery_permissions_reload_and_compaction(package):
    async def waiting(seconds):
        return '未使用'

    async with PluginTest(package) as bot:
        config = bot.host.config.scene_config(SCENE)
        store = bot.host.runtime.store
        async with ChatModel(config.model_settings('mind')) as mind:
            chat = Chat(config, persona(), store, mind, external_tools=bot.host.tools_for(SCENE))
            assert '查询协议样本' in chat.context.system and '共享流程' not in chat.context.system
            assert 'echo_data' not in chat.toolset.tool_names
            content, _, discovered = await chat.toolset.execute('unused',
                ToolCall(id='discovery', name='tool_search', arguments={'query': '查询协议样本'}), waiting)
            assert json.loads(content)['tools'] == [{'name': 'echo_data', 'description': '查询协议样本', 'source': '插件 contract'}]
            full_result, _, _ = await chat.toolset.execute('unused',
                ToolCall(id='full-lookup', name='tool_search', arguments={'query': '参数语义'}), waiting)
            assert json.loads(full_result)['tools'] == json.loads(content)['tools']
            store.save_discovered_tools(SCENE, discovered)
            chat.set_external_tools(bot.host.tools_for(SCENE))
            assert 'echo_data' in chat.toolset.tool_names
            assert chat.context.system.count('共享流程') == 1
            store.save_discovered_tools(SCENE, ['echo_data', 'legacy_text'])
            chat.set_external_tools(bot.host.tools_for(SCENE))
            assert chat.context.system.count('共享流程') == 1
            denied = Chat(config, persona(['tool_search']), store, mind, external_tools=bot.host.tools_for(SCENE))
            assert '共享流程' not in denied.context.system
            guide = bot.host.plugins['contract'].directory / 'tools.md'
            guide.write_text('刷新后的共享指南')
            await bot.host.reload('contract', bot.host.config)
            chat.set_external_tools(bot.host.tools_for(SCENE))
            assert '刷新后的共享指南' in chat.context.system and '共享流程' not in chat.context.system
            store.save_discovered_tools(SCENE, [])
            chat.set_external_tools(bot.host.tools_for(SCENE))
            assert '刷新后的共享指南' not in chat.context.system
            await bot.host.stop_plugin('contract')
            chat.set_external_tools(bot.host.tools_for(SCENE))
            assert 'echo_data' not in chat.context.system and '刷新后的共享指南' not in chat.context.system


@pytest.mark.asyncio
async def test_configuration_unavailability_is_visible_but_not_discoverable(package):
    async with PluginTest(package, config={'show_details': False}) as bot:
        preview = {item['name']: item for item in bot.preview_tools()}
        assert preview['echo_data']['reasons'] == ['查询功能未开启']
        assert 'echo_data' not in {tool.name for tool in bot.host.tools_for(SCENE)}
        assert 'legacy_text' in {tool.name for tool in bot.host.tools_for(SCENE)}


def test_model_guide_must_be_a_package_relative_file(package):
    manifest = package / 'plugin.toml'
    manifest.write_text(manifest.read_text().replace('instructions = "tools.md"', 'instructions = "../outside.md"'))
    with pytest.raises(ValueError, match='相对文件路径'):
        parse_manifest(manifest)


@pytest.mark.asyncio
async def test_model_schema_strips_metadata_titles_but_preserves_business_title_and_action(package):
    async with PluginTest(package) as bot:
        preview = {item['name']: item for item in bot.preview_tools()}
        parameters = preview['request_data']['parameters']
        schema = parameters['properties']['request']
        assert schema['discriminator']['propertyName'] == 'action' and len(schema['oneOf']) == 2
        read = parameters['$defs']['ReadRequest']
        assert 'title' not in read
        assert read['properties']['title']['description'] == '真实业务标题字段'
        assert 'title' not in read['properties']['title']
        data = {'action': 'read', 'title': '业务值保留'}
        assert json.loads(await bot.tool('request_data', {'request': data})) == data
        for value in ({'action': 'read'}, {'action': 'search', 'title': '错误分支'},
                      {'action': 'search', 'query': '词', 'title': '额外字段'}, {'action': 'missing'}):
            with pytest.raises(ValueError):
                await bot.tool('request_data', {'request': value})
