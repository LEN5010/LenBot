# S3 插件公共合同现状核对（只读）

- **核对 commit**：`ad41a5a71a4b5ec3693f4cc846342964c5b0b8c6`（分支 `feat/s0-product-contract`，`git rev-parse HEAD`；工作区 `git status --porcelain` 为空）
- **核对时间**：2026-09-21T23:21—23:31 +09:00
- **只读方式**：仅 `git`、`grep`、文件读取。未改业务文件、未提交、未运行测试/探针/回放/截图、未启动服务、未调用模型或平台、未读 `len_bot.db` 与媒体；本文件是唯一写入。
- **依据文本**：`docs/LenBot_分阶段任务卡_20260921.md:478-613`（S3-01…S3-06）、`docs/LenBot_成熟开源项目路线书_20260921.md:286-331`（第 6 节）、`docs/plugins.md`（按标题定位）。
- **一句话结论**：公共入口确实已集中在 `plugins/api.py` 的 28 个符号上，且工具声明/发现/执行三步齐备、Hook 有六阶段与类型化视图、生命周期有装载/启用/停用/卸载四段；但**没有公共 API 世代字段、没有兼容性判定、没有 deprecated 标记**（S3-01 的全部交付物在源码中为 0），PluginContext 仍直接暴露 `event_store`/`media_service`/`_runtime`，参考插件中 8 个文件绕过 `api.py` 直接 import 内核内部模块，`_runtime`/`_host`/`actor._queue` 等内部对象被插件直接触碰。

---

## 1. 逐项核对表

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| 公共 API 面（单文件导出） | 已实现 | `src/len_bot/plugins/api.py:1-18` | `from len_bot.plugins.base import BasePlugin, PluginContext`（`:2`）；`__all__` 分五批追加（`:13,14,15,16,17,18`），共 28 个符号 | 导入的 28 个符号与 `__all__` 完全一致（无「导入未导出」项）；但无版本常量、无稳定性/内部标记 |
| 公共 API 版本号 / 世代字段 | 未找到 | 全仓 `api_version` 只出现在两份计划文本：`docs/LenBot_分阶段任务卡_20260921.md:493`、`docs/LenBot_成熟开源项目路线书_20260921.md:309` | 源码内 `grep -rn "api_version" --include=*.py src/` 无命中 | S3-01 核心交付物；`PluginSpec`/`PluginManifest` 均无该字段（`catalog.py:21-38`、`models.py:148-161`） |
| 兼容性声明 / 装载前拒绝 | 未找到（仅有版本相等判定） | `src/len_bot/plugins/host.py:790-793`（`work_spec` 要求 `entry.spec.version != origin.plugin_version` 即拒绝）、`host.py:800-812`（`origin_issue`）、`host.py:249-288`（`validate_call` 比对 `plugin.manifest.version != origin.plugin_version`） | `if entry is None or entry.spec.version!=origin.plugin_version: raise ValueError('The plugin version responsible for this work is no longer installed')` | 这是**已保存工作/入口的原版本核对**，不是「公共 API 世代」兼容判定；`PluginSpec` 无 `api_version`、无兼容区间、无迁移说明 |
| deprecated 标记 | 未找到 | `grep -rn "deprecated\|Deprecated\|DEPRECATED" --include=*.py src/len_bot/` 无命中 | — | 现有「旧接口」只有描述性注释，例如 `python_workspace/__init__.py:1-6`「Compatibility ID for older local configuration」，不是机器可读标记 |
| PluginSpec（唯一描述符） | 已实现 | `src/len_bot/plugins/catalog.py:21-38` | 字段：`id,name,description,version,config_model,create,permissions,plugin_type,private_tools,call_timeout,validate_config,scene_config_model,validate_scene_config,event_models,config_apply,work` | 无 `api_version`；文档 `docs/plugins.md:9` 称其为「唯一元数据」，与源码一致 |
| 目录发现机制 | 已实现 | `src/len_bot/plugins/catalog.py:53-91` | `builtin = Path(__file__).parent/'builtin'`；`roots = [builtin.resolve(), *本地目录]`；按 `sorted(root.iterdir())` 找 `__init__.py`；本地目录以 `lenbot_local_{index}_{name}` 动态装载（`:74-82`） | 重复根报错 `:57-58`；重复 ID 报错双方路径 `:88-89`；目录名须为合法包名 `:67-68`；根配置变更需停机：`config_store.py:255-256` |
| 注册一个插件的最小必需提供物 | 已实现 | 入口文件 `__init__.py` + `PLUGIN: PluginSpec`（`catalog.py:83-87`）；实现类继承 `BasePlugin`（`base.py:177-200`）；`create(context)` 必须返回 `BasePlugin` 且 `plugin.manifest is manifest`（`host.py:394-396`） | `raise ValueError(f'{entry_file} must export PLUGIN: PluginSpec with a unique identifier')` | 生命周期函数全部有默认空实现，**非必需**（`base.py:183-193`）；`on_load` 实际承担注册职责（`docs/plugins.md:25`、`host.py:398`） |
| PluginContext 暴露面 | 已实现 | `src/len_bot/plugins/base.py:8-175` | 只读输入：`time_settings:17`、`members:22`、`now():26`、`bot_actor_id:29`、`scene_config():33`、`scene_enabled():38`、`scene_configs():41`；注册：`register_tool():131`、`register_handler():52`、`register_hook():49`、`start_task():45`；调用：`invoke_tool():61`、`submit_message():65`、`save_image():69`、`run_agent():75`、`stage_work():79`、`read_request_source():86`；事实：`emit_event():114`；路径：`data_directory:93`；权限：`has_permission():111` | `base.py:99` 注释「Built-in data readers use the store's scene-scoped query methods.」 | `event_store:97`、`media_service:102` 以 property 形式**公开给所有插件**，仅靠注释限定用途；`_runtime:11`/`_host:12` 为私有属性但同进程可读 |
| PluginCallContext 暴露面 | 已实现 | `src/len_bot/plugins/models.py:22-73` | 字段：`scene_id,requester_qq_uid,now,cutoff_rowid,episode_id,job_id,role(conversation/work),job_revision,ledger,work_operation,requester_qq_uids,tool_call_id,source_event_id,origin,entry_origin,entry(chat/handler/work),event,initiator,public_research`；方法：`invoke_tool:56`、`submit_message:59`、`save_image:62`、`run_agent:65`、`stage_work:68`、`read_request_source:72`；property `scene_config:52` | `models.py:43-46` 注释：`None` 表示未建立发起者，**不等于系统授权** | `ledger:34`、`origin:39`、`entry_origin:40`、`event:42`、`initiator:46` 都是内核类型直接出现在公共数据类上——文档口径（路线书第 6.1 节：新规划「不让第三方直接学完整 `EventStore`」）与现状不符 |
| 常用能力：读资料 | 已实现 | `call.invoke_tool`（`models.py:56` → `plugin_interactions.py:136-151`）、`call.read_request_source`（`base.py:86-91`）、`context.event_store`（`base.py:97`） | `invoke_tool` 拒绝提案工具：`if not toolkit.is_read_only(name): raise ValueError('invoke_tool accepts read tools; proposal tools use the active ledger')`（`plugin_interactions.py:142-143`） | 无「限定范围服务入口」抽象；插件直接拿到 `EventStore` 实例自行查询（`group_summary/plugin.py:100,122`） |
| 常用能力：调工具 / 调 Agent | 已实现 | `invoke_tool`（上）、`run_agent`（`base.py:75-77` → `plugin_interactions.py:323`）、`PluginAgentRequest`（`agent.py:28-52`） | 入口解析：`PluginAgentRequest` 校验 `output_tokens < context_tokens`、工具名唯一、`respond` 不得用 `materials` 投影（`agent.py:44-52`） | — |
| 常用能力：创建 works | 已实现 | `call.stage_work`（`models.py:68-70` → `base.py:79-84`） | 必须有活动 Ledger：`if (call.ledger is None or call.scene_id!=call.ledger.context.refs.scene_id or call.episode_id!=call.ledger.episode_id): raise ValueError('Work creation requires the active scene proposal ledger')`（`base.py:81-83`） | `stage_work` 直通 `ledger.stage_plugin_work`，Ledger 是内核内部类型 |
| 常用能力：提交表达 | 已实现 | `call.submit_message`（`models.py:59` → `plugin_interactions.py:154-194`） | 必须经 Actor/Gate：`actor.commit_turn(...)`、`runtime.runtime_gate.publish_committed(...)`（`:186-191`）；工作内不得直接发送：`if mailbox is None: raise ValueError('Background work returns results through its existing delivery; it cannot submit direct messages')`（`:159-160`） | 工具路径还要求提案工具 + 活动 Ledger（`:161-165`） |
| 内部实现泄漏（公共面） | 部分实现 | `base.py:97-100`（`event_store`）、`base.py:102-109`（`media_service`）、`base.py:11-12`（`_runtime`/`_host`）、`models.py:34`（`ledger`）、`models.py:49`（`execution`） | `media_service` 文档串：「The deployment's scoped media reader, for registered assets only.」 | 泄漏并非错误导出，而是**公共类上长期存在的内核引用**；无「稳定/内部」标记可依（S3-01 要求的「说明稳定和内部符号」未落地） |
| register_tool 真实签名 | 已实现 | `src/len_bot/plugins/base.py:131-175`（公共）；`src/len_bot/plugins/host.py:331-378`（宿主） | 位置参数：`name, description, parameter_model, handler`；仅关键字：`purpose, aliases=(), keywords=(), kind, ordered=False, roles, required_capabilities=(), side_effect='none', input_scope='current_scene', output_scope='current_scene', deferred=False, available=None, timeout_seconds=None, page_chars=None` | `kind`、`roles`、`purpose` 无默认值（必填）；`timeout_seconds=None` 时回落 `manifest.timeout_seconds`，两者都空则 `raise ValueError(... must provide a positive tool timeout)`（`base.py:156-158`） |
| 工具声明（第一步） | 已实现 | `models.py:163-186` `PluginToolDefinition` | 字段与 `register_tool` 一一对应，另含 `plugin_id` | 声明期即校验：权限 `REGISTER_TOOL`（`base.py:154-155`）、能力名合法性 `Capability(capability)`、账号写工具组合（`host.py:342-357`）、同名冲突报告双方（`host.py:358-364`） |
| 工具发现（第二步） | 已实现 | `host.py:869-881`（`get_tool_definitions`）、`host.py:836-861`（`search_tools`）、`tools/retrieval.py:434-456`（`tool_search` 工具） | `search_tools` 先过滤再排名：`candidates = [tool for name, tool in self._tools.items() if (allowed_names is None or name in allowed_names) and self.has_tool(name, call_context) and (kind is None or tool.kind == kind)]`，`matches.sort(...)` 在过滤之后（`host.py:839-857`） | 展开目录有额度：`retrieval.py:443-448`（`tool_discovery_limit`，超出弹出较早项） |
| 工具执行（第三步） | 已实现 | `host.py:883-967` | 执行前**再次**核资格：`if not self.has_tool(tool_name, call_context): return ToolResult.failure(..., "capability_denied", stage='availability', ...)`（`:890-892`）；无真实来源拒绝：`':894-896` | 失败分类：`not_found` / `capability_denied` / `invalid_source` / `timeout` / `request_failed` 等，均为 `ToolResult.failure` |
| 工具名过滤（allowed_tools / tool_names / 公共交集） | 已实现（三个入口各一层） | 显式插件 Agent：`cognition/social_core.py:127-131,143`（`toolkit.discovery_tool_names=frozenset(plugin_request.tool_names)`；`definitions()` 末行按 `tool_names` 过滤；不在集合内直接报错）；专用工作：`runtime/job_runner.py:903-914`（`discovery_names=set(work.allowed_tools)`）；公共研究：`job_runner.py:905-906`（与 `PUBLIC_WORK_TOOLS` 求交）、`runtime/public_research.py:6-20` | `return [item for item in available if item['function']['name'] in plugin_request.tool_names] if plugin_request else available`（`social_core.py:143`） | 能力说明投影也按同一集合过滤：`host.py:713-734`（`allowed_tool_names` 参数） |
| Hook 点数量与种类 | 已实现 | `src/len_bot/plugins/hooks.py:15-65` | `HookPhase = Literal['before_model','after_model','before_tool','after_tool','before_commit','after_delivery']`；`HookScope = Literal['own','conversation','work','scene']`；视图类型 6 个，`extra='forbid', strict=True`（`:19-21`） | 视图可写字段：`BeforeModel.instructions/materials/tool_names`、`AfterModel.candidates`、`BeforeTool.name/arguments`、`AfterTool.notes/view`、`BeforeCommit.messages`、`AfterDelivery.receipt`（只读校验） |
| Hook 顺序与作用域 | 已实现 | `hooks.py:90-118`、`host.py:103-112` | `for hook in sorted(self._hooks.values(), key=lambda item: (item.priority, item.order))`（`host.py:105`）；`scope='scene'` 或 `own` 且 owner 匹配，或 `scope == call.role and call.entry != 'handler'`（`host.py:110-111`） | 注册期校验未知 phase/scope 与重复 ID：`host.py:95-101` |
| Hook 不可改写的边界 | 已实现 | `hooks.py:123-125`、`:139-140`、`:147-148`、`:157-158`、`:168-169`、`:178-179` | `before_model may only select currently allowed tools, retaining the terminal`；`after_model must preserve actual call identities, names and order`；`before_tool cannot substitute another tool`；`after_tool cannot rewrite the original observation or operation receipt`；`before_commit may change segments, but cannot add or remove message ownership`；`after_delivery cannot rewrite the saved receipt` | 与路线书 6.4「Hook 不能改写原话、授权、读取资格或回执」逐条对应 |
| Hook 错误暴露行为 | 已实现（抛出，记录） | `hooks.py:113-117` | `except Exception as error: record.update(error=str(error)); if record['state'] != 'stopped': record['state'] = 'failed'; raise` | 不吞；`after_delivery` 是例外：`host.py:132-134` 捕获后仅 `record_plugin_error`，因为它在后台任务里 |
| Hook stop 语义 | 已实现 | `hooks.py:110-112`、`hooks.py:82-83`、`cognition/agent_loop.py:227-235` | `raise PluginHookStopped(f'{hook.plugin_id}/{hook.id}: {view.stop_reason}')`；`before_tool` 停在循环里转成真实失败观察：`stopped = ToolResult.failure(str(error), 'plugin_stopped', stage='execution')` | 仅 `before_tool` 有转换分支；其余阶段的 stop 如何向上呈现未在本次只读中逐处确认（**未确认**） |
| 加载语义 | 已实现 | `host.py:380-407` | `spec.create(context)` → `await plugin.on_load(context)`；`PluginContext` 注入 `runtime`/`host`/`entry`/`parsed_config`（`base.py:9-15`） | 失败时 `record_plugin_error` + `_release_plugin` 后 `raise`（`host.py:403-407`）；参数缺失则 `raise ValueError(f'Plugin {plugin_id!r} has no configured parameters')`（`:385-386`） |
| 启用语义 | 已实现 | `host.py:441-462` | 配置变化先 `unload_plugin`（`:449-450`）；已启用直接返回，不重复调钩子（`:453-454`）；`on_enable` 失败回滚 `manifest.enabled=False` 并释放（`:457-461`） | 与 `docs/plugins.md:75` 一致 |
| 停用语义 | 已实现 | `host.py:464-476`、`host.py:478-484` | `disable_plugin` → `stop_scene_work(plugin_id)` → `plugin.on_disable()`；`stop_scene_work` 取消工作、取消任务、切断等待与提醒（`job_runner.stop_plugin`、`_cancel_tasks`、`interrupt_plugin_waits_and_reminders`） | 停用不删事实：`stop_scene_work` 命中时写 `plugin_lifecycle` trace（`:482-484`） |
| 卸载语义 | 已实现 | `host.py:409-423`、`host.py:425-434`、`host.py:434-439` | `_release_plugin`：`_cancel_tasks` → `on_unload`（异常仅记录）→ 清理 `_plugin_contexts`、`_tools`、`_handlers`、`_hooks` | `unload_all` 逐个容错（`:434-439`），单个失败不阻断停机 |
| 取消语义 | 已实现 | `host.py:945-951` | `except WorkspaceCancelled as error: with suppress(asyncio.CancelledError): await task; raise` | 注释明确「A real cancellation is not a tool failure」；`asyncio.CancelledError` 也沿 `dispatch_handler` 重抛（`plugin_interactions.py:106-109`，`audit['state']='interrupted'`） |
| 参考插件：读取 + 确定性命令 | 已实现 | `local_plugins/local_clock/__init__.py:47-71` | 一个文件内含 `ClockConfig:11`、`ClockSceneConfig:27`、读取工具 `local_time_now:48`、「现在几点」`ExactText` handler `:51-53,66-71`；无模型调用 | 唯一**完全只走 `api.py`** 的插件：`local_clock/__init__.py:8` 单行 import `len_bot.plugins.api` |
| 参考插件：共用读取服务 + 图片提交 | 已实现 | `src/len_bot/plugins/builtin/asoul_calendar/plugin.py:65-77,160-177` | `register_tool(name="get_live_schedule", ...)`；`on_command` 调 `call.invoke_tool('get_live_schedule', request)`、`call.save_image`、`call.submit_message`；`deterministic_read_only=True` 声明睡眠豁免（`:75`） | 但绕过 `api.py`：`plugin.py:11-14` 直接 import `base`/`models`/`media.models`/`tools.results` |
| 参考插件：业务事件 + Agent 表达 | 已实现 | `src/len_bot/plugins/builtin/bilibili_live/plugin.py:51-89,142-160` | `register_handler(..., sources=('plugin_event',))`（`:61-71`）；`emit_event` 在轮询里（`:232-233`）；`call.run_agent(..., output_mode='result_only', output_model=Invitation)`（`:147-153`）；`register_hook('before_model'/'before_commit')`（`:72-73`） | 有内部泄漏，见第 3 节 |
| 参考插件：后台工作 + 产物 | 已实现（内部依赖最重） | `src/len_bot/plugins/builtin/group_summary/work.py:165-172`（`WORK=PluginWorkSpec(...)`，含 `execute=execute:150`、`needs_model:157`）、`analysis.py:10`、`plugin.py:63-125` | `PluginWorkSpec` 提供 `parameters_model/revision_model/progress_model/allow_learning/allowed_tools/input_cutoff/revise/new_progress/adopt_reads/finalize/continuation/project_progress`（`plugins/work.py:58-73`） | 直连内核：`analysis.py:8-9` `from len_bot.cognition.budget import count_remaining`、`from len_bot.cognition.jobs import JobBudgetExhausted, JobChanged`；`analysis.py:34,92,124,215,239,253,266` 经 `context.call.plugin.event_store` 反向穿透到 PluginContext |
| 发布与 Schema 表单 | 已实现 | `host.py:618-654`（`status_snapshot` 生成 `config_schema`/`scene_config_schema`）、`web/routes/plugins.py:34-37,40-58`、`web/query_service.py:1634-1660`、前端 `frontend/src/components/PluginConfigFields.vue:3-12,47-90`、`lib/pluginConfig.js:53-71` | `'config_schema': spec.config_model.model_json_schema()`（`host.py:641`）；三个表单扩展键 `x-lenbot-exclusive`/`x-lenbot-enum-labels`/`x-lenbot-list-choices` 仅在 `lib/pluginConfig.js` 与配置模型 `json_schema_extra` 中出现 | 面板保存只写根配置并原位应用：`host.py:596-616`（`restart_plugin` 默认，`in_place` 需插件实现 `apply_config`） |
| capability_status | 已实现（展示分组，非第二权限表） | `src/len_bot/web/capability_status.py:16-48`（`CARDS` 十组）、`:51-256` | 模块头注释：「The catalogue below is display grouping, never another permission registry. Execution still uses PluginHost, ScenePolicy and CapabilityAuthority.」（`:1-5`） | 实际可用性来自 `runtime.plugin_host.capability_facts(call)`（`:59`）；缺项文案映射 `:71-79` |
| builtin 文档 | 部分实现 | `src/len_bot/plugins/builtin/asoul_calendar/SOURCE.md`、`asoul_dynamics/SOURCE.md`、`group_summary/SOURCE.md`、`gscore_adapter/SOURCE.md` | `docs/operations.md:283` 引用 `asoul_calendar/SOURCE.md`；`docs/architecture.md:345` 引用 `gscore_adapter` 的 `SOURCE.md` | 只有 4/13 个内置插件有 `SOURCE.md`；**没有任何 builtin README**（`find … -name "README*.md"` 仅命中 `local_plugins/local_clock/README.md`）；任务卡 S3-06 要求的「内置插件兼容清单和插件作者迁移记录」不存在 |

---

## 2. 公共 API 实际清单（`src/len_bot/plugins/api.py`）

`api.py` 共 18 行，导入 28 个符号并在 `:13-18` 全部写入 `__all__`。下表「精确行」指该类/函数的定义位置，`api.py 行` 指导出语句行号。

| # | 符号 | 类别 | 定义位置（文件:行号） | api.py 行 | 本仓插件实际使用（引用文件数，含内置 13 + local_clock） |
|---|---|---|---|---|---|
| 1 | `BasePlugin` | 类 | `plugins/base.py:177` | 2 / 13 | 14 |
| 2 | `PluginContext` | 类 | `plugins/base.py:8` | 2 / 13 | 12 |
| 3 | `PluginSpec` | 类（dataclass） | `plugins/catalog.py:21` | 3 / 13 | 14 |
| 4 | `PluginCallContext` | 类（dataclass） | `plugins/models.py:22` | 4 / 13 | 12 |
| 5 | `PluginPermission` | 枚举（StrEnum） | `plugins/models.py:138` | 4 / 13 | 14 |
| 6 | `PluginType` | 枚举（StrEnum） | `plugins/models.py:142` | 4 / 13 | 14 |
| 7 | `ToolResult` | 类 | `tools/results.py:81` | 11 / 14 | 18 |
| 8 | `ToolSource` | 类 | `tools/results.py:47` | 11 / 14 | 13 |
| 9 | `ToolNextCall` | 类 | `tools/results.py:62` | 11 / 14 | 5 |
| 10 | `Command` | 匹配器（dataclass） | `plugins/models.py:85` | 15 | **0** |
| 11 | `EmptySceneConfig` | 类 | `plugins/models.py:17` | 15 | 1（仅 local_clock） |
| 12 | `ExactText` | 匹配器（dataclass） | `plugins/models.py:77` | 15 | 2 |
| 13 | `RegexText` | 匹配器（dataclass） | `plugins/models.py:94` | 15 | **0** |
| 14 | `EventType` | 枚举 | `events/models.py`（经 `:5` 导入） | 15 | 3 |
| 15 | `PluginOrigin` | 类 | `events/models.py`（经 `:5` 导入） | 15 | **0** |
| 16 | `MessageSegment` | 类 | `media/models.py`（经 `:6` 导入） | 15 | 6 |
| 17 | `BeforeModel` | Hook 视图 | `plugins/hooks.py:24` | 7 / 16 | **0**（插件按字符串 `'before_model'` 注册，不 import 视图类型） |
| 18 | `AfterModel` | Hook 视图 | `plugins/hooks.py:37` | 7 / 16 | **0** |
| 19 | `BeforeTool` | Hook 视图 | `plugins/hooks.py:41` | 7 / 16 | **0** |
| 20 | `AfterTool` | Hook 视图 | `plugins/hooks.py:46` | 7 / 16 | **0** |
| 21 | `BeforeCommit` | Hook 视图 | `plugins/hooks.py:55` | 7 / 16 | **0** |
| 22 | `AfterDelivery` | Hook 视图 | `plugins/hooks.py:59` | 7 / 16 | **0** |
| 23 | `PluginAgentRequest` | 类 | `plugins/agent.py:28` | 17 | **0** |
| 24 | `PluginWorkSpec` | 类（dataclass） | `plugins/work.py:58` | 18 | 1（group_summary） |
| 25 | `PluginWorkRevision` | 类（dataclass） | `plugins/work.py:52` | 18 | 1（group_summary） |
| 26 | `PluginWorkContext` | 类（dataclass） | `plugins/work.py:16` | 18 | 0（但为 `PluginWorkSpec.execute` 注解类型，`work.py:73`） |
| 27 | `JobResult` | 类 | `cognition/jobs.py`（经 `:10` 导入） | 18 | 1 |
| 28 | `PreparedWorkDelivery` | 类 | `cognition/jobs.py`（经 `:10` 导入） | 18 | 1 |

补充事实：

- `api.py` 的 28 个导入符号与 `__all__` 逐个对齐（`python3` 对照脚本：`imported not in __all__` 为空集），**不存在导入但未导出的符号**。
- **导出但全仓零消费**：`Command:85`、`RegexText:94`、`PluginOrigin`、6 个 Hook 视图类、`PluginAgentRequest`、`PluginWorkContext`——均为 0 处使用（Hook 视图被宿主内部 `hooks.py:103-105` 使用，只是插件侧不 import）。
- **api.py 无版本常量**：文件内没有 `__version__`、`API_VERSION`、`api_version` 或任何兼容性数据结构；`docs/plugins.md:3` 也只把 `plugins/api.py` 作为「导出见」链接，未声明世代。
- **`api.py` 自身是纯再导出**：`api.py:1` docstring「Public plugin authoring entry point; importing it does not start resources.」——导入无副作用，符合 `docs/plugins.md:9` 对描述符的要求。

---

## 3. 内部泄漏点

### 3.1 参考插件绕过 `api.py` 直接 import 内核模块

`api.py` 已导出 `BasePlugin`、`PluginContext`、`PluginCallContext`、`ExactText`、`ToolResult`、`ToolSource`、`ToolNextCall`，但下列 8 个内置插件文件仍从内部路径导入同一批符号（等价符号已在 `api.py` 导出）：

| 文件:行号 | 实际 import | 该符号本可从 `api.py` 取得 |
|---|---|---|
| `plugins/builtin/asoul_calendar/plugin.py:11` | `from len_bot.plugins.base import BasePlugin, PluginContext` | `api.py:2` |
| `plugins/builtin/asoul_calendar/plugin.py:12` | `from len_bot.plugins.models import ExactText, PluginCallContext` | `api.py:4` |
| `plugins/builtin/asoul_dynamics/plugin.py:16-17` | `from len_bot.plugins.base import BasePlugin, PluginContext` / `from len_bot.plugins.models import PluginCallContext` | `api.py:2,4` |
| `plugins/builtin/bilibili_content/plugin.py:16,21` | `from len_bot.plugins.base import BasePlugin, PluginContext` / `from len_bot.plugins.models import PluginCallContext` | `api.py:2,4` |
| `plugins/builtin/bilibili_live/plugin.py:13` | `from len_bot.plugins.base import BasePlugin, PluginContext` | `api.py:2` |
| `plugins/builtin/gscore_adapter/plugin_core.py:12-13` | `from len_bot.plugins.base import ...` / `from len_bot.plugins.models import PluginCallContext` | `api.py:2,4` |
| `plugins/builtin/interest_share/plugin.py:9` | `from len_bot.plugins.base import BasePlugin` | `api.py:2` |
| `plugins/builtin/link_parser/plugin.py:5-6` | `from len_bot.plugins.base import BasePlugin, PluginContext` / `from len_bot.plugins.models import PluginCallContext` | `api.py:2,4` |
| `plugins/builtin/web_search/plugin.py:16,18` | `from len_bot.plugins.base import BasePlugin, PluginContext` / `from len_bot.plugins.models import PluginCallContext` | `api.py:2,4` |
| `plugins/builtin/group_summary/service.py:11` | `from len_bot.plugins.models import PluginCallContext` | `api.py:4` |
| `plugins/builtin/group_summary/work.py:9` | `from len_bot.plugins.work import PluginWorkSpec, PluginWorkRevision` | `api.py:18` |
| `plugins/builtin/browser_agent/plugin_core.py:12-13` | `from len_bot.plugins.api import ...`（**正面例**）+ `from len_bot.browser.*`、`from len_bot.execution.*` | 后者为内核模块 |
| `local_plugins/local_clock/__init__.py:8` | `from len_bot.plugins.api import ...`（**唯一完全合规的参考插件**） | — |

### 3.2 参考插件/公共上下文直接依赖内核内部对象

| 文件:行号 | 触达的内部对象 | 性质 |
|---|---|---|
| `plugins/base.py:11-12` | `self._runtime`、`self._host` | 下划线私有，但同进程插件可读 |
| `plugins/builtin/bilibili_live/plugin.py:120` | `self.context._runtime.scene_manager.get_or_create_actor(...)` | 绕过 Actor 公共入口 |
| `plugins/builtin/bilibili_live/plugin.py:137` | `await actor._queue.join()` | 触达 Actor 私有队列 |
| `plugins/builtin/bilibili_live/plugin.py:121,138,230` | `self.context.event_store.events_by_ids/event_exists` | 直接用 `EventStore` |
| `plugins/builtin/group_summary/plugin.py:68,100,122` | `context.event_store` / `self.context.event_store`（`list_jobs`、`events_by_ids`） | 插件自行查工作表与事件表 |
| `plugins/builtin/group_summary/analysis.py:8-9` | `from len_bot.cognition.budget import count_remaining`、`from len_bot.cognition.jobs import JobBudgetExhausted, JobChanged` | 工作执行器内部异常类型进入插件 |
| `plugins/builtin/group_summary/analysis.py:34,92,124,215,239,253,266` | `context.call.plugin.event_store` / `call.plugin.config` / `call.plugin.now()` / `call.plugin.directory` | 经 `PluginCallContext.plugin` 反向穿透到 PluginContext 内部 |
| `plugins/builtin/interest_share/plugin.py:41-43` | `self.context.event_store.get_pending_tasks()` + `self.context._runtime.scheduler.cancel_task(...)` | 直接操作 Scheduler |
| `plugins/builtin/interest_share/plugin.py:57,87,138,163-171` | `self.context._runtime`、`self.context.event_store._db.execute("SELECT ...")` | **直连数据库连接**执行裸 SQL |
| `plugins/builtin/interest_share/plugin.py:95-96` | `from len_bot.runtime.gate import MAX_CONSECUTIVE_BOT_MESSAGES` | Gate 常量进入插件 |
| `plugins/builtin/browser_agent/plugin_core.py:24,58` | `context._runtime.config_store.current` | 读取根配置内部结构 |
| `plugins/builtin/browser_agent/plugin_core.py:70,81` | `require_execution_job(self.context.event_store, call, ...)` | 内核准入函数直接调用 |
| `plugins/builtin/gscore_adapter/plugin_core.py:102,103,122,186` | `self.context._host.validate_call` / `self.context._runtime.config.bot_qq` | 私有属性 |
| `plugins/builtin/workspace/plugin.py:45-46,48,73` | `context.data_directory`、`context.event_store`、`context.media_service`、`context._runtime.action_reviewer`、`self.context._runtime.file_assets.prepare(...)` | 公共 `event_store`/`media_service` + 私有 `_runtime` 混用 |
| `plugins/builtin/bilibili_content/account.py:82,102` | `context._runtime`、`context._host.validate_call` | 私有属性 |
| `plugins/builtin/bilibili_content/plugin.py:20` | `from len_bot.runtime.capabilities import Capability` | 内核能力枚举进入插件 |
| `plugins/builtin/media_analysis/plugin.py:3-5` | `from len_bot.media.segment_protocol/segment_service/transcription` | 插件直接构造内核服务 |

**规模**：`grep -rn "context\._runtime\|context\._host\|self\._runtime\|actor\._queue\|event_store\._" src/len_bot/plugins/builtin/ local_plugins/` 在 8 个文件命中 25 处。

### 3.3 跨插件 import（非内核，但同属「第二套依赖」）

| 文件:行号 | import | 说明 |
|---|---|---|
| `plugins/builtin/python_workspace/__init__.py:11,15`、`plugin.py:2` | `from ..workspace.config import WorkspacePluginConfig`、`from ..workspace.plugin import WorkspacePlugin` | 兼容 ID 复用同实现（注释见 `python_workspace/__init__.py:1-6`） |
| `plugins/builtin/media_analysis/__init__.py:3` | `from ..workspace.config import configured_workspace` | 依赖 workspace 插件内部函数 |
| `plugins/builtin/browser_agent/plugin_core.py:16` | `from ..workspace.config import configured_workspace` | 同上 |
| `plugins/builtin/bilibili_content/plugin.py:23`、`link_parser/plugin.py:11` | `from ..bilibili_client import public_json` / `video_view, first_play_url` | 依赖 `builtin/bilibili_client.py`（非包） |

---

## 4. 未确认与读不到

1. **Hook stop 在六个阶段各自的最终呈现**：仅确认 `before_tool` 在 `agent_loop.py:233-235` 被转成 `ToolResult.failure(..., 'plugin_stopped', ...)`；`before_model`/`after_model`/`before_commit`/`after_delivery` 的 `PluginHookStopped` 由谁捕获、如何落 Trace，本次未逐个追到（**未确认**）。
2. **`api.py` 是否为唯一「公共」约定**：源码中无任何标记区分「稳定符号」与「内部符号」（无 `__all__` 之外的白名单、无 `_internal`、无 docstring 承诺）。「哪些算公共」只能靠 `api.py:13-18` 反推（判定依据为静态推断）。
3. **`PluginManifest` 是否属于公共面**：`models.py:148-161` 的 `PluginManifest` 未出现在 `api.py`，但插件构造时经 `context.manifest` 使用（`base.py:10,15`；`docs/plugins.md:15` 明确「`super().__init__(context.manifest)`」）。它的字段可被插件读取，却不在导出清单内（**未确认是否算内部**）。
4. **`scene_config_model` 与 `PluginSpec.scene_config_model` 的公共契约文本**：`EmptySceneConfig` 已导出，但 `models.py:17-18` 只有 `extra='forbid', frozen=True`，无字段。
5. **`PluginWorkContext` 的公共地位**：它已在 `api.py:18` 导出（无遗漏），但全仓无任何插件使用它，其对 `execute` 注解的必要性只由 `plugins/work.py:73` 体现（**未确认**第三方是否需要显式引用该类型）。
6. **面板 Schema 扩展键是否对第三方插件开放**：三个键只在 `lib/pluginConfig.js` 与内置配置模型中观测到，未在 `docs/plugins.md` 之外找到面向第三方的字段说明位置（`docs/plugins.md:13` 是唯一描述，属内部文档而非公共 API 页面）。
7. **`capability_status` 的 `CARDS` 是否为第三方插件必须登记**：源码注释（`web/capability_status.py:1-5`）与 `docs/plugins.md:21` 都说「不要求外部插件加入固定能力卡才能配置」，但 `CARDS:16-48` 是硬编码元组，未装入 CARDS 的插件在该页不出卡（**未确认对第三方的影响**）。
8. **`docs/plan/s0-01-remaining-work-index.md`、`s0-01-s1/s2/s4-s7` 等兄弟报告**：`docs/plan/` 目录本次只存在 `README.md` 与 `delegation-plan.md`（`ls -la docs/plan/`），README 第 91-98 行列出的六份「S0 前期底稿」文件均**不存在**（**读不到**，非本次范围，仅记录目录事实）。
9. **编译/运行证据**：本次为纯源码核对，未运行 `compileall`、未启动服务，所有「已实现」仅表示源码存在对应符号与调用点，**不表示已运行通过**。

---

## 5. 与任务卡原假设的差异

| 任务卡/路线书原文（行号） | 原假设 | 现状 | 差异性质 |
|---|---|---|---|
| 任务卡 S3-01「增加明确公共 API 世代字段（建议名 `api_version`）」`分阶段任务卡:493` | 需要新增世代字段 | 源码中不存在 `api_version`；`PluginSpec` 只有插件自身 `version`（`catalog.py:26`） | **未实现的待办**，不是已有能力 |
| 任务卡 S3-01「不兼容在装载前明确拒绝」`分阶段任务卡:497` | 装载前应做世代兼容判定 | 现有拒绝只针对**已保存工作/入口的原版本**（`host.py:790-793,800-812`），装载本身不检查世代（`host.py:380-407` 只查 `parsed_config is None`） | 现有机制用途不同，不能当作 S3-01 已实现 |
| 任务卡 S3-01「说明稳定和内部符号」`分阶段任务卡:493` | 需区分稳定/内部 | `api.py:13-18` 有 `__all__`，但无任何稳定/内部标记；插件实际混用内部路径（第 3.1 节 12 处） | **部分实现**（只有导出清单，无稳定性声明） |
| 路线书 6.1「新规划不是让第三方直接学 `Runtime`、`ProposalLedger` 和完整 `EventStore`」`路线书:295` | 新规划应避免暴露内核 | `PluginContext.event_store`（`base.py:97`）、`PluginCallContext.ledger`（`models.py:34`）、`_runtime`（`base.py:11`）均可达；`interest_share` 甚至直连 `event_store._db`（`plugin.py:163`） | **现状与规划目标不一致**（规划尚未实施） |
| 任务卡 S3-02「常见读取提供限定范围的服务入口」`分阶段任务卡:520` | 应有范围限定服务 | 只有 `context.members`/`time_settings`/`scene_config`/`scene_configs`/`now()`（`base.py:17-43`）是限定投影；资料读取仍走 `invoke_tool` 或裸 `EventStore` | **部分实现** |
| 任务卡 S3-03「发现先过滤再排名，执行时再核资格」`分阶段任务卡:545` | 三步语义 | **已实现**：`host.py:839-857`（先过滤后排名）、`host.py:890-892`（执行再核） | 原假设成立，无需整改 |
| 任务卡 S3-04「描述符无运行副作用」`分阶段任务卡:570` | 描述符导入零副作用 | **已实现**：`__init__.py` 延迟 import 实现类（如 `asoul_calendar/__init__.py:5-7`、`web_search/__init__.py:5-7`），`api.py:1` 声明导入不启动资源 | 原假设成立；例外：`group_summary/__init__.py:3` 直接 `from .work import WORK`（仅类型定义，无 I/O） |
| 任务卡 S3-05「三种真正可用的参考插件」`分阶段任务卡:582` | 需三条教程 | 源码层三条路径都可指认（local_clock / asoul_calendar / bilibili_live / group_summary），但**只有 `local_clock` 完全只依赖 `api.py`**；教程文档只有 `local_plugins/local_clock/README.md:1-15` 一份 | **部分实现**：可指认，未成文；且「全部只依赖公开入口」的验收线仅 1/4 满足 |
| 任务卡 S3-06「内置插件兼容清单和插件作者迁移记录」`分阶段任务卡:594` | 需兼容清单 | 不存在该清单；builtin 仅 4 份 `SOURCE.md`，无 README | **未实现** |
| 路线书 6.2 契约表「描述符与版本：装载前明确兼容判断」`路线书:300` | 宿主承诺 | 装载前无世代判断（同上） | **未实现的待办** |
| 路线书 6.4「错误不静默吞掉后转入另一套默认聊天」`路线书:325` | 错误应暴露 | **已实现**：`hooks.py:113-117` 抛出；`host.py:110-112` 对 handler 记 audit 后**不再抛出**（`dispatch_handler` 里 `except Exception` 只 `record_plugin_error`） | 需注意：handler 异常被记录但不向调用者抛，与 Hook 行为不同 |
| 路线书 6.5「当前同进程 Python 插件按运营者信任的代码处理」`路线书:329` | 非安全沙箱 | 与源码一致：`docs/plugins.md:21` 与源码都未声明沙箱 | 原假设成立 |

---

## 6. 反向引用清单

**公共 API 与描述符**
- `src/len_bot/plugins/api.py:1,2,3,4,5,6,7,8,9,10,11,13-18`
- `src/len_bot/plugins/catalog.py:21-38,41-45,48-51,53-91`
- `src/len_bot/plugins/models.py:17,21-50,52-73,77,85,94,106,138,142,148,163`
- `src/len_bot/plugins/base.py:8-15,17-43,45-59,61-91,93-109,111-129,131-175,177-200`

**工具三步与过滤**
- `src/len_bot/plugins/host.py:331-378,683-704,713-769,771-783,836-861,862-867,869-881,883-967`
- `src/len_bot/runtime/plugin_interactions.py:136-151,154-194`
- `src/len_bot/cognition/social_core.py:112-143,384-385`
- `src/len_bot/runtime/job_runner.py:866-878,903-914`
- `src/len_bot/tools/retrieval.py:289-290,339-342,434-456`
- `src/len_bot/runtime/public_research.py:6,19,20`
- `src/len_bot/runtime/capabilities.py:37-49`

**Hook 与生命周期**
- `src/len_bot/plugins/hooks.py:15,16,19-65,68-79,82-83,86-118,120-179`
- `src/len_bot/plugins/host.py:90-114,117-140,141-165,166-192,194-247,249-288,289-297,298-322,325-329,380-407,409-423,425-439,441-462,464-484,596-616,618-654`
- `src/len_bot/cognition/agent_loop.py:227-235,345,441,505`

**工作合同**
- `src/len_bot/plugins/work.py:16-49,52-55,58-74`
- `src/len_bot/plugins/agent.py:20-25,28-52,55-69`
- `src/len_bot/plugins/builtin/group_summary/work.py:150-172`
- `src/len_bot/plugins/builtin/group_summary/analysis.py:8,9,10,34,92,118,124,199-200,215,239,253,266,276`

**参考插件**
- `local_plugins/local_clock/__init__.py:8,11,27,40-56,58-81,84-93`；`local_plugins/local_clock/README.md:1-15`
- `src/len_bot/plugins/builtin/asoul_calendar/__init__.py:1-20`；`plugin.py:11-14,23,43-45,60-85,122,159,160-177`
- `src/len_bot/plugins/builtin/bilibili_live/__init__.py:1-32`；`plugin.py:9-18,51-89,104-140,142-160,194-240,253-299`
- `src/len_bot/plugins/builtin/group_summary/__init__.py:1-22`；`plugin.py:12,63-125`
- `src/len_bot/plugins/builtin/interest_share/__init__.py:1-19`；`plugin.py:1-14,35-49,57,70,87,95-96,126,138,142-171,198`
- `src/len_bot/plugins/builtin/workspace/plugin.py:5-9,10,40-48,68-73`；`__init__.py:1`
- `src/len_bot/plugins/builtin/python_workspace/__init__.py:1-21`；`plugin.py:2`
- `src/len_bot/plugins/builtin/browser_agent/plugin_core.py:6-16,24,58,70,81`
- `src/len_bot/plugins/builtin/gscore_adapter/plugin_core.py:9-15,48,58,79,94,102-103,122,136,140,186,269`
- `src/len_bot/plugins/builtin/bilibili_content/plugin.py:11,16,20,21,22,23`；`account.py:11,12,82,102`；`actions.py:12,13,14`
- `src/len_bot/plugins/builtin/media_analysis/__init__.py:1-24`；`plugin.py:3,4,5`
- `src/len_bot/plugins/builtin/link_parser/plugin.py:5-11,25-34,122-134`
- `src/len_bot/plugins/builtin/web_search/__init__.py:1-14`；`plugin.py:16,18,19,20,21,22`
- `src/len_bot/plugins/builtin/asoul_dynamics/plugin.py:13-18,28,42,73,77,233-234`

**发布、Schema、能力状态**
- `src/len_bot/web/routes/plugins.py:1-58`
- `src/len_bot/web/query_service.py:1613-1660`
- `src/len_bot/web/capability_status.py:1-5,16-48,51-59,71-79,163-174,175-214,253-256`
- `src/len_bot/web/group_quick.py:13,84`
- `src/len_bot/web/frontend/src/components/PluginConfigFields.vue:3,5,11,12,15,47-90`
- `src/len_bot/web/frontend/src/lib/pluginConfig.js:53,60,71`
- `src/len_bot/config_store.py:112-120,149-158,175-178,183-229,255-256,268-270`
- `lenbot.config.json:1126-1128`

**文档与计划依据**
- `docs/LenBot_分阶段任务卡_20260921.md:478-613`（S3-01…S3-06）
- `docs/LenBot_成熟开源项目路线书_20260921.md:286-331`（第 6 节）
- `docs/plugins.md:1,3,5,7,9,11,13,15,17,19,21,23,25,27,29,31,33,35,39,41,43,45,47,53,55,57,59,71,73,75,77,79,81,83,85,87,89,91,93,95,97,101,105,107,109,111,113,115,117,119,121,123,125,127,129,131,133-141,142,144,146,148,150`
- `docs/plan/README.md:28-58,84-98`
- `docs/plan/delegation-plan.md:24-46`
- `docs/operations.md:39,283`
- `docs/architecture.md:345`
