# 插件开发

面向维护 LenBot 业务插件的开发者。公共导出在 [plugins/api.py](../src/len_bot/plugins/api.py)；执行和事实边界见[架构](architecture.md)，部署与配置操作见[运行手册](operations.md)。未实现的本轮目标只记在[当前任务](iteration.md)。

## 目录、描述符与配置

一个插件是一个 Python 包。内置包位于 `src/len_bot/plugins/builtin/`；本地插件位于根配置 `plugin_directories` 明确列出的目录下。宿主按根目录顺序、包目录名称顺序发现 `__init__.py` 的 `PLUGIN: PluginSpec`，重复 ID 报告双方路径。

`PluginSpec` 是唯一元数据，包含 ID、名称、版本、描述、全局 config_model、scene_config_model 和 `create(context)`。已有资源权限与类型在同一处声明；工具清单从实际 `register_tool` 生成。描述符导入只定义类型和入口，不能建立 HTTP 客户端、启动轮询或请求模型。参照[日历描述符](../src/len_bot/plugins/builtin/asoul_calendar/__init__.py)和[网页描述符](../src/len_bot/plugins/builtin/web_search/__init__.py)，不再编辑中央插件清单或配置类型映射。

根配置 `plugins.<id>` 明确保存 `enabled` 和 `config`。`config_model` 负责参数类型；可选 `validate_config(config, root)` 只做本地的公共时间、成员及容量关系校验。ConfigStore 在发现目录后解析一次专有参数，启动和面板保存使用同一入口。未配置的目录仍可展示元数据，但不建立插件实例或连接。

实现类继承 `BasePlugin`，构造时使用 `super().__init__(context.manifest)`；取得的 `context.config` 已通过该插件模型解析。插件文件和资源相对于 `context.directory`，运行数据需要时写入 `context.data_directory`。目录不自动创建空数据文件。项目依赖继续由 uv 管理，插件不自行安装依赖。

## 工具与读取

在 `on_load(context)` 调用 `register_tool`，提供名称、用途、明确的 Pydantic 参数模型、handler、read/proposal 类别和可用角色。低频工具可声明 deferred，并提供业务别名和关键词；实际执行名保持唯一。参数模型同时用于校验与 Schema，handler 接收已解析参数和本次 `PluginCallContext`。

调用字段包括当前场景、请求者、真实 source_event_id、时间、读取截点、episode/job、角色、工具调用 ID、PluginOrigin 和当前提案 Ledger。系统来源没有人类请求者，不伪造 user 身份。长期插件实例不保存可变的“当前群”。只读服务返回 `ToolResult`，暂存操作复用 Ledger；原资料与视觉覆盖、提交和送达的含义继续由架构规定。

工具 timeout 可由描述符的 `call_timeout(config)` 从实际配置读取，也可在注册时明确传入。工具定义和调用时均检查当前角色、场景与启用状态；工具名冲突会报告实际注册双方，不覆盖前者。

## 加载与停用

宿主创建实例并调用 `on_load` 建立资源与注册，再调用 `on_enable` 启动任务；插件不在 `on_load` 自行再次启用。`on_disable` 结束本插件活动，`on_unload` 释放资源。重复启用已启用实例不重复调用钩子；资源参数已变化时先正常释放旧实例，再按已解析的新参数加载。

加载或启用失败会释放已建立的资源并注销工具，保留发现的描述符及实际错误供面板查看。代码变更按正常停机升级处理；本轮不提供在线安装或代码热替换。验证方式遵循[工程约束](../AGENTS.md)。

## 消息处理与公共调用

在 on_load 中 register_handler，声明 id、description、match、handler、event_types、sources、priority、consume 和 require_to_me。ExactText、Command、RegexText 或本地同步函数返回 bool；数值优先级小者先匹配，同级按稳定注册顺序。冲突的独占精确命令在注册时报告双方，匹配不请求网络或模型。available(call) 检查插件自身的群配置；耗时校验使用 validate(call)。原话与归属先保存，耗时 handler 后执行，消费后失败不转普通聊天。

sources 默认 human；plugin_event 和 self_sent 要显式声明。self_sent 只认真实 MESSAGE_SENT，不认草稿、Shadow 或 unknown；同一插件自己的输出不会再次触发自己。引用关系在 call.event.metadata.quote_context 中读取，日历引用评论的消费规则见[日历实现](../src/len_bot/plugins/builtin/asoul_calendar/plugin.py)，核心不统一消费所有插件评论。

call.invoke_tool(name, typed_arguments) 复用注册服务和原观察存储，没有伪模型调用 ID；返回完整 ToolResult，调用者应按 status、coverage 与来源处理失败。call.save_image 保存当前场景素材，call.submit_message 接受 MessageSegment，经 Actor、Gate、队列提交并保存真实回执。提交与发送重新检查真实 source_event_id、已保存路由、版本、当前启用与 validate；全体提及还要求该 handler 明确提供当下有效的 allow_mention_all。

call.run_agent 的 result_only 模式接收插件指令、ToolResult 资料、明确工具名、既有 model_role、输出 Pydantic 模型和从插件根配置取得的步数、工具、上下文及输出额度；include_identity 明确选择是否带入现有身份。资料放在带类型的 user 投影中。插件选择 return_result 输出契约，结果返回后不自动发送；[直播实现](../src/len_bot/plugins/builtin/bilibili_live/plugin.py)随后明确提交一次邀请。模型仍使用 ProviderRegistry、ModelGateway、AgentLoop 与 model_calls。

自定义事件在 PluginSpec.event_models 声明名称和 payload 模型。context.emit_event(name, typed_payload, scene_id=..., event_id=..., timestamp=...) 发布 PLUGIN_EVENT；事件身份由插件的真实业务关系确定，不生成内容摘要去重。

context.scene_config(scene_id)、scene_configs()、members、time_settings、now() 提供只读公共输入；长期实例不读取私有 Runtime。on_enable 用 context.start_task(coroutine, name=...) 启动所属任务，停用由宿主取消并等待，on_unload 释放客户端。config_apply 默认 restart_plugin；仅实现 apply_config 的插件可显式声明 in_place。面板由实际工具、handler 与两种配置 Schema 生成，没有单独手写的业务清单。
