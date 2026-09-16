# 当前架构

面向维护者。操作、配置、起停与恢复见[运行手册](operations.md)，插件公共接口见[插件开发](plugins.md)，本轮实际核对与未确认项只写[当前任务](iteration.md)。本文描述当前源码结构与非显然约束；代码路径存在不代表已取得真实运行验收。

## 社会 Agent 计划与当前实现

完整目标、D01—D12 裁决、M01—M20 模块和 C00—C29 提交合同见[完整实施计划](LenBot_社会Agent_完整实施计划_7a4152d.md)（保护文件，正文不改）。本文不复制计划对象与未来拓扑。公共兴趣、心跳与睡眠、文件上传及点赞收藏尚未形成当前可用链路。执行后端由 workspace 配置在宿主 worker 与独立 Gateway 之间二选一，样例仍是宿主 worker，浏览器仍在本进程内。源码可把 `run_python` 交给网关并登记出口代理，不等于隔离或公网出口已验收。

## 主链与所有权

运行参数进入 Runtime。OneBot、感知插件和 Scheduler 产生事件，SceneActor 在同一场景写序中先确定交互归属，再保存事实与注意力进度。未被插件消费的普通消息进入注意力和 SocialCognitionCore；插件通过已注册的本地 matcher 认领事件，耗时 handler 在保存后、Actor 写入区之外执行。日历确定性图片与直播专用 Agent 流程由各插件定义，表达仍由 Actor 和 RuntimeGate 提交，再进入 ActionQueue 与真实回执。`start_work` 和 `summarize_group_chat` 暂存的工作由 InformationJobRunner 执行，资料与结果回到原请求群。

| 模块 | 唯一职责与边界 |
|---|---|
| `events/store` | 追加事件、持久事实和事务；数据库保存持续状态，模型上下文不作永久存储 |
| `scenes` | 事实 reducer 与 SceneActor；Actor 是 SceneSession 单写者，不等待模型 |
| `cognition` | 临时上下文、原生循环、短引用和暂存提案；SocialCognitionCore 是唯一社会判断入口 |
| `runtime` | 生命周期、Gate、信息工作、预算与调用账；工作和维护不能直接发送 |
| `scheduler/actions` | 持久认领、到期事件、队列、显式传输与回执 |
| `memory` | 单一认识账本与增量历史；当前没有公共兴趣存储 |
| `skills` | 有来源、场景与版本的方法文档；没有额外执行权 |
| `plugins/tools` | 注册工具与事件处理器、读取资料、调用公共 Agent 和提交入口；模型与发送仍由现有运行时执行 |
| `media` | 原图读取、解码与窗口装配，运营素材及其来源；不调用独立视觉模型 |
| `adapters` | 协议归一化、唯一消息连接与获准传输 |
| `web` | 路由经 RuntimeQueryService 读取限定范围的持久记录，干预使用事件与提案接口，不在路由另写数据库查询 |

事件追加且不可变。Session、认识、任务、工作与等待状态由事件或获准提案更新；原始证据、撤销记录与历史身份保留。认识、工作状态、回执关联和行动提案同事务提交，冲突回滚后确认消息不能入队。运营干预记录 OPERATOR_ACTION，不确认未读的人类输入。

Gate 先返回持久事务的真实结果，Actor 随即采用同次提交的 Session，再由调用者发布 Scheduler 和 Action。发布按同一场景顺序执行，完整准备行动后才入队；Trace 单独保存 committed、commit_event_id、发布阶段、已调度任务及逐条 not_enqueued／enqueued／enqueue_unknown。发布异常不改变 accepted 或把终结调用标成 rejected，不重做事务、不补发；取消发生在等待提交期间时，等待同一次事务的实际结局，已提交则记录发布中断。重复读取已提交轮次只返回原结果和行动身份。未提交候选目录排除存在真实 CONVERSATION_COMMITTED 的轮次。

## 配置与持续数据

ConfigStore 从项目根目录的固定 `lenbot.config.json` 读取 RootConfig。先发现内置和 plugin_directories 中的无运行副作用描述符，再解析 runtime、models、delivery、access、resources、scenes、time、members 与插件 config。面板候选使用同一解析入口。样例、环境变量、CLI 和数据库不覆盖根配置；网络客户端显式禁用环境继承。业务时区未配置为 null，不满足相关插件必需条件时不能启用。

面板保存持有 Runtime.config_update_lock，先校验完整候选并替换根文件，再发布内存设置。需要重建组件的参数记录需重启，不自动重启。运行参数热更新会同步 `EventStore.budget_config`，因此新工作预占与新执行段读同一份已发布上限；已有工作仍读自己的创建快照。文件保存失败不会改用数据库存配置。

插件配置保存按 Schema 路径处理凭据：面板投影删除任意深度的凭据字段，只回可显示值与“该路径是否已配置”；保存时“省略／空串”保持原值、“非空值”替换、显式 `null` 清除，显示占位值永不作为真实密钥回写。凭据只存在于根文件，不进 localStorage、URL 或长期草稿，不写入日志，也不进入模型上下文。根文件本身不挂载进执行容器，网关闭包字段（同一份 token）同样不交给执行容器。

SQLite 保存事件、账号、认识、人工样例、素材、工作/检查点、模型绑定、调用账、预占与运行结果。请求来源、逐来源处理、交付关联与阅读范围使用既有事件和 payload JSON；旧 `request_source_event_id` 缺失保留未知，旧 `observation_reads` 缺省为空，不补造真实阅读或人类身份。

### 发起者与能力检查

工作 payload 可以保存 HumanInitiator、SystemInitiator 或 PluginInitiator；旧人类工作仅由其明确 requester/source 字段转换。JobStore 在提案事务内核验对应真实来源。普通 start_work 与插件 stage_work 仍以已读人类请求为入口。

CapabilityGrant 保存在 `access.capability_grants`，默认空。Gate 对非人类 create/revise/resume 复核当前授予，主体取自原工作；cancel 不另开执行。JobRunner 在每次模型/工具前对非人类再查当前 grant，人类仍走 chat_allowed。information 使用 public_research 授予及其绑定策略；策略名称失效直接拒绝。系统工作的检索范围只有 `global-safe`。并发上限计入创建准入。面板提交可编辑授予字段，认证后再绑定签发者；只改白名单保留原授予。类型中出现 system/plugin 仍不表示计划中的全部自主能力已经开放。

### 预占、调用与结算

`resources.policies` 是具名额度。未配置则各维度 null，不套用 10M/30M。`limit_tokens` 是创建时累计上限：`null` 表示没有 token 维度；缺快照且缺预占上限的旧记录拒绝继续，不当无限。预占与工作行同一写事务。真实 usage 与估算分列，结算按 call_id 幂等。技能候选未完成时预占为 settling，不提前释放。

### 工作与对话的恢复限制

工作保存累计 model_steps、tool_calls 和 elapsed_seconds，并在首次真正开始时写入绝对 `deadline_at`。运行段 timeout 和对话循环 timeout 都对着该持久期限（或对话已存窗口）的剩余时间；排队未开始不计时。没有 `deadline_at` 的旧工作仍按累计活动时长换算，不能补造首次开始时刻。

resume/revise 保留原 ID、资料、模型绑定、累计计数和创建快照；重开 settled/released 预占时使用原 `limit_tokens`，排除本工作后再检查日账。没有存档上限的旧已结算行拒绝自动恢复，不能按当前默认策略补造。无预占的旧工作仍没有 token 维度。

对话可配置 conversation_window_seconds；ConversationResume 保存绝对截止时刻 `deadline_at` 与原窗口值（含明确的 `null`），恢复时按该时刻继续，等待时间计入窗口。人格候选与人工样例仍分别经根配置和数据库接口保存。

## 执行后端与边界

workspace 插件按根配置选择唯一后端：`worker` 由 LenBot 进程调用宿主容器运行时，`gateway` 把 `run_python` 交给独立 Worker Gateway，宿主不再启动容器。两者互斥，网关失败不会回落宿主 `docker run`，一次调用里也没有第二条执行路径；切换是运营者改根配置的操作。工具为 run_python、list_workspace_files、read_workspace_file 和 export_workspace_artifact。WorkspaceService 从已有 work 的 scene、真实请求者和 job 取得工作区归属，不接受模型自填 owner；当前入口仍要求人类请求者。

### 宿主 worker

容器无网络、只读根、非 root、cap drop。取消结局为 confirmed_stopped / confirmed_absent / unconfirmed；未确认阻止复用。`WorkspaceCancelled` 结束等待它的 Agent，不改成普通工具错误。目录字节/文件数是应用层限制。产物与发送分开：导出只登记或供面板下载。输入先校验再整体就位，半成品不留。起停细节见[运行手册](operations.md)。

一次执行的输入有两类，都由**工作本身**决定，不由模型在工具参数里指定路径：一是本工作已登记的观察（`input_result_ids`），二是本工作来源里已经登记过的媒体资产（`input_asset_ids`）——来源事件自身的图片、被引用消息的图片，或本工作自己产出的观察附件。名单是精确且完整的：不在其中的 id 一律拒绝，而不是静默丢弃；因此模型知道一个看似合理的 asset_id 也拿不到别群或别的图片。观察必须是本工作已登记的 result_id，资产必须经本场景的 `get_bytes` 读出并在导出前用 Pillow 验证（媒体能力停用时明确拒绝）。两类合计不超过 8 份、总字节不超过 24 MB；名字按序生成为 `result_N.txt` 与 `asset_N.<ext>`，重复 id 只导出一次。

每次导出都写一份只读的 `manifest.json`，记录 job/scene、输入目录与每个输入的来源身份（观察 result_id／资产 asset_id 与其登记事件）、coverage、状态、字节数、展示描述与来源列表。manifest 描述的是这次导出，不是来源本身；原始文件按不变式放在 `/lenbot-control/input/` 下。

### Gateway

`execution/protocol`、`client`、`journal` 及 `services/worker_gateway` 已有代码。工作必须已有类型化发起者。宿主先写 `execution_runs`，再提交同一 execution_id；`input_assets` 只作来源登记，字节走 `input_files`。等待锁、对账和导出之后，真正入场前再核同一 revision。网关截止取 `accepted_at + deadline_seconds` 与工作 `deadline_at` 的较早者，确认启动也受该时刻约束。

对账结论只有 available / occupied / unknown。活动、取消中、终止未确认都占用。HTTP 404：仅 ACCEPTED 且从未 start 可记未登记；其余 404 保持占用，不证明从未运行。4xx 认证失败是查询资格问题，不是“没有执行”。

当前产物快照只取**最新一次** EXITED / FAILED / TERMINATION_CONFIRMED 且清单可读的执行；占用中或未确认终止不回退到旧文件。历史读取/导出/下载必须带同一 `execution_id`。文本页丢弃前缀，只保留请求页。

出口：`none` 无网；`proxy` 须 `deployment_verified`、代理在跑、宿主 `egress_authorized`。授权只给无群来源的 SystemInitiator 且本次无文本/图片导入。凭据绑定策略与当前执行；撤销后不发新预算。未核验部署不得放行联网。

### 浏览器

`browser_agent` 样例默认停用，工具只在已有 work 下使用。当前 worker 在 LenBot 进程内运行 Playwright；Chromium sandbox 已启用，页面使用独立 BrowserContext 并禁用 Service Worker，但这不等于浏览器已具备独立 OS/网络隔离。HTTP(S) 主机按配置白名单检查；`*` 表示允许任意公网主机，代码仍检查回环、私网、链路本地、保留和未指定地址。应用层 DNS/路由检查不能替代部署出口隔离；缺少 Playwright 或页面被网络规则拒绝时保留错误，不能推定完整网络边界已验收。

临时 page_ref 按工作作用域隔离，snapshot_revision 用于当前元素引用。DOM 返回的是文字覆盖；当前 snapshot 支持 text_offset/text_limit，但重新读取仍会取得页面快照，不能当作已实现持久、不可变快照续读。截图登记为场景媒体后返回 asset_registered=true、pixels_loaded=false，只有后续请求确实装入图片才计为像素已读。交互默认关闭；启用后支持滚动及当前元素引用点击。工具没有提供专用登录凭据或点赞/收藏等账号动作入口，但通用 click 本身不是对任意网站副作用的完整识别与拦截。独立 worker、出口和账号动作边界尚未完成前，不以功能使用记录代替生产隔离验收；同进程插件代码的宿主可达性不能仅靠接口说明排除。

## 输入、注意力与实际阅读

事件保存、注意力扫描、摘要覆盖、本轮原文读取与来源处理分别记录。AttentionPolicy 在事件事务前提供观察机会，扫描位置与待处理唤醒和原话同事务保存；BurstAssembler 只按到达时间聚合获得机会的输入，不决定其中各请求的归属或完成情况。

Actor 提交时校验 episode lease、实际读取集合、读取截点和 knowledge_revision。CONVERSATION_COMMITTED.source_event_ids 保存实际读过的原话；EpisodeOutcome.source_outcomes 经同一事务写入提交事件，并由 reducer 只移除这些来源的 pending_wakes。每项保留 replied/delegated/waiting/incomplete/silent、原因、未完成要求、消息序号和已提交 action/task/operation 关系；这些关系不证明答案语义正确。处理来源必须属于已读集合，并且是当前待处理来源或同一 episode 先前 checkpoint 的来源，定位或部分原文不授予整条处理资格。未处理来源继续保留；一次提交处理了有限来源后，Runtime 可沿现有调度继续其他来源，空提交只能由尚未提供的新输入继续唤醒，不反复领取新预算。

普通聊天按实际读过的快照提交。普通回应、认识修改、工作控制、提醒、履约与 OpenLoop 由 Actor 检查有关的未读确定唤醒：使用原请求者、回应及提及对象、认识主体与证据、真实事项 ID 和 reply 引用关系，不按全量群消息或关键词猜关联。有关追加须先读完，其他独立请求不因此阻塞本项提交。认识版本或租约失效结束提交，不进入通用重试循环。Gate 沿用 Actor 的检查结果；Runtime 从事件存储取得实际新输入，Mailbox 只保存轮次身份、互动参与者与显式取消。

TurnReferences 将本轮短编号映射到真实 ID。历史目录只授予定位，持久记录使用真实事件 ID，跨轮回读重新绑定短编号。长原话与引用按字符范围提供；范围并集完整之前，不能使用整条原话作证据或确认对应唤醒。历史查询还在 SQL 中限制场景与允许读取截点。

模型输入按 kind 分区。chat_message 独立保存 ref、sender、text、mentions、reply_to、时间、正文范围与媒体定位，不把预算、待处理目录或运行说明拼进原话。纯运行元数据使用 developer 消息；含人类或外部内容的运行资料、认识、摘要和表达参考保留为带类型的 user 资料，不提升为指令。首次装配先选当前请求、引用链和按现有邻居数提供的相邻原话，再将选中原话按保存顺序放在参考之后；后续原生工具交换不重新排序。

工具返回或收到新输入不无条件清历史。实际下一次请求超出容量时才外置旧工具正文，依次移出可选参考、摘要与已提供的无关历史；当前及关联原话保留。Trace 的 context_plan.request.messages 记录角色、段类别、真实事件与范围、工具调用 ID 和省略状态；原句从事件库按范围回读，不另存完整供应商请求或 base64 图片。

MESSAGE_SENT 仅为该条 origin_event_id 的明确 @／回复、真实受托事项或未过期的真实等待关系建立短时关注，并保存 focus_renewed_actor_ids。处于关注集合的成员普通发言只提供观察机会，不更新到期时间；弱关注或随机机会带来的 Bot 回复不自行续期。在途来源单独记录，工作参与者按原请求者认定，不把所有资料作者列为受托人。最新发送状态以批次、action_id 和片段关联到原提交，晚于输入截点的回执作为独立运行事实，不推进原文读取位置或授予证据权限。release_focus 在原提案事务内撤销已处理本人原话对应的 focused_participants，不创建长期规则；随后发送该阶段确认时保留撤销对象，避免送达后再次续期。本人纠正误接或要求停止时可随提交撤销现有短时关注，临时结束当前互动不自动变成长久群规则。

ScenePolicy 直接读取当前根文件中的群字段与全局 QQ 回复白名单。群停用时继续保存接入的原话，但不进入认知或发送；chat 关闭群只有白名单请求者获得普通对话资格。发送层保留已提交的文字原样：指定照发只发送要求的文字、标点和换行，不追加角色评论，也不自动把字面反斜线解释成转义；只有用户明确要求显示转义写法时才发送反斜线文本。新工作、群总结和提醒从明确的已读人类 request_source 取得 request_source_event_id 与 requester_qq_uid；控制既有事项保留原请求者，修订原话加入来源集合。每条普通 MessageProposal 单独保存 source_event_id 与 requester_qq_uid，Gate 核验人类原话、场景、实际阅读及当前资格，不再以整轮一个请求人代替多人身份。插件可用性在定义和执行处均检查当前场景、角色与插件状态。

插件路由记录 plugin_routes、plugin_consumed 与 conversation_excluded；日历插件自己认领精确命令及通过真实 reply message_id 找到的日程评论。同一归属用于注意力、默认近期原文、轮中新增原文、历史维护和回应关注；明确的总结查询仍可读取这些人类消息。日程交互不进入普通对话的确定唤醒，也不构成未读控制阻塞。没有引用证据时不作语义评论分类。

## 对话循环与提案

ModelGateway 和 AgentLoop 供对话、工作与维护共用。一次运行固定提供商、型号、推理强度与客户端；对话同时固定配置快照，循环上限、终结定义、上下文装配和新输入使用同一份预算。工作执行段或显式恢复采用当时生效上限并继续累计原账目；三角色分别配置，没有继承、同轮切换、跨型号 fallback 或独立视觉路由。新配置用于新轮次，已启动工作保留原绑定。原生 assistant 续接、供应商扩展与工具调用顺序保留在私有运行数据中。

调用角色与记账用途分开：插件 Agent 使用既有角色的模型绑定，以 plugin_agent 用途写入原 model_calls。直播插件选择 conversation 绑定；调用详情通过真实 run_id/episode_id 关联 plugin_run Trace，来源事件 ID 单独保存。result_only 返回插件声明的类型，不自动发送；直播插件随后明确提交一次邀请。插件调用不写普通对话的 disposition：供应商是否完成由 status 表示，结果与运行失败见插件 Trace，表达和送达分别以提交、行动回执为准；work 路由也通过相同调用身份关联。

模型通过窄原生工具读取与暂存。`respond` 接受本阶段消息、逐来源 sources 和 next=end/continue/wait，空消息列表表示本阶段不发送；同一 episode 的全部 checkpoint 累计最多三条消息。片段恰好填写 `{"text":"一句话"}`、`{"image":"P01"}` 或 `{"at":"U2"}`。成员提及由本轮 U 解析为 qq_uid，OneBot 编码为 at；addressed_to 单独解析为 response_actor_ids，不从请求者、引用作者或等待目标拼成回应对象。ProposalLedger 解析本轮短引用并转换为内部来源与 `type/text/asset_id/qq_uid` 片段。MessageProposal 与 ActionItem 以必填 segments 为唯一消息主体，content 只读派生；Gate、MediaService 和 OneBot 不按 content 重建发送正文。普通模型正文不发送，消息及工作、提醒、认识和等待提案共同提交。

无依赖的只读工具可并发取回，按原调用顺序回填；暂存提案和工作状态更新有序执行。提交工具独占一次模型响应，必须在取得此前全部回执之后调用，不能引用同批尚未返回的新提案。每个 checkpoint 使用独立 CONVERSATION_COMMITTED 事件与 action_id，发送批次绑定该提交，episode_id 另保留原执行身份；重复提交只返回原记录，不再次发布。Actor 原子提交后更新会话/认识版本和累计消息数，Ledger 才清空该阶段；next=continue 在原 AgentLoop 中得到真实提交/发布回执再继续，步骤和工具额度不重置。发布失败不把 accepted 改成 rejected，后续失败仍保留所有已提交 checkpoint。

核心与插件提案直接返回 status、proposal_ref 及对应 ack_ref／operation_ref，仍只表示未提交意向。有限模型次数的最后一步只提供终结工具，定义由当前提案状态生成；这项次数限制不证明累计 token 有足额预留。每次请求和 Trace 记录运行时生成的调用序号、后续模型余量、工具余量；工作还记录累计执行时间余量。压缩计入原账后，重新生成剩余额度、终结定义和最终容量核对，避免把压缩前的工具额度发送给模型。

模型返回的所有调用先核对完整 ID、唯一性、工具名与 JSON 对象形状，再执行。非法 JSON、未知工具、缺失或重复 ID、协议截断保持运行失败；已开放工具的参数类型错误形成 invalid_arguments 观察，无结果、单次超时和可处理业务失败保留各自错误码，在原预算内交回模型决策。正常返回与可处理错误保留对应原生回执，成功资料不因同组另一条普通错误而丢失；运行时不自行修改参数、重做相同调用或开启修复模型。

完整工具交换后才能吸收新输入。`FreshInputConflict` 表示候选因必读输入尚未完整读取而未提交：仍有模型步骤时保留匹配工具结果，在同一运行和同一预算内续接；最后一步直接记录具体冲突，不改报泛化的预算耗尽。成功提交后不再吸收新输入。

`TerminalArgumentError` 只用于尚未提交候选的字段、引用或范围错误，返回同一调用 ID 的工具回执及 committed=false。对话与信息工作把该错误保存为观察，并提供可用纠正位置；仍有预算时由模型在同一循环续读或重新提出候选，新响应必须使用新调用 ID。最后一步错误保留具体未完成原因，不额外调用，也不自动开启新轮次重试旧候选。非法协议、供应商失败、失效租约和真实版本冲突不进入此类纠正。

工作存储仅将事务内已回滚的候选校验标记为 JobResultRejected；结果落库后的完成事件发布失败另记 result_committed=true 与 completion_publication，不返回虚假的 committed=false。

### 工具页与容量

原始观察完整保存；本轮只展示 ObservationPage。字符页与记录页坐标分开，source_next_call 不是本地续读。试算未采用的页不算已读。字符片段不授予原话证据或认识编辑资格。capacity_failure / presentation_capacity_error 表示装不下或未读，不是源为空。

插件 handler 和工具接收不可变 PluginCallContext，携带场景、真实来源、可为空的人类请求者、截点、插件版本、入口、父调用身份与本次调用被准入时的工作 revision。共享插件实例没有可变 current_scene。根场景插件条目按 plugin_id 保存 enabled 和插件自己的 config，专有模型由描述符提供。处理器数值优先级小者先匹配，同级按注册顺序；消费归属与原话一同保存，失败不退回普通聊天。精确命令入口可在普通聊天关闭时执行本插件获准工具，其他插件工具仍履行各自的当前资格。读取定义声明角色与是否延迟发现，返回 ToolResult；提案定义只暂存到现有 Ledger。Toolkit 不按相同参数盲目复用旧结果：源的有效缓存由具体服务维护，原观察按 result_id 显式续读。conversation 与 work 的整组展示共同复用 pack_tool_pages，实际展示后才更新原文覆盖。

新输入装配时更新当前运行事实槽位，消失或省略的事项明确标注；保存观察本身不反复追加完整工作列表。历史查询中的旧工作版本不能覆盖当前目标版本。展示页与续读参数不自动执行，模型实际选择的读取仍沿原工具与模型预算记账，不另建分页循环、后台工作或压缩供应商。

## 工作、任务与交付

InformationJobRunner 使用现有 tasks 与 agent_jobs，同一工作共用实际 ID。任务存储读写 TaskItem，固定列在存储边界解析一次；payload 必须是 JSON 对象，可选 wake_match 区分 SQL NULL 与坏 JSON，不用行长或任意对象猜旧形状。Scheduler 持久认领后追加 TASK_DUE。恢复时只把匹配任务、场景与本次触发的待投递事件视为该次认领；已执行却没有持久结果的工作进入待核对，已有当前结果保留待回应，未知送达不自动重发。

新工作与提醒返回当轮暂存引用，只有对应的确认消息填写 ack_ref；同轮普通回复使用自己的 source，显示引用 reply_to 可独立选择。ack_ref 只能取已返回的本轮真实回执，一个新事项只确认一次。work_ref 指表达所依据的工作，delivery_ref 指本条送达后履约的工作或提醒，operation_ref 指本轮控制或记忆操作的 proposal_ref；每条消息只能选择一种关系，不能相互代替。事务先核对各操作观察到的版本与业务状态，再应用变化；仅明确引用该操作的确认绑定其实际新版本。解析后的 EpisodeOutcome、CONVERSATION_COMMITTED 和 Action 采用同一关系，操作事实保存在提交事件 operation_receipts。发送队列按 batch_id、operation_ref 与 action 核对已经提交的操作，取消确认不因目标进入 cancelled 而误拒；普通旧结果继续按原版本检查，同轮控制与旧结果交付冲突则回滚。

Gate 在同一提案事务中保存确认的 ack_action_id 与结果交付的 delivery_action_id；新确认 ActionItem 另携带 acknowledges_task_id，送达履约仍使用 fulfils_task_id。工作引用与版本从真实对象核验，不能按跨轮重复的 S1 搜索历史事项，也不能将创建确认当成结果已经交付。创建工作确认的 job_id 与 revision 也在事务内写入解析后的消息；提交后的行动准备使用该版本，不在异步发布时重新绑定可能已经变化的工作版本。

面板登录后的无发言管理提案由 Actor 传入可信 operator_control，允许修改／恢复既有工作、修改／触发提醒和其他原有状态管理，不用空 QQ UID 检查聊天资格，也不生成聊天消息。工作和提醒的原请求者保留，后续执行与交付仍检查当前群、插件、版本和请求者资格。管理提交不消费群友未读输入，不记成模型选择沉默。

工作保存目标版本、预算、资料和 WorkState。修订保留原 ID 与创建快照；resume 不改目标，使用新 revision，旧调用按其准入 revision 归档。finish_work 据未决项形成 completed/partial；搜索摘要不能冒充查证完成。预算到边界保存 interrupted，不为写总结再调模型。`result_ready` 只表示终态资料待回应。sent 才完成交付，unknown 不自动补发。next=wait 只允许一条真实期待回应的消息；进程重启把旧等待标为 review_required。

专用工作仍走 JobStore：PluginSpec.work 只提供本插件参数、进度和时限内的 execute，不授予发送或额外工具额度。

群报告 2.0 在 group_summary 内按真实 request_source 固定范围分析并渲染 PNG；read_group_report 只读本群成品，不调用模型或发送。复用分析不计入当前已读，也不成为长期事实。

全局停用或场景关闭取消对应插件的未提交运行和未完成工作；共享任务按其实际归属保留其他场景服务。停用后的等待和未发送提醒进入 review_required，已入队行动、未知回执及已送达事实继续沿原发送状态核对。工作结果引用保留负责插件的版本，出站不能改成普通聊天绕过停用。旧专用工作缺少归属或与当前版本不兼容时保留原数据和中断说明，不自动从头执行；管理取消不要求重新启用插件。

工作对用户可见的结局：执行完成指按当前目标形成完整结果，仍需结合原问题与来源判断结论是否正确；部分完成保留已取得结论与明确未决项；失败／中断表示本次没有成功完成，保留原因、资料与预算并可能显式恢复；结果待回应表示已有终态资料等待对话处理，可能包含失败说明，不代表查询成功或已送达；待核对表示运行中断等情况需要运营或当前对话明确处置，不自动从头重做；等待送达／送达未知表示表达已进入交付阶段，未知不能推断成功，也不能自动补发。

## 认识、维护与方法技能

认识账本保存主体、陈述、reported/inferred、原话证据、有效期与修订链。查询先按允许场景、主体、类型、认识创建时间和有效状态筛选，再复用确定性中文片段与别名排序；昵称、群名片和有效 reported 称呼只作同一主体的检索线索，不合并身份或新增认识。当前互动投影限有关参与者与本群的有效明确偏好，其他认识按需读。角色资料、模型摘要和 Bot 自己的发言不能独立证明群友事实或现实能力。要求忘掉昵称时先查看有效认识，有记录则撤销或替代；未保存为长期认识时停止采用该称呼，不声称清空历史。撤销或替代保留旧陈述及理由。

历史维护沿 ReflectionEngine、LLMReflector 和原 history 存储处理新增原始范围，同批生成摘要与稀疏认识提案，由 Actor 原子保存摘要、认识和覆盖。长事件使用稳定字符分段，文本维护中的图片只记定位和未解读范围。失败、中断或认识冲突不推进覆盖，失败范围由显式操作重试，不因下一条新消息自动重做。LLMReflector 的认识读取按当前请求余量装入完整记录；装不下时保留原 offset，不把截断正文当作已读认识。

技能目录按用途、适用及排除条件确定性检索，返回版本；正文沿普通观察分页，工作首次读取时固定版本。有实际工作观察或明确纠正的候选才触发一次 maintenance，同一次模型调用只接受 save_skill 或 skip_skill 中的一条终结。重复、没有方法价值、来源不足或仅有暂时故障可正常 skipped，原因保存在既有候选结果字段；保存和跳过均核对候选状态与来源工作版本。有效纠正形成新版本并保留前版与依据，人工内容不能自动覆盖，公开针对指定版本。

同一场景、工作和目标版本下完全相同的候选正文复用已有 ID，不同正文仍是不同候选；新候选使用随机身份，历史身份和来源引用不重算。读取或使用技能不增加可信度。历史维护、工作压缩与技能整理均无创建实际工作、任务或发送的权限，群总结也不自动产生技能候选。

## 工具、媒体与传输

工具、插件注册、发现与 Schema 入口见[插件开发](plugins.md)。外部协议在生产者解析一次；持久观察是 ToolResult，不是任意成功字符串。错误不保存凭据或原始 input。别名不是执行入口。历史、观察和媒体的场景隔离在存储层执行。

MediaService 解码与缩放已获准文件，不计算内容校验和；发送已登记资产不要求当前像素。URL 本身不授予像素或发送资格。视觉分析只依据实际装入的像素。ActionQueue 发送前复查范围、工作版本与群；准备失败记原发送失败。Scheduler 是持久认领的单写者，不另建投递队列。真实 MESSAGE_SENT 才确认送达；Shadow 与 unknown 不自动重发。

`gscore_adapter` 默认停用、可选；普通群聊不上报 Core。协议细节见适配器 `SOURCE.md`。

| 发送结果 | 含义 |
|---|---|
| `sent` | 具有真实发送回执，证明实际送达 |
| `not_sent` | 已明确未发送 |
| `rejected` | 候选或发送被当前业务条件拒绝 |
| `unknown` | 结果不确定，不自动重试或更换通道 |
| Shadow／模拟 | 候选或隔离运行事实，不证明真实发送、履约或正常短时关注 |

OneBot 只有一个消息连接；发送通道选定后结果不确定不跨通道重试。令牌不在读取 API 中返回。

## 面板与记录

正文与凭据不进入 URL 或浏览器持久存储。管理列表返回 `{items,total,page,page_size}`。

RuntimeQueryService 按已保存 ID 投影，不以时间相近猜因果。混策略账户不显示由默认策略推导的假余额。凭据按 Schema 路径脱敏，不以占位值回写。保存拆成：已写入根配置 / 运行已应用 / 待重装或重启 / 已保存但应用失败。失败保留草稿并定位字段。概览依赖清单只读已保存记录，不探测外部服务。

ModelGateway 在真实请求前创建唯一 model_calls；真实 usage 与估算分开。Trace 不能代替原始事件或送达回执。语义检索默认关闭，须场景显式开启；语义候选不授予原话证据。

## 必要术语

| 术语 | 边界 |
|---|---|
| Scene | 群聊或私聊的持续隔离范围 |
| SceneSession | Actor 保存的身份、游标、版本与收发事实投影 |
| Read Cutoff / Original Read Set | 快照上界／实际提供原文的集合；上界不表示以前全部已读 |
| Attention Scan / Pending Wake | 调度扫描位置／尚未被明确处理的唤醒来源；读过不等于已处理 |
| Request Source / Source Outcome | 每项请求的人类原话身份／本阶段处理去向、未完成要求与实际操作关联 |
| Episode Lease / Mailbox | 本轮执行权／轮次身份、互动参与者与显式取消 |
| Knowledge Revision | 认识账本版本，变化后旧轮次不能按原认识提交 |
| TurnReferences / ProposalLedger | 本轮定位映射／未提交意向，不是永久身份或执行结果 |
| Tool Observation / ObservationPage | 已保存原始资料／本轮展示页，正文、记录和源端下一批坐标分别表达 |
| Work Revision / Complete Checkpoint | 目标与约束版本／已完成的原生工具交换 |
| OpenLoop | 真实发出后激活的等待回应 |
| Operator Outcome | 有操作事件来源的干预结果，不消费人类未读输入 |

插件自定义来源使用 PLUGIN_EVENT 封套，plugin_id、版本、事件名与 payload 类型属于描述符；旧 LIVE_STARTED/LIVE_ENDED 记录保留。提交与行动保存 PluginOrigin，出站重新检查来源、入口、启用状态、版本及插件业务校验。开播的当前场次、订阅与全体提及许可由直播插件校验。宿主记录并取消插件的轮询、工具与 handler 任务；加载失败清理注册和资源，保留元数据与错误。开发接口见[插件开发](plugins.md)。

插件的 before_model、after_model、before_tool、after_tool、before_commit 与 after_delivery 钩子按声明范围及稳定顺序执行。模型原 usage、原调用身份、观察与回执不改写；参数和提交前片段经过原类型边界，附加资料进入 user 投影，实际变化或停止写入 Trace。消息片段钩子在 Actor 提交前调用，送达钩子在回执保存后由宿主任务执行。AgentLoop 的调用计数通过同一个 AgentBudget 账户收口；工作账户继续委托原 JobStore 计费与时间检查，不另建存储。调用者的 plugin_material 是本次任务输入；plugin_hook_instructions/plugin_hook_material 仅属于当前请求的钩子补充，下一步只清理后两类。输入资料仍参与统一容量与实际阅读范围核对。
