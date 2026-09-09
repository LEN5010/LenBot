# 插件开发

面向维护 LenBot 业务插件的开发者。公共导出在 [plugins/api.py](../src/len_bot/plugins/api.py)；执行和事实边界见[架构](architecture.md)，部署与配置操作见[运行手册](operations.md)。未实现的本轮目标只记在[当前任务](iteration.md)。

## 目录、描述符与配置

一个插件是一个 Python 包。内置包位于 `src/len_bot/plugins/builtin/`；本地插件位于根配置 `plugin_directories` 明确列出的目录下。宿主按根目录顺序、包目录名称顺序发现 `__init__.py` 的 `PLUGIN: PluginSpec`，重复 ID 报告双方路径。

`PluginSpec` 是唯一元数据，包含 ID、名称、版本、描述、全局 config_model、scene_config_model 和 `create(context)`。已有资源权限与类型在同一处声明；工具清单从实际 `register_tool` 生成。描述符导入只定义类型和入口，不能建立 HTTP 客户端、启动轮询或请求模型。参照[日历描述符](../src/len_bot/plugins/builtin/asoul_calendar/__init__.py)和[网页描述符](../src/len_bot/plugins/builtin/web_search/__init__.py)，不再编辑中央插件清单或配置类型映射。

根配置 `plugins.<id>` 明确保存 `enabled` 和 `config`。`config_model` 负责参数类型；可选 `validate_config(config, root)` 只做本地的公共时间、成员及容量关系校验。ConfigStore 在发现目录后解析一次专有参数，启动和面板保存使用同一入口。未配置的目录仍可展示元数据，但不建立插件实例或连接。

实现类继承 `BasePlugin`，构造时使用 `super().__init__(context.manifest)`；取得的 `context.config` 已通过该插件模型解析。插件文件和资源相对于 `context.directory`，运行数据需要时写入 `context.data_directory`。目录不自动创建空数据文件。项目依赖继续由 uv 管理，插件不自行安装依赖。

可直接阅读独立目录中的[业务时钟](../local_plugins/local_clock/__init__.py)：一个文件完成描述符、配置、读取工具和两个命令，README 只说明[本插件用法](../local_plugins/local_clock/README.md)。它没有核心注册补丁；添加根目录、全局配置和目标群条目后，由同一个宿主发现。样例包含停用的参数条目，实际开放须在目标部署保存。

## 工具与读取

在 `on_load(context)` 调用 `register_tool`，提供名称、用途、明确的 Pydantic 参数模型、handler、read/proposal 类别和可用角色。低频工具可声明 deferred，并提供业务别名和关键词；实际执行名保持唯一。参数模型同时用于校验与 Schema，handler 接收已解析参数和本次 `PluginCallContext`。

调用字段包括当前场景、请求者、真实 source_event_id、时间、读取截点、episode/job、角色、工具调用 ID、PluginOrigin 和当前提案 Ledger。系统来源没有人类请求者，不伪造 user 身份。长期插件实例不保存可变的“当前群”。只读服务返回 `ToolResult`，暂存操作复用 Ledger；原资料与视觉覆盖、提交和送达的含义继续由架构规定。

工具 timeout 可由描述符的 `call_timeout(config)` 从实际配置读取，也可在注册时明确传入。工具定义和调用时均检查当前角色、场景与启用状态；工具名冲突会报告实际注册双方，不覆盖前者。

### 日历：同一服务供工具和精确消息调用

[现有日历实现](../src/len_bot/plugins/builtin/asoul_calendar/plugin.py)是共用读取服务的最小业务示例。on_load 把 get_live_schedule 注册为工具，同时为配置中的命令词注册 ExactText、consume=True 的 handler。两者都调用 get_live_schedule；精确命令不需要普通聊天先决定是否查询。

下面是其 on_command 的实际处理路径。request 由 command_request 使用原命令时间及业务时区计算；渲染和失败处置属于本插件。

```python
request, title = self.command_request(call.origin.entry_id, call.event.timestamp)
observed = await call.invoke_tool('get_live_schedule', request)
if observed.status not in {'ok', 'no_results'}:
    raise ValueError(f'日程来源未完整取得：{observed.content}')
schedule = ScheduleResult.model_validate_json(observed.content)
png = await asyncio.to_thread(self.renderer.render, schedule, title)
asset_id = await call.save_image(png, '日程命令生成图片')
await call.submit_message([MessageSegment(type='image', asset_id=asset_id)])
```

自然语言读取取得同一 ToolResult，由当前 Agent 继续使用；精确命令取得资料后渲染一次并提交图片。业务时钟的“现在几点”沿相同路径直接提交文字，“时间简报”则显式调用 run_agent，展示插件如何选择是否使用模型。三者均通过原提交和发送服务。

## 加载与停用

宿主创建实例并调用 `on_load` 建立资源与注册，再调用 `on_enable` 启动任务；插件不在 `on_load` 自行再次启用。`on_disable` 结束本插件活动，`on_unload` 释放资源。重复启用已启用实例不重复调用钩子；资源参数已变化时先正常释放旧实例，再按已解析的新参数加载。

加载或启用失败会释放已建立的资源并注销工具，保留发现的描述符及实际错误供面板查看。代码变更按正常停机升级处理；本轮不提供在线安装或代码热替换。验证方式遵循[工程约束](../AGENTS.md)。

## 消息处理与公共调用

在 on_load 中 register_handler，声明 id、description、match、handler、event_types、sources、priority、consume 和 require_to_me。ExactText、Command、RegexText 或本地同步函数返回 bool；数值优先级小者先匹配，同级按稳定注册顺序。冲突的独占精确命令在注册时报告双方，匹配不请求网络或模型。available(call) 检查插件群配置；validate(call) 只核对已保存资料与当前本地状态，它也会在提交边界调用，不能请求 HTTP/模型。原话与归属先保存，耗时读取和处理放在 handler 中，消费后失败不转普通聊天。

sources 默认 human；plugin_event 和 self_sent 要显式声明。self_sent 只认真实 MESSAGE_SENT，不认草稿、Shadow 或 unknown；同一插件自己的输出不会再次触发自己。引用关系在 call.event.metadata.quote_context 中读取，日历引用评论的消费规则见[日历实现](../src/len_bot/plugins/builtin/asoul_calendar/plugin.py)，核心不统一消费所有插件评论。

call.invoke_tool(name, typed_arguments) 复用注册服务和原观察存储，没有伪模型调用 ID；返回完整 ToolResult，调用者应按 status、coverage 与来源处理失败。call.save_image 保存当前场景素材，call.submit_message 接受 MessageSegment，经 Actor、Gate、队列提交并保存真实回执。提交与发送重新检查真实 source_event_id、已保存路由、版本、当前启用与 validate；全体提及还要求该 handler 明确提供当下有效的 allow_mention_all。

call.run_agent 接收 instructions、input_observations、tool_names、model_role、max_steps、max_tool_calls、context_tokens、output_tokens；参数由 PluginAgentRequest 在入口解析。模型路由和额度从插件的根配置取得。include_identity 决定是否带入当前身份；input_mode 可选 materials（仅资料）、source（真实触发和引用）、conversation（普通对话投影，含当前群史及参考）。外部资料保持带类型的 user 投影，不提升为指令。

output_mode=result_only 必须提供 output_model。Agent 调用 return_result 返回该 Pydantic 类型，不能使用提案工具或自动发送；[直播实现](../src/len_bot/plugins/builtin/bilibili_live/plugin.py)使用公告配置的既有模型路由，只带场次资料，随后明确提交一次邀请。

返回结果不改变普通对话 disposition；模型 status 与 plugin_agent 用途保留实际调用，提交和送达另查回执。input_observations 不会被下一步钩子的临时资料清理移除。专用循环的图片读取与普通对话共用正文、附件和阅读范围装配，实际像素及资料位置可在对应步骤中核对。

output_mode=respond 不提供 output_model，使用同一个 ProposalLedger、respond、Actor 和 ActionQueue。source 或 conversation 投影给出真实来源 M，插件可按 tool_names 明确开放已有工作、提醒或记忆提案；这些操作仍须满足原人类请求和证据契约。全部 checkpoint 共用消息额度，continue 继续当前运行，wait 在真实送达后由 open loop 等待目标的回复。恢复保留原插件、入口、模型绑定、请求参数、已存资料及累计预算；旧进程或已变化的入口不能被当作一次新运行重做。

工具内部调用 Agent 时共享父运行的调用账户，并借用已经持有的模型并发位；后台工作仍由原 JobStore 收取调用与时间额度。专用 Agent 串行使用父账户并为父调用留一次收尾调用，不支持 Agent 内再次递归启动插件 Agent。read 工具只能取得结果；主动表达须使用 proposal 工具和父运行的真实 Ledger。工具提交的等待同时结束父运行，真实回复由原插件恢复。确定性 invoke_tool 也保留真实父来源，调用不制造模型 tool_call。

嵌套表达共享原 Ledger 和引用身份；本次窗口、工具集合、容量和像素范围在调用结束后恢复父运行的设置。新取得的观察仍保存，实际展示进入子调用 Trace，不通过替换父窗口破坏原工具交换。

自定义事件在 PluginSpec.event_models 声明名称和 payload 模型。context.emit_event(name, typed_payload, scene_id=..., event_id=..., timestamp=...) 发布 PLUGIN_EVENT；事件身份由插件的真实业务关系确定，不生成内容摘要去重。

context.scene_config(scene_id)、scene_configs()、members、time_settings、now() 提供只读公共输入；长期实例不读取私有 Runtime。on_enable 用 context.start_task(coroutine, name=...) 启动所属任务，停用由宿主取消并等待，on_unload 释放客户端。config_apply 默认 restart_plugin；仅实现 apply_config 的插件可显式声明 in_place。面板由实际工具、handler 与两种配置 Schema 生成，没有单独手写的业务清单。

原始 HTTP 超时、状态码与网络错误在宿主执行边界形成失败观察；服务自己的“无结果”和协议解析错误由插件明确返回。核心不按工具名称改写失败正文或在一次失败后隐藏工具。若允许下一步读取，应在插件结果中准确说明已知资料与可用入口，由调用者在剩余预算内选择。

## 长期工作

提案工具可调用 `call.stage_work(goal=..., request_source=..., evidence=..., parameters=...)`，取得原 Ledger 的暂存引用，再由 respond 提交。request_source 始终是已读人类原话；普通信息工作不填 parameters。专用工作在 PluginSpec.work 提供 PluginWorkSpec，parameters 必须使用其参数模型，不在插件中建立任务队列或写裸连接。

只有需要固定业务范围和独立覆盖的工作才声明该对象。它提供参数、修订和进度模型，明确的 allowed_tools 与 allow_learning，以及截点、修订、新进度、阅读覆盖、结果判定、续页和进度展示函数；[群总结实现](../src/len_bot/plugins/builtin/group_summary/work.py)保留实际范围和分页职责。这些函数只使用已保存资料和本地计算，存储回调在原事务中执行，不请求 HTTP/模型、不建立嵌套写事务。专用提示词仍由本插件的 before_model 钩子提供。

新工作在既有 task.payload 保存 PluginOrigin、work_parameters 和 work_progress；原工作 ID、revision、预算、观察和发送服务继续使用。版本不兼容时更新 PluginSpec.version。原版本或入口不存在的工作只保留中断说明与原数据，不能用新参数类型猜测恢复；取消仍可由原管理入口完成。业务参数修订通过 revise_work.parameters 或工作页的插件字段完成，Schema 由所属插件提供。

全局停用取消所属调用、轮询及未完成工作；关闭某个群中的插件只取消该群的调用和工作，共享轮询继续服务其他开放群。已送达事实保留，未发送行动在原出站检查核对归属；未知发送不重发。插件的真实等待和尚未发送的提醒进入待核对，已执行或已进入发送的工作保留各自结果与回执状态。

## 调用钩子

`context.register_hook(phase, id=..., handler=..., scope='own', priority=100)` 在 on_load 注册。数值较小者先执行，同级沿注册顺序；scope 默认 own，只作用于本插件发起的运行。conversation、work 和 scene 须显式声明，且仍要求该插件在当前场景启用。钩子接收下表中的类型和 PluginCallContext，返回同类型或 None；stop_reason 明确停止本次阶段。改变、中止和错误进入当前 Trace。

| 阶段 | 可处理的内容 | 保留的事实 |
|---|---|---|
| before_model / BeforeModel | 增加插件指令、ToolResult 资料投影，选择当前允许工具的子集 | 终结工具仍可用，修改后重新核对上下文容量 |
| after_model / AfterModel | 调整候选调用参数 | 原调用 ID、工具名、顺序、供应商续接与实际 usage 不变；执行参数重新校验 |
| before_tool / BeforeTool | 验证或明确改写业务参数，stop_reason 可停止这次调用 | 停止返回真实失败观察，计入本轮已请求工具额度，不伪造执行成功 |
| after_tool / AfterTool | 增加 notes 或独立 view 组织模型可见内容 | 原始观察、实际展示范围与操作回执保留；新视图不成为来源事实 |
| before_commit / BeforeCommit | 提交前调整每条消息的片段 | 消息数、来源关系不变，类型、人物与素材资格再次校验；提交后不改正文 |
| after_delivery / AfterDelivery | 读取已保存的真实回执 | 不改写 sent、not_sent、unknown 或 Shadow；错误另记插件钩子 Trace |

直播插件的公告指令通过 before_model 加入；其 before_commit 保持一条文本邀请的业务契约。确定性图片提交也经过 before_commit，真实队列回执保存后才调用 after_delivery。工具错误、未知回执和模型生成内容的事实含义仍见[架构](architecture.md)。
