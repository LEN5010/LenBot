# ADR-0021: Plugin Manifest, Lifecycle Health & First Real Plugins

## Context

V2 计划 Phase 6(Plugin Runtime)。ADR-0016 已建立沙箱与权限模型,但核实发现:

- Manifest 缺少 §15.1 要求的 `capabilities/config_schema/default_config/emitted_events/registered_tools` 声明面;
- 生命周期只有 load/unload/flag-flip 式 enable/disable:无健康状态、无错误计数、`on_enable/on_disable` 钩子从未被调用;
- 无任何发现与装载机制——生产 `run_app` 不加载任何插件;
- 控制面是 `RESERVED_PLUGINS` 硬编码 mock(§二十九:"不要展示 mock plugin"),`bilibili_live_sensor` 只是假数据。

## Decision

### 1. Manifest 声明面补全

`PluginManifest` 增加 `config_schema`(驱动控制面配置表单)、`default_config`、`emitted_events`、`registered_tools`。

### 2. 生命周期健康(`PluginRuntimeStatus`)

每插件维护 state(loaded/enabled/disabled/error)、last_error、error_count、last_event_at、last_run_at。tool 执行、emit、enable/disable 路径全部更新;`on_enable/on_disable` 钩子真正接线(失败进 error 态)。`status_snapshot()` 是控制面唯一数据源——真实注册表,mock 条目删除。

### 3. 发现与装载

发现 = 显式内置注册表 `plugins/builtin/__init__.py: BUILTIN_PLUGINS`(V2 只交付两个真实插件,不做文件系统扫描/entry points——最短路径)。`AgentRuntime.start()` 以持久化(`plugins_state` dynamic config)的 config + enabled 实例化装载;`stop()` 卸载全部。`AgentRuntime.save_plugin_state()` 是持久化权威,控制面与测试共用。

### 4. 真实插件

- **BilibiliLiveSensor**(Sensor):轮询公共 API `api.live.bilibili.com/room/v1/Room/get_info`,live_status 0↔1 迁移发 `LIVE_STARTED/LIVE_ENDED`;`scene_id/room_ids/interval_seconds` 可配置且逐轮热读;未配置则惰性不动作;API 失败记 error 态并继续轮询。绝不直接通知——是否说话由条件 obligation 经 TASK_DUE 权威路径决定(ADR-0018)。
- **WebSearchToolPlugin**(Tool):`web_search`(DuckDuckGo HTML,免 key,uddg 链接解码)与 `read_page`(正文抽取截断)两个工具,经 PluginHost 沙箱执行;结果丰富时追加 `[COMPLEXITY: HIGH]`(ADR-0020 的生产者之一);网络失败以错误字符串返回(Invariant B / Goal 7 既有语义)。

## Consequences

- 正向:完成定义"至少一个 Sensor + 一个 Tool 真正工作"达成;Goal 5 的"开播叫我→自动 WAKE"全链路(传感器→事件→obligation→TASK_DUE→认知)可端到端测试。
- 取舍(接受):DuckDuckGo HTML 解析依赖页面结构(风险由沙箱错误字符串兜住,可后续替换后端);传感器无鉴权(公共只读数据);内置注册表需要改代码才能新增插件(第三方市场明确不做)。
