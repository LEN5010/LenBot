"""Plugin package loading (plugin.toml, interface version, configuration) and OneBot notice parsing."""

import json
from pathlib import Path
import shutil

import pytest
from pydantic import ValidationError

from len_bot.next.config import load_host_config
from len_bot.next.plugins.host import BUILTIN, PluginHost, read_manifest
from len_bot.next.platform.onebot_messages import parse_notice


CORE = {"say", "wait", "tool_search"}


def test_object_list_config_keeps_structured_records(tmp_path):
    directory = tmp_path / "roomwatch"
    directory.mkdir()
    (directory / "plugin.toml").write_text('''name = "roomwatch"
version = "1.0.0"
interface = 1
requires_lenbot = ">=0.1,<1"
requires_python = ">=3.13"
platforms = ["linux", "darwin", "win32"]
reload = "plugin"
authors = ["LEN5010"]
license = "AGPL-3.0-or-later"
description = "Room subscriptions"
[config.rooms]
type = "object_list"
description = "Room UID, room number, name and scenes"
default = [{uid = 100001, room_id = 123, name = "示例", scenes = ["onebot:group:80001"]}]
''', encoding="utf-8")
    manifest = read_manifest(directory)
    model = manifest.values_model(())
    values = model.model_validate({}).model_dump()
    assert values["rooms"] == [{"uid": 100001, "room_id": 123, "name": "示例", "scenes": ["onebot:group:80001"]}]
    assert model.model_validate({"rooms": []}).model_dump() == {"rooms": []}
    for malformed in ("[]", ["room:123"], [[123]], [None], {"uid": 100001}):
        with pytest.raises(ValidationError):
            model.model_validate({"rooms": malformed})
    source = (directory / "plugin.toml").read_text(encoding="utf-8")
    (directory / "plugin.toml").write_text(source.replace('type = "object_list"', 'type = "string_list"'), encoding="utf-8")
    with pytest.raises(ValueError):
        read_manifest(directory)


MANIFEST_HEAD = '''name = "roomwatch"
version = "1.0.0"
interface = 1
requires_lenbot = ">=0.1,<1"
requires_python = ">=3.13"
platforms = ["linux", "darwin", "win32"]
reload = "plugin"
authors = ["LEN5010"]
license = "AGPL-3.0-or-later"
description = "Room subscriptions"
'''


def test_form_fields_declare_labels_choices_scenes_paths_and_urls(tmp_path):
    directory = tmp_path / "roomwatch"
    directory.mkdir()
    (directory / "plugin.toml").write_text(MANIFEST_HEAD + '''
[config.card_mode]
type = "string"
label = "卡片样式"
group = "显示"
description = "Card layout"
default = "auto"
options = [{value = "auto", label = "自动"}, {value = "image", label = "图片"}]

[config.font]
type = "path"
label = "字体文件"
description = "Font file"
placeholder = "/usr/share/fonts/example.ttf"

[config.feed]
type = "url"
description = "Feed address"

[config.backup_font]
type = "path"
description = "Optional font"
default = ""

[config.note]
type = "string"
description = "Free text"
multiline = true
default = ""

[config.rooms]
type = "object_list"
label = "直播间"
description = "Rooms"
default = []
[config.rooms.fields.room_id]
type = "integer"
label = "房间号"
description = "Room number"
[config.rooms.fields.scenes]
type = "scene_list"
label = "推送到的群"
description = "Scenes"
default = []
''', encoding="utf-8")
    manifest = read_manifest(directory)
    assert [choice.label for choice in manifest.config["card_mode"].choices()] == ["自动", "图片"]
    model = manifest.values_model(["onebot:group:80001"])
    values = {"font": "/srv/fonts/a.ttf", "feed": "https://example.com/rss",
              "rooms": [{"room_id": 123, "scenes": ["onebot:group:80001"]}]}
    assert model.model_validate(values).model_dump()["card_mode"] == "auto"
    assert model.model_validate({**values, "backup_font": ""}).model_dump()["backup_font"] == ""
    for change in ({"card_mode": "自动"}, {"font": "fonts/a.ttf"}, {"font": ""}, {"feed": ""}, {"backup_font": "a.ttf"}, {"feed": "ftp://example.com/a"},
                   {"feed": "https://"}, {"rooms": [{"room_id": 123, "scenes": ["onebot:group:80002"]}]},
                   {"rooms": [{"room_id": 123, "scenes": ["group 80001"]}]}):
        with pytest.raises(ValidationError):
            model.model_validate({**values, **change})

    source = (directory / "plugin.toml").read_text(encoding="utf-8")
    for broken in (source.replace('multiline = true', 'multiline = true\nminimum = 1'),
                   source.replace('[config.feed]\ntype = "url"', '[config.feed]\ntype = "url"\nmultiline = true'),
                   source.replace('label = "推送到的群"', 'label = "推送到的群"\ndefault = ["onebot:group:80001"]').replace(
                       'description = "Scenes"\ndefault = []', 'description = "Scenes"'),
                   source.replace('{value = "image", label = "图片"}', '{value = "auto", label = "图片"}'),
                   source.replace('label = "房间号"', 'label = "房间号"\ngroup = "显示"')):
        (directory / "plugin.toml").write_text(broken, encoding="utf-8")
        with pytest.raises(ValueError):
            read_manifest(directory)


def test_plugin_owner_permission_uses_root_identity_and_enabled_scene(tmp_path):
    root = _root(tmp_path, {"clock": {}}, ["clock"])
    path = root / "lenbot.config.json"
    source = json.loads(path.read_text())
    source["owners"] = ['onebot:70001']
    source["scenes"]["onebot:group:80001"]["tasks"] = {"owner": 'onebot:70002'}
    path.write_text(json.dumps(source))
    host = PluginHost(load_host_config(root), core_tools=CORE)
    ctx = host.plugins["clock"].context
    ctx.require_owner("onebot:group:80001", 'onebot:70001')
    for scene, requester in (("onebot:group:80001", 'onebot:70002'), ("onebot:group:80001", 'onebot:90001'),
                             ("onebot:private:80002", 'onebot:70001')):
        with pytest.raises(PermissionError):
            ctx.require_owner(scene, requester)
    del source["owners"]
    path.write_text(json.dumps(source))
    host = PluginHost(load_host_config(root), core_tools=CORE)
    with pytest.raises(PermissionError):
        host.plugins["clock"].context.require_owner("onebot:group:80001", 'onebot:70001')
    for invalid in ('onebot:90001', "nickname", 70001, "0", ""):
        source["owners"] = invalid
        path.write_text(json.dumps(source))
        with pytest.raises(ValueError):
            load_host_config(root)


def _root(tmp_path: Path, plugins: dict | None, scene_plugins: list[str] | None = None) -> Path:
    root = tmp_path / "host"
    root.mkdir()
    source = {"compaction": {"input_tokens": 2000},
        "mode": "isolated-multi", "bot_id": 'onebot:90001', "timezone": "Asia/Shanghai", "database": "state.db",
        "onebot": {"mode": "reverse_ws", "listen_host": "127.0.0.1", "listen_port": 0},
        "models": {"providers": {"sample": {"api": "openai-chat", "base_url": "http://127.0.0.1:9/v1",
                                            "api_key": "synthetic"}},
                   "roles": {"mind": {"provider": "sample", "model": "mind", "context_window_tokens": 8192}}},
        "scenes": {"onebot:group:80001": {"persona": "role", **({} if scene_plugins is None else {"plugins": scene_plugins})},
                   "onebot:private:80002": {"persona": "role"}},
    }
    if plugins is not None:
        source["plugins"] = plugins
    (root / "lenbot.config.json").write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    return root


def _copy_clock(target: Path, name: str, *, interface: int = 1) -> Path:
    directory = target / name
    shutil.copytree(BUILTIN / "clock", directory, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = (directory / "plugin.toml").read_text(encoding="utf-8")
    manifest = manifest.replace('name = "clock"', f'name = "{name}"').replace("interface = 1", f"interface = {interface}")
    (directory / "plugin.toml").write_text(manifest, encoding="utf-8")
    source = (directory / "__init__.py").read_text(encoding="utf-8")
    (directory / "__init__.py").write_text(source.replace('@command("时间"', f'@command("时间{name}"'),
                                           encoding="utf-8")
    return directory


@pytest.mark.asyncio
async def test_dependency_restore_ignores_unapplied_first_install_candidate(tmp_path, capsys):
    from io import BytesIO
    import zipfile

    from len_bot.next.maintenance.plugin_dependencies import install
    from len_bot.next.plugins.install import PluginInstaller

    root = _root(tmp_path, {'paths': ['plugins'], 'clock': {}, 'counter': {}, 'disabled': ['counter']})
    package = Path(__file__).parents[1] / 'developer/examples/counter'
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name in ('plugin.toml', '__init__.py'):
            archive.writestr(name, (package / name).read_bytes())
    installer = PluginInstaller(root)
    await installer.prepare_zip(buffer.getvalue(), 'counter.zip', [])
    record_before = installer.read('counter')
    config_before = (root / 'lenbot.config.json').read_bytes()

    await install(root)

    assert '已配置插件：clock' in capsys.readouterr().out
    assert installer.read('counter') == record_before
    assert (root / 'lenbot.config.json').read_bytes() == config_before
    assert not (installer.directory / 'counter').exists()


def test_builtin_clock_manifest_loads_with_checked_values(tmp_path):
    manifest = read_manifest(BUILTIN / "clock")
    assert manifest.config["show_seconds"].type == "boolean"

    root = _root(tmp_path, {"clock": {"show_seconds": False}}, ["clock"])
    host = PluginHost(load_host_config(root), core_tools=CORE)
    record = host.plugins["clock"]
    assert (record.status, record.error, record.scenes) == ("loaded", None, ("onebot:group:80001",))
    assert record.context.config == {"show_seconds": False}


def test_interface_mismatch_and_bad_values_fail_only_that_plugin(tmp_path):
    extra = tmp_path / "personal"
    extra.mkdir()
    _copy_clock(extra, "oldclock", interface=2)
    _copy_clock(extra, "badclock")
    root = _root(tmp_path, {"paths": [str(extra)], "clock": {},
                            "oldclock": {}, "badclock": {"show_seconds": "yes"}}, ["clock", "badclock"])
    host = PluginHost(load_host_config(root), core_tools=CORE)
    assert host.plugins["clock"].status == "loaded"
    old = host.plugins["oldclock"]
    assert old.status == "failed" and "插件接口版本 2 与宿主接口版本 1 不一致" in old.error
    bad = host.plugins["badclock"]
    assert bad.status == "failed" and "plugins.badclock 配置不合法" in bad.error
    assert host.commands == {"时间": "clock"}
    assert "时间badclock" not in host.commands


def test_duplicate_or_missing_plugin_directories_are_reported(tmp_path):
    extra = tmp_path / "personal"
    extra.mkdir()
    shutil.copytree(BUILTIN / "clock", extra / "clock", ignore=shutil.ignore_patterns("__pycache__"))
    root = _root(tmp_path, {"paths": [str(extra), str(tmp_path / "missing")], "clock": {}, "absent": {}})
    host = PluginHost(load_host_config(root), core_tools=CORE)
    assert host.plugins["clock"].status == "failed" and "多个目录提供同名插件" in host.plugins["clock"].error
    assert "未在内置目录和 plugins.paths 中找到" in host.plugins["absent"].error
    assert host.discovery_errors == [f"插件目录不存在或不是目录：{tmp_path / 'missing'}"]


def test_manifest_name_must_match_directory(tmp_path):
    directory = _copy_clock(tmp_path, "renamed")
    (directory / "plugin.toml").write_text(
        (directory / "plugin.toml").read_text(encoding="utf-8").replace('name = "renamed"', 'name = "other"'),
        encoding="utf-8")
    with pytest.raises(ValueError, match="必须等于目录名"):
        read_manifest(directory)


@pytest.mark.parametrize("decorator", ['fullmatch("今日直播", "日历")', 'regex(r"查看 (?P<name>.+)", "查询")'])
def test_duplicate_message_rules_fail_plugin_configuration(tmp_path, decorator):
    extra = tmp_path / "personal"
    extra.mkdir()
    for name in ("first", "duplicate"):
        directory = _copy_clock(extra, name)
        (directory / "__init__.py").write_text(
            'from len_bot.next.plugin import Plugin, fullmatch, regex\n'
            f'class Rules(Plugin):\n    @{decorator}\n'
            '    async def handle(self, ctx, *args):\n        pass\n', encoding="utf-8")
    root = _root(tmp_path, {"paths": [str(extra)], "first": {}, "duplicate": {}}, ["first", "duplicate"])
    host = PluginHost(load_host_config(root), core_tools=CORE)
    assert host.plugins["first"].status == "loaded"
    assert host.plugins["duplicate"].status == "failed"
    assert "已注册" in host.plugins["duplicate"].error


@pytest.mark.asyncio
async def test_stopped_plugin_context_loses_host_capabilities(tmp_path):
    root = _root(tmp_path, {"clock": {}}, ["clock"])
    host = PluginHost(load_host_config(root), core_tools=CORE)
    ctx = host.plugins["clock"].context
    await host.close()
    for call in (lambda: ctx.get_kv("counter"), lambda: ctx.set_kv("counter", 1),
                 lambda: ctx.delete_kv("counter"), lambda: ctx.send("onebot:group:80001", "text"),
                 lambda: ctx.emit_event("onebot:group:80001", "event"),
                 lambda: ctx.memory("onebot:group:80001", {"action": "search", "query": "text"})):
        with pytest.raises(RuntimeError, match="未处于可运行状态"):
            await call()
    with pytest.raises(RuntimeError, match="未处于可运行状态"):
        ctx.recent_messages("onebot:group:80001")


@pytest.mark.asyncio
async def test_tool_reference_cannot_execute_in_disabled_scene(tmp_path):
    extra = tmp_path / "personal"
    extra.mkdir()
    directory = _copy_clock(extra, "scoped")
    (directory / "__init__.py").write_text(
        'from len_bot.next.plugin import Plugin, tool\n'
        'class Scoped(Plugin):\n    @tool("scoped_read", "读取")\n'
        '    async def read(self, ctx) -> str:\n        return "local"\n', encoding="utf-8")
    root = _root(tmp_path, {"paths": [str(extra)], "scoped": {}}, ["scoped"])
    host = PluginHost(load_host_config(root), core_tools=CORE)
    await host.start()
    reference = host.tools_for("onebot:group:80001")[0]
    try:
        with pytest.raises(PermissionError, match="未在场景"):
            await reference.call("onebot:private:80002", {})
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_local_harness_reports_enabled_scenes(tmp_path):
    from len_bot.next.plugin_testing import PluginTest

    directory = _copy_clock(tmp_path, "scenes")
    (directory / "__init__.py").write_text(
        'from len_bot.next.plugin import Plugin, command\n'
        'class Scenes(Plugin):\n    @command("场景", "列出")\n'
        '    async def list(self, ctx, args):\n        await ctx.reply(",".join(self.ctx.scenes))\n',
        encoding="utf-8")
    async with PluginTest(directory, config={"show_seconds": True}) as bot:
        assert await bot.message("/场景")
        assert bot.deliveries[-1].text == "onebot:group:80001"


@pytest.mark.parametrize(("plugins", "scene_plugins", "message"), [
    (None, ["clock"], "not configured under root plugins"),
    ({"clock": {}}, ["clock", "clock"], "must not repeat"),
    ({"Clock": {}}, [], "lowercase letters"),
    ({"clock": 1}, [], "must be an object"),
])
def test_plugin_configuration_is_checked(tmp_path, plugins, scene_plugins, message):
    root = _root(tmp_path, plugins, scene_plugins)
    with pytest.raises(ValueError, match=message):
        load_host_config(root)


def test_onebot_notice_samples_route_to_scenes():
    increase = {"time": 1790000000, "self_id": 90001, "post_type": "notice", "notice_type": "group_increase",
                "sub_type": "approve", "group_id": 80001, "operator_id": 0, "user_id": 70001}
    notice = parse_notice(increase)
    assert (notice.scene, notice.notice_type, notice.sub_type, notice.user_id, notice.operator_id) == (
        "onebot:group:80001", "group_increase", "approve", 'onebot:70001', "onebot:0")
    assert notice.raw is increase
    poke = {"time": 1790000001, "self_id": 90001, "post_type": "notice", "notice_type": "notify",
            "sub_type": "poke", "target_id": 90001, "user_id": 80002}
    assert parse_notice(poke).scene == "onebot:private:80002"
    assert parse_notice({"time": 1, "post_type": "notice", "notice_type": "client_status"}) is None
    with pytest.raises(ValueError, match="group_id is not a QQ number"):
        parse_notice({**increase, "group_id": "abc"})
    with pytest.raises(ValueError, match="lacks a text notice_type"):
        parse_notice({"time": 1, "post_type": "notice"})


@pytest.mark.asyncio
async def test_rss_scene_disable_keeps_other_scene_cron(tmp_path):
    root = _root(tmp_path, {"rss_broadcast": {"subscriptions": [{
        "name": "fixture", "url": "https://example.invalid/feed.xml",
        "scenes": ["onebot:group:80001", "onebot:private:80002"], "cron": "0 8 * * *", "timezone": "UTC",
    }]}}, ["rss_broadcast"])
    path = root / "lenbot.config.json"
    source = json.loads(path.read_text())
    source["scenes"]["onebot:private:80002"]["plugins"] = ["rss_broadcast"]
    path.write_text(json.dumps(source))
    host = PluginHost(load_host_config(root), core_tools=CORE)
    await host.start()
    try:
        assert len(host.state()["plugins"][0]["crons"]) == 2
        source["scenes"]["onebot:private:80002"]["plugins"] = []
        path.write_text(json.dumps(source))
        await host.reload("rss_broadcast", load_host_config(root))
        state = host.state()["plugins"][0]
        assert state["status"] == "running"
        assert state["scenes"] == ["onebot:group:80001"]
        assert [item["scene"] for item in state["crons"]] == ["onebot:group:80001"]
    finally:
        await host.close()
