# 插件开发

面向维护 LenBot 业务插件的开发者。导出见 [plugins/api.py](../src/len_bot/plugins/api.py)，执行与事实边界见[架构](architecture.md)，部署与保存见[运行手册](operations.md)，待实施变更与状态见[产品路线](plan/README.md)，本批实际核对见[当前任务](iteration.md)。本文不把新计划中的字段或接口当作已提供。

## 目录、描述符与配置

一个插件是一个 Python 包。内置包位于 `src/len_bot/plugins/builtin/`；本地插件位于根配置 `plugin_directories` 明确列出的目录下。宿主按根目录顺序、包目录名称顺序发现 `__init__.py` 的 `PLUGIN: PluginSpec`，重复 ID 报告双方路径。

`PluginSpec` 是唯一元数据，包含 ID、名称、插件自身 version、接口世代 api_version、描述、全局 config_model、scene_config_model 和 `create(context)`。已有资源权限与类型在同一处声明；工具清单从实际 `register_tool` 生成。描述符导入只定义类型和入口，不能建立 HTTP 客户端、启动轮询或请求模型。参照[日历描述符](../src/len_bot/plugins/builtin/asoul_calendar/__init__.py)和[网页描述符](../src/len_bot/plugins/builtin/web_search/__init__.py)，不再编辑中央插件清单或配置类型映射。

### 接口世代 1 与迁移

宿主当前只接收 `PluginSpec(api_version=1, ...)`。该字段必填，值须为整数 `1`，不接收字符串、布尔值或其他世代。插件声明自己适配的字面值，不从当前宿主常量取值来假定兼容。`version` 仍是插件自身版本，原 `PluginOrigin.plugin_version`、工作修订和出站版本核对继续使用它；接口世代不替代业务归属或执行资格。

发现流程先导入包、取得描述符，再核对世代；不匹配时报告入口文件、插件 ID、声明值与宿主要求，并终止发现，不实例化插件、不解析其根配置，也不自动改参数或选择旧接口。缺少必填字段时，描述符构造直接报错。所有发现目录都受此约束，包括尚未配置或停用的插件；不是忽略不兼容目录后继续启动。同进程包导入已经执行 Python 代码，因此这不是恶意代码隔离。

作者从 [公共导出入口](../src/len_bot/plugins/api.py)取得现有类型，以本文的工具、事件、表达、工作与提交合同为世代 1 的使用说明。以下边界不因增加字段而改变：身份、场景、预算、阅读资格和提交回执归宿主；直接访问 `_runtime`、`_host` 或其他内部模块不属于公共合同。现有内部依赖尚待逐条收窄，声明世代不表示这项工作已经完成。

从无世代版本迁移时，先核对插件使用的公共入口和所属业务合同，再在原 `PLUGIN` 构造中补 `api_version=1`；不得只改数字声称适配。仓库内 13 个内置插件与业务时钟已补声明，未改变插件自身版本、ID、配置或事件身份。外部目录由维护者按实际代码迁移；升级／回退步骤见[运行手册](operations.md#插件接口世代升级)。

此批没有移除或弃用公共导出，也没有兼容适配层。未来不兼容变更须先说明变更入口、迁移办法和对应世代，不能仅改插件版本绕过拒绝；公开稳定支持期和弃用期限仍待维护者决定，当前不承诺永久兼容。面板分别显示插件版本和接口世代，只表示已发现的声明，不表示已启用或业务验收。

调用材料清单 v3 由宿主在实际工具定义生成处绑定插件 ID、实例 version 与 api_version，最终登记时保存归属而不保存动态 Schema。声明后变化会单独标记；仅有归属字段不能还原旧参数模型，也不说明工具已执行。插件作者不填另一份审计描述符，不增加公共注册参数；现有 deferred、场景／角色过滤及 Hook 子集保持原合同。

根配置 `plugins.<id>` 明确保存 `enabled` 和 `config`。`config_model` 负责参数类型；可选 `validate_config(config, root)` 只做本地的公共时间、成员及容量关系校验。ConfigStore 在发现目录后先分别解析所有已配置插件的参数，再执行依赖校验；依赖使用 parsed_config，不按 JSON 排列顺序重猜或重复解析原字段。启动和面板保存使用同一入口。未配置的目录仍可展示元数据，但不建立插件实例或连接。同一个插件在全局与某个群各有一份开关：本群条目只有在全局已配置、全局已启用、本群已启用且本群启用的场景都成立时才生效，面板据此逐项说明，不把“在本群打开”写成已经可用。

面板表单由 `config_model` 生成的 JSON Schema 驱动，不手写字段清单。互斥的配置形状（例如 workspace 的 `worker` 与 `gateway`）用 `json_schema_extra` 的 `x-lenbot-exclusive` 声明字段组，表单据此渲染成一次单选，不构造同时给出两个分支的草稿；该键只是表单提示，服务端的模型校验仍然是准入依据。列表与详情按 Schema 字段逐个渲染：布尔、枚举、数字、文本和按 JSON 编辑的对象／列表；`title`、`description` 与上下限来自 Schema，前端不另写一份字段说明。枚举在界面上显示中文名，保存的仍是 Schema 里的原值；中文名用 `x-lenbot-enum-labels` 写在声明该字段的模型上，新枚举值没有中文名时回落显示原值，不会从选项里消失。取值封闭的简单列表（link_parser 的平台）用 `x-lenbot-list-choices` 声明成勾选项，没有声明的列表仍是可增删的行。三个扩展键都只是表单提示，不参与服务端校验。保存前表单先核对必填项与 JSON 结构，服务端拒绝时按其返回路径把错误落到对应字段并保留草稿。

实现类继承 `BasePlugin`，构造时使用 `super().__init__(context.manifest)`；取得的 `context.config` 已通过该插件模型解析。插件文件和资源相对于 `context.directory`，运行数据需要时写入 `context.data_directory`。目录不自动创建空数据文件。项目依赖继续由 uv 管理，插件不自行安装依赖。

可直接阅读独立目录中的[业务时钟](../local_plugins/local_clock/__init__.py)：一个文件完成描述符、配置、读取工具和两个命令，README 只说明[本插件用法](../local_plugins/local_clock/README.md)。它没有核心注册补丁；添加根目录、全局配置和目标群条目后，由同一个宿主发现。样例包含停用的参数条目，实际开放须在目标部署保存。

插件面板保存与启停的迟到响应只能更新原打开对象；配置冲突在详情内处理，刷新失败时不能用另一插件或未重读的值替换基线。离开页面不会取消服务器已接受的配置操作。凭据仍只在原根配置，由原配置锁和凭据世代处理并发编辑，未新增凭据存储。

本群设置按原 catalog 投影编辑全部已声明插件，不要求外部插件加入固定能力卡才能配置；同一 ID 只呈现一份群参数。缺少群 Schema 会明确阻止空参数替代。全局和本群的冲突选择只重建草稿：未改字段采用新读取值，修改列表按整组保留，JSON 按实际值比较；互斥分支不由属性顺序决定。嵌套字段和增删按钮继承保存中禁用状态，写入后的全局参数须读回同一插件再建立新基线。以上均不代替服务端 Schema、根配置锁、凭据代次或插件装载准入。

## 工具与读取

在 `on_load(context)` 调用 `register_tool`，提供名称、用途、明确的 Pydantic 参数模型、handler、read/proposal 类别和可用角色。低频工具可声明 deferred，并提供业务别名和关键词；实际执行名保持唯一。参数模型同时用于校验与 Schema，handler 接收已解析参数和本次 `PluginCallContext`。

调用字段包括当前场景、请求者、真实 source_event_id、时间、读取截点、episode/job、角色、工具调用 ID、PluginOrigin、可选 initiator 和当前提案 Ledger。initiator 未建立时保持 None，不等于系统授权；独立公共研究另有宿主核验的 public_research 标记；它只开放明确的公共工具，不能由插件用 system 场景名前缀取得权限。长期插件实例不保存可变的“当前群”。只读服务返回 `ToolResult`，暂存操作复用 Ledger；原资料与视觉覆盖、提交和送达的含义继续由架构规定。

### 读取当前调用的已保存资料

业务代码使用 `await call.read_observation(result_id)`，返回原 `ToolResult` 或 `None`，不必访问完整 EventStore。方法委托当前 PluginContext，要求上下文属于该实例，再沿原 Host.validate_call 核对真实来源、所属插件版本及本场景入口；查询范围固定为 call.scene_id，作者不能传任意场景列表。参数须为真实 result_id，不自动将 R 别名、URL 或文件路径猜成资料身份。

这是本地已保存观察的读取，不执行工具、不重新抓源、不登记新的观察、不扣一次模型／工具额度，也不直接授予模型已读资格。资料不存在或不属于当前场景时返回 None，由业务明确处理；准入、存储或解析失败直接抛出，不自动改用另一来源。source 的截点沿原调用核对，观察本身仍按原场景查询，不套用事件 rowid 截点去拒绝同轮新产生的资料。资料内容的业务类型、工作修订、公开研究来源资格和后续呈现／引用继续由所属路径核对。

当前群报告的分析输入与已保存产物、动态卡片的已取得资料、链接下载的已保存解析结果已改用该入口；它们的业务解析及后续操作不变。PluginContext.event_store 仍供尚未迁移的内部集成使用，不是新插件推荐入口，也没有被伪装为沙箱。其他事件／媒体／工作能力的内部依赖尚待逐项收窄，本方法不表示全部公共上下文已经稳定。

工具 timeout 可由描述符的 `call_timeout(config)` 从实际配置读取，也可在注册时明确传入。工具定义和调用时均检查当前角色、场景与启用状态；工具名冲突会报告实际注册双方，不覆盖前者。

核心提案和 respond 使用固定结构，不将当前目标或回执写入 Schema 枚举。低频插件仍沿 deferred 渐进发现，显式插件调用仍受原 tool_names 子集限制，before_model hook 只能选择当前获准目录并保留终结工具。终结步骤不再缩成单工具表，但宿主在执行前禁止非终结调用；定义可见不等于当前可执行，也不放宽来源、版本或预算。

工具发现同步沿所属入口的原子集：显式插件传入 tool_names，专用工作传入 allowed_tools，公共研究叠加原公共工具集合。候选及无结果时的类别先过滤，再排名和截取；已不属于当前入口的展开项不占目录额度。嵌套调用复制展开目录，不通过子入口的搜索挤掉父入口工具；观察、已读资格与累计预算继续按原共享合同处理。发现结果只说明本入口本次匹配，下次提供 Schema 时仍查当前资格与合法 hook 子集。

capability_facts 复用原适用性判断投影用途和缺项，不新增执行判断。混合模块同时保留对话用途与普通工作用途；条件型工作用途仅列 required_capabilities 和原入口检查说明，不在缺少真实工作时运行 available 来宣称已就绪。工作用途仅在允许 start_work 的对话入口说明，只覆盖普通工作可用的读取和账号写入工具；专用提案继续走所属入口。页面和模型读取同一投影，runtime_state 不作为第二套启停开关，具体参数与业务条件仍由原 handler 核对。

普通 start_work 新增可选 reuse_work_ref，选择已实际提供的普通研究结果版本，用于后续整理／导出。宿主填写内部 JobProposal.reused_work 并在原 Gate／事务核对；插件钩子不能靠填写该内部字段补造已读资格、换来源版本或带入其他场景资料。新工作不继承旧阅读账、发送片段或上传资格；专用插件成果仍沿所属入口处理。现有插件 stage_work 的普通 result_refs 资料传递不变，不自动把相同资料推断成父子工作关系。

工作空间的 prepare_workspace_file 增加独立 MD 纯文本格式，仍只接受当前工作 Gateway 的确认产物。Markdown 按 UTF-8 与原控制字符规则检查，不作为可执行内容；for_upload 沿原 send_file、工作修订及动作审查，不直接发送。工具发现增加 Markdown／MD 关键词，不增加额外执行角色或权限。

respond 的输入 Schema 与本地解析由同一组模型定义：业务关系通过唯一回执字段确定，不填写 intent；sources 仅声明 silent／incomplete，实际回应、创建与等待由宿主派生。提案工具 submit_message 已沿同一路径提交，不再补写 replied。covers 只用于普通人类对话合并回应，插件系统表达仍绑定原唯一来源。直接确定性入口及 result_only 保持原合同；旧参数不自动改写为新结构。

respond 的 `messages[].answer_basis` 是可选增量字段，详见[单条答复的依据](architecture.md#单条答复的依据)。嵌套表达共享原实际阅读记录与工作版本，但导入资料 ID 不表示已读；result_only 的结果类型和直接确定性发送入口不变，不自动给它们补造依据。钩子仍不能重写原始工具回执或增加消息归属，依据穿过提交/队列时保留，不能借它改变权限或发送参数。

资料页的 evidence_ref 由宿主投影生成。respond.answer_basis 和普通工作 finish_work 用 evidence_refs 选择页，内部仍解析为原 ResultSpan；原始 ToolResult 保存时不接受插件自带的页名作为读取资格。原生正文或合法宿主投影必须实际进入最终请求，并在模型响应后确认。独立插件读取通过既有工作 record_presentations 回调保存页名和范围，工作恢复从 observation_reads 重建；result_only 的自定义输出类型、专用工作的 JobResult 以及 work_state 的范围合同保持不变。

最终请求材料清单可逐位置记录上述原判定认可的资料范围，包含独立插件表达中的工具回复与合法材料；不另外发起读取、不采用资料正文或改写 after_tool 的回执。保存了范围不表示模型已经响应或已取得引用资格，失败／定位结果没有原文范围也不等于工具没运行。

`kind` 区分读取与提案，`ordered` 独立声明兄弟调用是否必须串行；工作区执行和文件操作共用状态，不能因 kind=read 就并发。原始观察按 result_id 保存、分页展示，不按参数相同盲目复用旧结果。

浏览器的 open/snapshot/interact/capture 也声明 `ordered=True`；内部页面锁保留，但不代替模型响应内的调用顺序。正文结果以实际快照地址为来源，区分采集截断和仅保存当次正文页。宿主续取 DOM 使用 `source_next_call`；Gateway 已保存的受限正文使用原 R 的本地续读，不把两种偏移混用。截图附件只说明已登记，是否装入像素仍看最终请求清单。

工具 attachments 可同时包含图片和已登记音视频。现有媒体准备器按登记 MIME 分流，音视频只提供引用说明，不调用图片解码，也不冒充视觉证据或转写。插件必须继续在正文保存采样时间、音轨范围和派生限制；面板读取这些已保存字段，不为显示覆盖而重新请求源端。

`register_tool` 已支持 `required_capabilities`、`side_effect`、`input_scope`、`output_scope`，声明与 PluginHost 当前可用性检查共同生效；具体源身份、工作修订和资源边界仍由原 handler 核对，不能只靠声明扩大资格。

WorkspaceCancelled 是取消信号，Host 继续传播以结束等待它的运行；不要统一转成普通失败后继续循环。执行与清理期限分别有界，终止是否确认需要原执行回执，不能仅凭收到取消就声明容器已停止。

### 日历：同一服务供工具和精确消息调用

[现有日历实现](../src/len_bot/plugins/builtin/asoul_calendar/plugin.py)是共用读取服务的最小业务示例。on_load 把 get_live_schedule 注册为工具，同时为配置中的命令词注册 ExactText、consume=True 的 handler。两者都调用 get_live_schedule；精确命令不需要普通聊天先决定是否查询。

request 由 command_request 使用原命令时间及业务时区计算。on_command 随后调用同一工具，按以下分支处理；完整代码以该实现为准。

| 工具结果 | 处理路径 |
|---|---|
| error_code=source_unavailable | 取得本次来源失败信息，StatusCardRenderer 生成“日程暂未取得”，save_image 后 submit_message |
| ok 或 no_results | 解析 ScheduleResult，使用共享 HTML 日历模板生成正常或空日程卡，保存并提交图片 |
| 其他错误 | 在该 handler 结束并保留错误，不转成空日程或普通对话 |

日历按业务模板渲染，查询区间、抓取时间和来源保留在 ScheduleResult；空日程只表示源日历未收录。现有 HTML 渲染失败路径记录原错误并使用已有 Pillow 卡片，不增加模型调用。具体样式留在插件模板，不在公共开发合同重复维护。

未知成员返回 invalid_member 和配置内可选名称/别名，不记插件执行异常；未提供成员表示全部日程。上述确定性分支不新增模型调用，渲染失败与发送失败仍分别处理。自然语言读取取得同一 ToolResult，由当前 Agent 继续使用。业务时钟的“现在几点”直接提交文字，“时间简报”则显式调用 run_agent。所有提交均经原发送链，工具返回或图片登记不等于送达。

## 加载与停用

宿主创建实例并调用 `on_load` 建立资源与注册，再调用 `on_enable` 启动任务；插件不在 `on_load` 自行再次启用。`on_disable` 结束本插件活动，`on_unload` 释放资源。重复启用已启用实例不重复调用钩子；资源参数已变化时先正常释放旧实例，再按已解析的新参数加载。

加载或启用失败会释放已建立的资源并注销工具，保留发现的描述符及实际错误供面板查看。代码变更按正常停机升级处理；当前不提供在线安装或代码热替换。验证方式遵循[工程约束](../AGENTS.md)。

**“已配置”“全局保存为启用”“运行时已加载启用”“本群已加入”是四件独立的事实**，面板分别显示，不压成一个开关。全局保存为启用的插件仍可能因为参数校验失败而不在运行时；已经加载也不代表目标群加入了它。缺少已声明参数时插件不装载，也不会建立源连接。保存参数只写根配置并按 `config_apply` 原位应用或重新装载该插件，不会顺带启用它。

## 消息处理与公共调用

在 on_load 中 register_handler，声明 id、description、match、handler、event_types、sources、priority、consume 和 require_to_me。deterministic_read_only 仅供无模型、确定性只读服务声明睡眠豁免，当前只用于日程精确命令；同一声明在 Actor 提交时也不因有关未读确定唤醒而失败，因为 handler 没有模型循环可续读。handler 或 announcement 本身不授予豁免。ExactText、Command、RegexText 或本地同步函数返回 bool；数值优先级小者先匹配，同级按稳定注册顺序。冲突的独占精确命令在注册时报告双方，匹配不请求网络或模型。available(call) 检查插件群配置；validate(call) 只核对已保存资料与当前本地状态，它也会在提交边界调用，不能请求 HTTP/模型。原话与归属先保存，耗时读取和处理放在 handler 中，消费后失败不转普通聊天。

sources 默认 human；plugin_event 和 self_sent 要显式声明。self_sent 只认真实 MESSAGE_SENT，不认草稿、Shadow 或 unknown；同一插件自己的输出不会再次触发自己。引用关系在 call.event.metadata.quote_context 中读取，日历引用评论的消费规则见[日历实现](../src/len_bot/plugins/builtin/asoul_calendar/plugin.py)，核心不统一消费所有插件评论。

call.invoke_tool(name, typed_arguments) 复用注册服务和原观察存储，没有伪模型调用 ID；返回完整 ToolResult，调用者应按 status、coverage 与来源处理失败。call.save_image 保存当前场景素材，视频和音频下载也先登记为场景媒体，再由 call.submit_message 通过 Actor、Gate、队列提交并保存真实回执。提交与发送重新检查真实 source_event_id、已保存路由、版本、当前启用与 validate；全体提及还要求该 handler 明确提供当下有效的 allow_mention_all。

call.run_agent 接收 instructions、input_observations、tool_names、model_role、max_steps、max_tool_calls、context_tokens、output_tokens；参数由 PluginAgentRequest 在入口解析。模型路由和额度从插件的根配置取得。include_identity 决定是否带入当前身份；input_mode 可选 materials（仅资料）、source（真实触发和引用）、conversation（普通对话投影，含当前群史及参考）。外部资料保持带类型的 user 投影，不提升为指令。

max_steps/max_tool_calls 允许 None；没有父预算的入口应显式提供有界模型次数。共享父工作时使用原绑定、快照、累计账目与 work_call_admission；有限日额度无法计算预占时拒绝，不补造上限。插件不得绕过公共调用入口自建模型客户端，也不能通过新建账户解决剩余额度不足。

output_mode=result_only 必须提供 output_model。Agent 调用 return_result 返回该 Pydantic 类型，不能使用提案工具或自动发送；[直播实现](../src/len_bot/plugins/builtin/bilibili_live/plugin.py)使用公告配置的既有模型路由，只带场次资料，随后明确提交一次邀请。

返回结果不改变普通对话 disposition；模型 status 与 plugin_agent 用途保留实际调用，提交和送达另查回执。input_observations 不会被下一步钩子的临时资料清理移除。专用循环的图片读取与普通对话共用正文、附件和阅读范围装配，实际像素及资料位置可在对应步骤中核对。

output_mode=respond 不提供 output_model，使用同一个 ProposalLedger、respond、Actor 和 ActionQueue。source 或 conversation 投影给出真实来源 M，插件可按 tool_names 明确开放已有工作、提醒或记忆提案；这些操作仍须满足原人类请求和证据契约。全部 checkpoint 共用消息额度，continue 继续当前运行，wait 在真实送达后由 open loop 等待目标的回复。恢复保留原插件、入口、模型绑定、请求参数、已存资料及累计预算；旧进程或已变化的入口不能被当作一次新运行重做。

提醒修改／取消的原值由已有上下文登记并由 ProposalLedger 放入内部 expected，插件不自行猜测或补当前值。原事务会拒绝缺失／过期原值，不能绕过它直改任务表；创建和结果提交不使用控制原值。该变化不新增模型工具参数或赋予原本没有的提醒资格。

工具内部调用 Agent 时共享父运行的调用账户，并借用已经持有的模型并发位；后台工作由原 JobStore 记录调用与活动时长。专用 Agent 串行使用父账户，有限模型次数下为父调用留一次收尾调用；这不等于 token 已原子预留。不支持 Agent 内再次递归启动插件 Agent。read 工具只能取得结果；主动表达须使用 proposal 工具和父运行的真实 Ledger。工具提交的等待同时结束父运行，真实回复由原插件恢复。确定性 invoke_tool 保留真实父来源，不制造模型 tool_call。

嵌套表达共享原 Ledger 和引用身份；本次窗口、工具集合、容量和像素范围在调用结束后恢复父运行的设置。新取得的观察仍保存，实际展示进入子调用 Trace，不通过替换父窗口破坏原工具交换。

自定义事件在 PluginSpec.event_models 声明名称和 payload 模型。context.emit_event(name, typed_payload, scene_id=..., event_id=..., timestamp=...) 发布 PLUGIN_EVENT；事件身份由插件的真实业务关系确定，不生成内容摘要去重。

context.scene_config(scene_id)、scene_configs()、members、time_settings、now() 提供只读公共输入；长期实例不读取私有 Runtime。on_enable 用 context.start_task(coroutine, name=...) 启动所属任务，停用由宿主取消并等待，on_unload 释放客户端。config_apply 默认 restart_plugin；仅实现 apply_config 的插件可显式声明 in_place。面板由实际工具、handler 与两种配置 Schema 生成，没有单独手写的业务清单。

原始 HTTP 超时、状态码与网络错误在宿主执行边界形成失败观察；服务自己的“无结果”和协议解析错误由插件明确返回。核心不按工具名称改写失败正文或在一次失败后隐藏工具。若允许下一步读取，应在插件结果中准确说明已知资料与可用入口，由调用者在剩余预算内选择。

## 长期工作

提案工具可调用 `call.stage_work(goal=..., request_source=..., evidence=..., parameters=...)`，取得原 Ledger 的暂存引用，再由 respond 提交。request_source 始终是已读人类原话；普通信息工作不填 parameters。专用工作在 PluginSpec.work 提供 PluginWorkSpec，parameters 必须使用其参数模型，不在插件中建立任务队列或写裸连接。

只有需要固定业务范围和独立覆盖的工作才声明该对象。它提供参数、修订和进度模型、allowed_tools、allow_learning，以及截点、修订、新进度、阅读覆盖、结果判定、续页和进度展示函数。这些存储回调只读已保存资料并做本地计算，在原事务中执行，不请求 HTTP/模型、不建立嵌套写事务。

需要插件安排完整执行顺序时，声明 `execute(context: PluginWorkContext) -> JobResult`。它在原工作运行器、取消关系与时限中执行；上下文提供 call、revision、parameters、goal、constraints、输入／输出窗口和 resume_from。`progress()` 读取当前版本，`save_progress(typed_progress)` 保存并核对版本，`save_result(operation, ToolResult)` 将长资料存入原观察库，`adopt_results(ids)` 复用本群已有资料。不要在进度里反复复制长正文。

`context.run_agent(instructions=..., input_observations=..., output_model=...)` 使用该工作的原绑定和账户，每次只做一次 materials/result_only 的结构化调用，不开放工具、身份资料或直接发送。`input_tokens(...)` 使用公共请求估算器为批次分配容量，最终仍经过实际请求装配检查。`budget()` 返回当前记录中的已用与上限；恢复使用原创建快照、绝对期限与累计计数，不重算当前默认上限。可选 `needs_model(job)` 是只读本地判断，用于允许仅剩渲染的工作在没有模型余量时继续；它不能增加预算。

成品使用 `JobResult.delivery = PreparedWorkDelivery(result_id=..., segments=...)`：result_id 必须属于本次成果，图片先通过 `call.save_image` 登记。本次 execute 返回后，由原完成事件和 Actor/Gate 交付保存的片段；插件不能从后台工作调用 submit_message。新相关输入先由原对话处理；已提交或发送未知不重复提交，渲染错误与送达错误分开。

[群报告执行](../src/len_bot/plugins/builtin/group_summary/analysis.py)展示批次保存、复用、合并和渲染；[业务类型](../src/len_bot/plugins/builtin/group_summary/work.py)记录范围与完成条件，[报告类型](../src/len_bot/plugins/builtin/group_summary/report.py)保留统计、来源、引语和身份。自然日工具先用 `call.read_request_source(M)` 取得已读人类原话时间；不是从当前时间或模型猜测的日期生成窗口。插件业务字段可在 JSON Schema 的 format 标注 tool-result-id、tool-result-list 或 media-id，工作详情使用公共资料／媒体链接显示。

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

after_tool 的原始投影字符串保持不变，notes/view 由宿主作为独立 user 材料 plugin_tool_view 追加在整组工具回执之后，带原 tool_call_id；不插入原生工具配对中，也不授予原话或工具正文的已读范围。插件处理器仍返回 AfterTool 或 None，无需改返回类型。before_model 附加 ToolResult 只声明坐标不构成阅读证明：字符正文须对应保存片段，本地记录须对应宿主采用的投影；摘要不能复制旧范围冒充原文。附加说明仍占用请求容量，不因独立展示绕过原限制。

直播插件的公告指令通过 before_model 加入；其 before_commit 按当前插件合同核对邀请与卡片片段。确定性图片提交也经过 before_commit，真实队列回执保存后才调用 after_delivery。工具错误、未知回执和模型生成内容的事实含义仍见[架构](architecture.md)。

消息发送适配器返回 sent 时必须携带平台 message_id，文件上传则须有 file_id；缺对应身份会沿原发送尝试保存 unknown，不由宿主补 ID、重试或换通道。模拟回执只记隔离观察，不激活真实等待或满足后续行动的送达依赖。deferred_delivery 属于原行动的专属调度，不能用普通提醒提案改写或提前触发；插件仍使用原 Scheduler 和生命周期入口，不直接修改任务表。

workspace 与 python_workspace 保留同一实现及各自已有数据目录。共享配置以唯一启用项为准；都停用时只可有一份已配置项，歧义明确失败。只读产物面板优先取原 run_python 注册所有者，无装载入口时才按此规则读取，不跨目录猜产物。两项不是可同时启用的独立执行器，原同名工具冲突仍拒绝装载。

浏览器和媒体也按同一规则取得已解析 Gateway，绑定原 ID 与参数；配置变化须显式重新装载所属插件，不将已存执行或页面引用带去另一个后端。无 Gateway 时仅浏览器保留原本机试用分支，媒体仍拒绝。共享连接并不启用 Python 或扩大公共研究工具目录，场景／主体／工作资格继续各自核对。提交前本地拒绝写入原执行日志，不能当作网关已收到请求。

## 公共资料与外部执行

B 站公共信息、搜索、分 P、评论、字幕使用独立匿名客户端；既有账号动态接口使用自己的账号客户端，字幕资源 URL 不携带默认账号 Cookie。cid 必须属于指定视频；字幕范围为 [start_ms,end_ms)，end 必须大于 start。need_login_subtitle 返回 authentication_required，匿名空轨道只说明本次未取得。字幕 JSON 在下载时按 max_subtitle_bytes 限制，不截 80 条或 400 字后冒充完整；完整取得的匹配时间轴写 R，经已有本地分页续读，sources 保留视频/轨道。评论保留源分页计数及 source_next_call，不把评论者观点当视频事实。

浏览器在 Gateway 模式复用独立 browser 执行与持久命令协议；page_ref 和 revision 不能跨工作/重启借用，截图只有回收并登记后才返回资产。workspace 的联网执行需 network_python_enabled、真实公共工作来源、匿名输入证明、当前 grant 与逐脚本动作审查；控制目录、网络策略和镜像只能由既有配置引用决定。

工具获取产生的 provenance 是来源事实，不能让模型自填公开标记。未知来源的文件、计算或模型摘要不会因放在 system 场景成为公共证据。公共兴趣候选通过 finish_work.public_interests 交回宿主，在完成事务按实际读取范围采用；插件不直接写兴趣表或发布群消息。工具执行异常只由所属宿主边界记录一次，等待中的插件任务不再重复记同一错误。

link_parser 的 parse_link 只获取 metadata，明确 download_media 才进入媒体字节读取。命中同场景缓存但字节不可读或类型不符时返回 cached_media_unavailable；不吞掉该错误后重新下载。没有缓存的正常下载仍通过原字节上限、容器格式验证和资产登记；登记不证明播放、模型阅读或群发送。

### 公共兴趣分享

内置 `interest_share` 插件在全局未配置时为 unconfigured。全局参数为 `max_steps`、`context_tokens`、`output_tokens`；模型使用现有 conversation 绑定。本群参数为 `topics`（空表示所有有效主题）、`daily_limit`（0 不发）、`cooldown_seconds`。还需通过原能力授予向 `principal_type=plugin`、`principal_id=interest_share`、具体 `scene_id` 授予 `interest_share`，不使用 system 公共研究 grant 代替。场景表单按插件 Schema 呈现这些字段。

插件只接受原 Scheduler 的真实槽及自己声明的 candidate 事件，不消费人类普通消息。社会表达复用 `run_agent(..., input_mode='conversation', output_mode='respond')`；仅能读取当前材料、提交至多一条短文字或沉默。此入口不授权文件上传、B 站账号写入或全体提及。候选表达和发送过程均复核当前版本与权限；发出的记录保留兴趣来源，候选被采用不等于消息已送达。

本群主题和排除研究意图先参与候选查询，数量上限在匿名来源核对后生效；采用前回读原修订与有效期。近期表达只引用当前 Bot、同群、原截点内至多四条真实 live 送达记录的前 300 字符，来源 IDs 与实际节选一致。调度候选、表达提交与逐群发送仍是三个事实；面板回读发布记录不触发分享或重放。

### 媒体片段

内置 `media_analysis` 注册 `get_video_segment` 和 `transcribe_video_segment`，均只在 work 中开放。前者参数为明确 bvid/cid、毫秒 start/end、frames（0—12）和 audio；后者使用前次实际 execution_id 与 result_id，不接受任意音频 URL 或宿主路径。结果附件引用真实媒体资产，像素是否装配以模型上下文清单为准，不能从工具完成状态推断已看完视频。

Gateway 的固定 media worker 与 Python/browser 共用原执行协议，但分支互斥，不混入脚本或凭据。新下载动作使用原 ActionReviewer，转写使用同一 ProviderRegistry 的能力绑定并计入原工作，不引入第二个 Agent。前者来源为 anonymous_public，后者保留 derived 及原片段 source_result_ids；宿主不会把 ASR 强制标成匿名原始事实。插件关闭与工作结束回收原执行，结果未知不重新提交同一调用。

### 文件产物

workspace 的 `prepare_workspace_file(path, execution_id, display_name, for_upload=false)` 只用于人类当前工作。返回持久 `file_asset`，不增加媒体 I 引用，也不直接发送。`for_upload` 审查消耗原工作预算。资产经原对话提案/Gate 才能进入上传队列；通知文字须是另一条行动，不能在上传失败后声称成功。支持格式由工具 Schema 明列，不以任意扩展名开放新格式。

该登记入口只接受 Gateway 已确认不可变产物，本机 worker 的普通导出不能冒充持久文件资产。状态读取按原 action_id 区分提交、尝试、真实回执、模拟和 Shadow；只有非模拟且带平台 file_id 的上传回执证明成功。插件不能用 message_id、宿主路径、资产存在或另一行动的旧回执替代它，也不为未知上传重新运行原工作。

工作区的“当前文件”仅指最新 Python 执行的已确认产物；同一工作内的浏览器截图与媒体片段仍走原观察及媒体入口，不按执行时间挤占 Python 快照。显式 execution_id 必须属于该工作的已确认 Python 执行；宿主 worker 不支持该参数并明确拒绝，不能忽略它读取当前目录。输入清单保留导出版本与来源范围，脚本不得把导出成功理解为模型已读或资料正确。工具结果中的原执行结局与产物清单是否可读分别保留，未确认终止和读取失败不变成空目录。

文件回执进入原 `after_delivery`，事件类型是 `FILE_UPLOADED/FILE_UPLOAD_FAILED`，字段使用 `file_asset_id/file_id/file_receipt`。插件不得将 file_id 当成 QQ message_id。上传适配默认缺失，失败保留资产供下载；部署、路径与开启条件统一见运行手册。

### 登录资料工具

`get_dynamic_feed(mid, offset="")` 只在已配置并获准的人类工作可发现/执行。connector 按工作工具预算及原动作审查执行，返回 account 范围的动态正文观察和显式下一页调用。公共研究没有此工具；没有 `desc` 的动态保留其真实身份并说明未读附件，不伪装成已读完整动态。

### 工作中的账号提案

工具声明增量包括 required_capabilities、side_effect、input_scope、output_scope。当前账号写入组合限定为 kind=proposal、roles=(work,)、side_effect=account_write、current_work 输入和 account 输出，并声明独立能力；PluginHost 的发现/schema/执行复用同一权限检查。数据范围声明描述所属边界，不能代替 handler 的真实来源、工作修订、具体资源和审查校验。账号写工具在工作循环中串行执行，返回持久平台动作的 ToolResult；不能调用 QQ 适配器冒充平台回执。B站 connector 独占凭据并执行固定点赞/收藏端点，无 URL、任意 Cookie、任意请求或 toggle 入口。

### Core 支持范围

沿现有 GSUID Core 插件与连接锁，只匹配完整前缀词（默认 `/gs`），上行包含该条命令与直接引用，保留 `onebot` 和与当前适配器一致的实际 `bot_self_id`。命令首次发送前以原消息登记持久身份；WebSocket 提交只表示已转发，不能当游戏业务完成。断线和重启不重放已有命令。

下行首版支持已配置群的文字、at、URL/base64 图片，经过原资产/Gate/发送队列。必须携带 echo 作为稳定帧身份；同 echo 不再次提交群消息。无 echo、语音/视频、普通文件、合并转发、按钮、撤回控制、私聊/频道和私聊登录均明确未支持，整帧拒绝。图片文字形式的登录提示没有独立登录能力，不自动执行账号流程。`after_delivery` 从持久源事件恢复 echo，按完整回执及 metadata 判定真实 sent 后才回填 message_id；unknown、失败、Shadow、模拟或缺身份均不回填成功 ID。回传本身先登记尝试，未知回传不自动重放。

Core 可选，未配置不加载、不建立连接。面板列出代码支持矩阵、现场版本与身份状态；所有已接入项仍标记待现场联调，不能仅填写版本便宣称已验证。
