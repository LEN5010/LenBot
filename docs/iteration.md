# 当前任务：社会 Agent 计划归档与前期准备

2026-09-13。已将附件 [`LenBot 社会 Agent 完整实施计划`](LenBot_社会Agent_完整实施计划_7a4152d.md) 纳入 `docs`，并把它标为实施合同而非完成说明。按计划 C00 仅收口产品、架构、执行边界、工程约束和运行手册中的冲突语义：公共兴趣与成员认识分离；`proactive_chat`、`interest_share`、`send_file` 独立授权；GSUID 可选；system/plugin 工作不伪造 QQ 请求者；30 分钟绝对期限与 10M 累计模型预算；当前同进程浏览器、无网络 Python worker 与目标独立 Gateway 分开记录。

本次只做文档归档和静态内容核对，未修改根配置、数据库、模型绑定、人格、Shadow、生产群名单或运行代码；未启动服务、容器、浏览器、Core、模型或 OneBot，未进行真实发送。计划的 M01—M20 与后续阶段仍待按计划逐提交实施和人工验收。

前期工作就绪项：完整计划已与当前 `HEAD`（`7a4152d734b1430f0f42c728cae0c850cc9afae3`）对应；产品、架构、运行手册、执行边界和工程约束均已加上当前/目标区分；`docs/social-agent-readiness.md` 逐条记录计划条款与当前实现的落点、建议复核的两个存量疑点、待确认信息和 C01—C05 首批提交边界。

本次实际执行的静态核对：`git diff --check` 与 `uv run --no-dev python -m compileall -q src/len_bot`（退出码 0）。未运行测试、容器、浏览器、模型或 QQ。

## 2026-09-13 首批 C00—C05 实施

在分支 `social-agent-m0-foundation` 上按计划第 8.2 节完成首批五个提交（实际顺序 C01、C03、C02、C05、C04），并在 [`social-agent-implementation-log.md`](social-agent-implementation-log.md) 逐阶段记录设计判断、实际改动、未做的事、静态核对与未确认项，末尾按计划第 10.3 节模板给出首批验收记录。

- C01 `99be439` 取消与终止结果保留：取消不再被降级为普通 `ToolResult`，workspace 外层期限严格大于内层并覆盖清理窗口，清理子进程有统一上限，终止身份额外保留。
- C03 `98227ff` 日历来源失败状态卡：来源失败与“本日 0 条”分开，失败在同一个 handler 内渲染并提交明确状态卡。
- C02 `3b60fbc` 维护读取容量：`query_memory` 支持模型给出的 `limit`/`kind`/`offset`，返回结构化分页与 `next_offset`。
- C05 `7564df9` 可委托能力摘要：能力事实补充 `delegable_purposes` 与“用 `start_work` 委托”的说明，不暴露 work schema、不新增配置。
- C04 `70c9720` 话题与对象感知的参与：`input_status` 带上既有注意力判定（`reasons`/`certain`），系统提示区分明确接近与弱机会并补现实事实边界。

验收状态：五个提交均为**实现完成 / 未运行**；没有新增表、列或配置字段，不需要离线转换或停机迁移。本批只执行了 `git diff --check` 与 `uv run --no-dev python -m compileall -q src/len_bot`（退出码 0），未启动服务、容器、浏览器、Core、模型或 OneBot，未真实发送。计划第 10.2 节矩阵中与本批相关的 A01、A02、A04、A05、A08 仍待真实业务核对。

下一阶段入口：先确认计划第 2 章 D01—D12 推荐裁决，再根据计划第 13 章补齐 Linux VPS、OneBot 文件协议、B 站专用账号、音频转写和可选 Core 的部署信息；D04/D06/D09 的具体取值直接决定 C06/C07 的字段与判定，开始 C06 前需要明确。未确认项不阻塞不依赖它们的能力编写，也不能先写入生产配置或验收结论。

## 2026-09-13 第二批 C06—C07 实施

按计划第 8.2 节完成 C06、C07 两个提交，逐阶段记录写在 [`social-agent-implementation-log.md`](social-agent-implementation-log.md)（含设计判断、实际改动、未做的事、静态核对与未确认项）。

- C06 `a06b4c6` 类型化发起者与能力授予：`HumanInitiator`/`SystemInitiator`/`PluginInitiator` 判别联合与只有真实事件能取人的 `human_event_uid`；`JobProposal.initiator` 由内部路径构造、模型不可见；事务内按类型分支二次校验；新增窄 `runtime/capabilities.py` 与根配置 `access.capability_grants`（默认空，未配置即拒绝）。
- C07 共享用量原子预占与结算：新增 `usage_reservations`；预占在 `commit_proposal_transaction` 的既有写事务内与工作行同生共死；`measured_call_tokens()` 把真实 usage 与本地估算分成两列；`ReservationPolicy` 给出单工作/账号日/场景日三个维度；根配置新增 `resources.policies`（默认空，不改变既有部署行为），`CapabilityGrant.resource_policy` 按名称解析；模型页新增“工作额度预占”，系统设置页新增“额度策略”。

**C07 本轮实际执行的本地核对（非运行服务，临时库核对后删除）**：两次 10M 预占后账号占用 20,000,000，第三次被账号日额度拒绝并给出可读文本；场景维度独立计数并按其上限拒绝；无模型调用的工作关闭后 `released` 且不再占用，已调用模型的工作结算为真实 `(1500, 0)`、账号占用从 20,000,000 降为 1500，次日为 0；`BEGIN IMMEDIATE` 内先插工作行再触发拒绝并回滚后，工作行与预占行均为 0；同锁并发创建同一份余额时一个成功一个被拒；同一 `job_id` 下的工作调用、压缩调用与插件子代理调用汇总进同一账。静态核对为 `git diff --check`、`uv run --no-dev python -m compileall -q src/len_bot`（退出码 0）与 `npm run build`（442 modules，成功）。

验收状态：C06、C07 均为**实现完成 / 未运行**。C07 只做创建期预占与结束期结算，**执行中按 token 或 deadline 真正停止属 C08**，本轮不得宣称“超额会自动停止”。D06 的取值（单工作 10M、账号日 30M、场景维度是否设限）仍按计划推荐裁决实现，未获用户逐项确认；运营者可在“额度策略”页改，改数值不需要改代码。计划第 10.2 节矩阵中与本批相关的 A03、A06、A07、A09 待真实业务核对。

下一阶段入口：C08 `feat(agent): enforce resource budgets across native loops`（依赖 C07），完成条件是 None 次数模式无漏算/类型错误、deadline 与 token 真实停止、预留终结能力；随后 C09 `feat(jobs): preserve revisions and budget ownership on resume`。

## 2026-09-13 第二批 C08 实施

按计划第 8.2 节完成 C08 `feat(agent): enforce resource budgets across native loops`，逐阶段记录写在 [`social-agent-implementation-log.md`](social-agent-implementation-log.md)。

本轮不是“给类型加 `| None`”，而是把 `None` 语义、判定规则与停止条件一起落地（计划第 8.3 节把只用 `None` 放行、不改循环终结判断列为禁止的半成品）：

- 新增 `count_remaining(limit, used)` 与 `tightest(*bounds)` 两个纯函数，`AgentLoop`、对话、工作、维护、压缩、报告批次与插件子 Agent 的判定全部改到它们上；`job_max_steps`、`job_max_tool_calls`、`conversation_max_steps`、`conversation_max_tool_calls`、`maintenance_max_tool_calls` 与插件 Agent 的两个次数字段改为 `int | None`。
- `AgentBudget` 增加绝对 `deadline` 与从持久记录读到的 `tokens_limit`/`tokens_used`；`_refusal()` 在启动下一次调用前给出 `elapsed_time`/`model_steps`/`tokens` 三种拒绝；`terminal_seconds_reserve` 与 `terminal_token_reserve` 保证终结本身仍有输出与时间。
- 工作的时间与 token 维度改由持久记录说话：`budget_state()` 读 `agent_jobs.elapsed_seconds`/`model_steps`/`tool_calls`，token 上限取该工作的 `usage_reservations` 预占额、已用量按同一 `job_id` 汇总全部 `model_calls`（含压缩、技能维护与插件子 Agent）。C07 的预占在这一步才成为真正的执行期硬上限。
- 对话轮次新增可选 `conversation_window_seconds`（默认 `null`，升级不新增限制），`ConversationResume` 新增 `elapsed_seconds_limit` 使等待恢复继续同一个窗口而不是重新计时。配置在解析期拒绝“次数与期限同时为 `null`”这类没有任何停止条件的组合。

**C08 本轮实际执行的本地核对（非运行服务，临时库核对后删除）**：不设次数 + 30 token 额度在第 3 次调用被 `tokens` 拒绝（此前两次正常），不是死循环也不是首轮误判；不设次数 + 过期期限在第一次调用前即以 `elapsed_time` 拒绝；只剩终结预留时第 1 次调用就只给终结工具且工具一次未跑；额度充足时工具保持可用并正常终结；`window_deadline(600, 0/400)` 为 600/200 秒（恢复不重置）；有限次数 3 仍与既有“最后一次预留终结”行为一致；工作账户把同一 `job_id` 下的工作、压缩与无 usage 维护调用汇总进同一份已用量。静态核对为 `git diff --check`、`uv run --no-dev python -m compileall -q src/len_bot`（退出码 0）与 `npm run build`（442 modules，成功）。

验收状态：C08 为**实现完成 / 未运行**。D05 的 1800 秒仍未获确认——本轮实现的是“绝对期限不重置、排队不计入”的语义，默认值仍是根配置既有的 `job_max_seconds=600`；把 `conversation_window_seconds` 从 `null` 改成数值才会让对话轮次也有期限维度，两者都只需改配置。计划第 10.2 节矩阵中与本批相关的 A10、A11 待真实业务核对。

下一阶段入口：C09 `feat(jobs): preserve revisions and budget ownership on resume`（依赖 C08），完成条件是继续不重置模型/预算/deadline、授权撤销限制后续操作、旧执行不会写新修订。

## 2026-09-13 第二批 C09 实施

按计划第 8.2 节完成 C09 `feat(jobs): preserve revisions and budget ownership on resume`，逐阶段记录写在 [`social-agent-implementation-log.md`](social-agent-implementation-log.md)。

C09 的落点不是三处新判定，而是让同一条既有规则在**控制路径**上也成立——恢复既然是“同一工作的下一次执行”，就必须重新受同一份额度与**当前**授权约束：

- 恢复/修订重新预占：`runtime/job_store.py:rehold_job_budget_in_transaction` + `cognition/call_store.py:rehold_work_in_transaction`。工作结束时预占已结算成“实际花了多少”，那一行不再表示“还能花多少”；直接拿它当 C08 的 token 上限会让工作在自己的第一次调用上就被判定超额，即“恢复”变成“立刻停止”。重开只作用于 `settled`/`released` 行，`reserved_tokens` 回到工作的累计上限，行与原 `day_key` 保留，不新增额度也不搬日子。
- 不重复收费：`account_used_tokens_in_transaction` 增加 `exclude_job_id`，重预占复核日/群额度时把自己排除。
- 恢复过当前 grant：`runtime/gate.py:evaluate_and_commit` 的授权循环按操作分支，`resume` 用**工作自身已存的发起者**（新增 `runtime/job_store.py:initiator_of`）过当前授予；撤销/停用/过期即拒绝恢复，revise 与 cancel 不启动执行、保持原样。控制者不能借恢复替别的来源扩权，人类主体的工作仍走既有白名单路径。
- 旧执行写新修订：沿用既有 `revision` 匹配（本次只核对，未新增规则）。
- 旧工作不发明额度：C07 之前的工作没有预占行，恢复时保持“没有 token 维度”由期限停止；有预占行却无从归属发起者的记录由重预占拒绝并给出原因。

**C09 本轮实际执行的本地核对（非运行服务，临时库 `/tmp`，核对后删除）**：结算后再恢复，预占行从 `settled 1500` 变回 `held 1929216`、`day_key` 仍为原来那一天，恢复后计数 `revision 2 / model_steps 6 / tool_calls 5 / elapsed_seconds 38` 未清零；修订走同一分支（`held 1929216`，任务回到 `pending`）；该账号当天总占用为两个工作各 1,929,216，未把同一工作算两次；无预占行的旧工作被拒并给出 `This work has no typed initiator; continuing it would have no account to hold against`；用假配置源注入 grant 时，grant 存在则恢复无拒绝，`enabled=false` 与 grant 不存在都以 `Capability check refused at step 当前 grant` 拒绝；`save_job_compression` 对 `revision=0` 与已取消工作的 `revision=1` 均被 `JobChanged: Compression belongs to obsolete work` 拒绝。静态核对为 `git diff --check`、`uv run --no-dev python -m compileall -q src/len_bot`（退出码 0）、`ConfigStore.load()`（实际根配置仍能加载，`resources.policies == {}`，未改根配置）。本次无前端改动，因此未重新执行 `npm run build`。

验收状态：C09 为**实现完成 / 未运行**。本轮没有跑真实的“先建工作、再撤 grant、再恢复”端到端流程，也没有在真实规模上触发“额度不足”拒绝文本；D05 的 1800 秒与 D06 的具体数值仍未获逐项确认。计划第 10.2 节矩阵中与本批相关的 A08（权限撤销）、A09（恢复不清零）待真实业务核对。

下一阶段入口：C10 `feat(execution): define owned worker protocol and journal`（依赖 C09），范围是 `execution` 协议/客户端、`execution_runs` 与最小 Gateway 服务；计划第 8.3 节把“添加 Gateway 客户端后继续在失败时回落宿主 `docker run`”列为禁止的半成品，新后端与旧宿主路径不能并存。

---

# 当前任务：A/B 审查修复与单群报告

2026-09-10。A/B 基线为 `cf0bc6d`。用户要求修复新增审查报告、参考 [单群日报项目](https://github.com/LEN5010/astrbot_plugin_qq_group_daily_analysis)，并审计刚才的真实运行。现行契约归[产品](product.md)、[架构](architecture.md)、[运行手册](operations.md)和[插件开发](plugins.md)。

## 本轮实现

| 阶段 | 行为变化 | 本地提交 |
|---|---|---|
| 公共插件 Agent | 移除非法 disposition 更新；分开调用者资料与临时钩子资料；专用循环复用普通对话的工具正文、图片与阅读范围装配 | `4eae038` |
| 单群报告 2.0 | 程序安排批次、保存与复用分析、合并候选、计算统计、原话提取金句；本地渲染图片并沿原工作交付；面板可读 JSON、图片和阶段进度 | `5136845` |
| 运行审计收口 | 指定照发与真实换行进入发送协议；历史动态关键词工具直接可选；新工具正文先于可选旧上下文分配容量 | `4eea141` |
| 容量与媒体装配收口 | 可选上下文省略不再写回占用额度的标记正文；同一工具组的图片读取在试装与最终装配间复用 | `c6b2670` |

插件的执行入口沿用原工作账户、绑定、取消和 revision。焦点、额外目标及附加要求进入实际分析；新要求不继续采用旧要求的派生进度。已读、已分析和复用分别保存。自然日从请求原话的时间确定；缩小时段不复用范围外引用。渲染恢复读取已有 JSON，原回执未知不重发。原请求者仍有新话待处理时暂留 result_ready，原对话处理后再核对成品首次交付。没有新增表列、发送队列、摘要校验或独立模型客户端。

新报告目前只有代码与构建结果；旧分页总结不能算成其运行验收。随后一次 2.0 工作实际匹配并读入 201 条，但两次结构化终结候选缺少字段，未保存成功批次；工作 `job_1fe7daa5221b5170b4fc` 保留 `result_ready/failed`、全部原始资料和累计预算。代码已放宽非事实性的点评字段、允许完整来源引用，并把缺少摘要的话题列为未确认；下一次显式恢复才会验证。当前范围内不会默认每日广播，旧 1.1 工作保留原始资料、结果和回执，不自动转换为 2.0 的成功分析。

## 刚才运行的实际观察

用户在 09-10 00:55—01:43 左右运行基线。数据库保存 105 条群入站、25 条 MESSAGE_SENT，模型含 50 次普通对话（1 次取消）、10 次工作和 2 次压缩，没有插件 Agent 调用；审查报告中的三处公共缺陷属于静态确认，不能声称已在此次现场复现。

- 00:55:35 “今日直播”被日历 handler 消费，`trc_bbcf039f506f` 因 ICS 403 结束，没有回落普通聊天。
- 00:57:50 “我的意思是让你发这四个字 今日直播”对应 `trc_48895a91c52e`；00:57:59 的送达 `8c50447c-7f2e-453a-bdcd-66d38e72b56f` 仍追加角色话术。01:14:22 的送达 `d0f61ad0-1c66-42b3-92da-246c3221a4ea` 含字面量反斜线换行符，用户随后指出。
- 00:58:56 的称呼纠正进入 `trc_8feafdbabc19`，query_memory 后 remember 暂存并随确认提交；不能再记为完全没有记忆操作。
- `job_be63aca8d9a45ffaadf7` 匹配 3,491 条、33 人，实际完整读过 150 条，使用模型 12 次（含压缩）、工具 9 次。正文只概括最后一段并自报 58 条；真实部分交付 `05241cd9-9ba2-404a-b8fa-b487315ccc27`，行动 `6139c341-3e36-4b38-b09d-7ddc347753cc`。00:59:28 原请求说“今天”，参数却覆盖 09-09 零点至 09-10 01:00。新流程已从入口修正日期解析，并按成功批次定义完成度。
- `trc_302d09eaf5b1` 对“搜一下历史动态，关键词鹅肝手握”只调用网页和群消息搜索，没有调用历史动态工具；两条群消息结果因呈现容量未读，最后却说动态里没有。属于未完成检索和错误结论，不是 no_results。

指定文字、换行和来源选择已分别补入正常消息协议与对应插件工具说明；发送层不自动解码字面反斜线。新工具正文超出余量时先腾出可选旧上下文，仍无法装入就保存未读失败。实际运行中 25 条文字发送的长度中位数为 58，P90 为 131.4，8 条超过 70 字，4 条超过 100 字，最长 142 字；偏长内容常在回答后又追加推荐、安慰、提醒或追问。已用 Gemini `gemini-3.8-flash-high` 润色三个人格字段并保存，调用耗时 6392ms、usage 1509 tokens。新一轮 13:41—17:34 的真实日志仍出现 19925/19904、20250/19904 和 20187/19904 的容量失败；根因已定位为省略标记及同轮媒体重复读取并修复，尚未观察修复后的真实请求新模型输出或送达，不能写成效果已通过。

## 配置与已做核对

升级备份保留在 `.backups/20260909-212148-before-A-start/`、`.backups/20260910-002519-before-B-config/`。本次 01:53 确认无 11307 监听或当前数据库运行句柄后，普通备份实际配置、SQLite、完整媒体及基线代码到 `.backups/20260910-015316-before-review-fixes/`。

本轮给实际根配置的群报告补上 render_font_path，使用仓库已有字体 `../asoul_calendar/resources/font.ttf`，经同一 RootConfig/ConfigStore 离线解析保存；随后将用户提供的 Edge UA 原样写入 `plugins.asoul_calendar.config.user_agent`。停机核对时，`curl --noproxy '*'` 得到 HTTP 200、`text/calendar`、5531 bytes，实际日历插件的 httpx 也成功解析 13 个事件。原模型、角色资料、人工样例、Shadow、两个群及其开放项没有更换；人格三段表达字段按用户要求用 Gemini 润色后保存。样例新增完整但停用的群报告配置；不参与运行合并。

正常 Python 编译、前端构建（441 modules）、差异格式和受影响 Markdown 引用按交付要求核对。页面源文件已阅读；新页面实际展示、报告渲染、插件 Agent 最终请求和新回执仍待获准启动后的人工使用确认。除用户明确要求的一次人格润色 API 调用外，本轮没有启动生产、调用业务模型或发送 QQ 消息，也没有新增、修改或运行测试、夹具、断言式探针、自动截图或覆盖率。另存 `.backups/20260910-121521-before-ics-and-run-audit/` 与 `.backups/20260910-132148-before-persona-polish/`；未推送。

## 仍需真实使用核对

本次前端与维护链路修复：信息工作筛选区改为可收缩横向布局，左上角品牌标记改为由构建产物管理的 LenBot 极简 SVG；对话提示补充庆祝、吐槽和明确表情包请求时的素材选择规则。历史维护批次按完整请求（系统提示、工具 schema、JSON 原文）核算输入容量，重载和显式重试按已保存的 source_event_ids、顺序与偏移恢复同一批次，维护失败页面可读取对应 trace 的具体原因，重试页面会等待并显示该批次的终态。尚未在生产启动后观察新代码下的维护重试、模型实际输出或 QQ 送达。

A 的实群验收此前明确跳过；不能将旧失败或缺少观察改为通过。保留的原场景包括“评价一下你的创作者”把运行标签当群友措辞（`trc_50d948c14bdb`），以及身份否认后继续猜另一种身份（`trc_0bcc2de675e6`）；原事件与回执均保留。旧部分工作 `job_7fd2432a0551568e86ca` 的 250/515 条和送达 `0c844ef0-3117-4fef-a2a4-e7c521fd6a16` 也没有重写。

09-09 20:59 六个已配置直播房间均有一次 HTTP 200 并通过解析，只能确认该次来源读取，不能推定持续采集或公告送达。停机后用用户指定 Edge UA 直连 `https://asoul.love/calendar.ics` 得到 HTTP 200、`text/calendar`、5531 bytes，实际日历插件解析 13 个事件；配置改动需下次启动后再观察群内消费与发送。

修复后的确定性命令、result_only 返回、调用者资料保留、图片工具的实际像素输入、报告批次复用与渲染、实发回执，以及纠正、真实提及和未完成工作继续，需要回到正常原群链路观察。

## 2026-09-12 记忆与链接计划实施观察

本轮继续完成了计划中的静态实现：历史批次严格按已保存来源恢复；读工具额度与等待资格分开；result_only 的 Pydantic 参数错误转入既有纠正分支；初始插件资料复用正文、分页与图片装配；上下文优先当前连续性和既有摘要。新增检索模型绑定（embedding/rerank，默认空）、独立调用计量、只为已提交认识建立的可重建 SQLite 向量索引、按需语义候选合并、历史摘要定位工具，以及 B 站 BV/av 链接的原生解析插件骨架。新增能力默认未启用，未把任何凭据写入仓库，也未发起业务模型或 QQ 请求。

已实际核对：`uv run python -m compileall -q src/len_bot`、前端 `npm run build`（442 modules）和 `git diff --check` 成功；插件目录发现包含 `link_parser`。随后补上 P2 的最小宿主贯通：MessageSegment、ActionQueue、OneBot payload、发送前媒体解析和媒体缓存现在识别 video/audio；下载使用临时文件、字节上限、场景素材登记与取消清理。B 站 link_parser 增加可选公开播放地址解析和 `download_media`，仍需 Agent 或确定性宿主提案决定发送。设置页增加从已确认真实 `MESSAGE_SENT` 事件创建表达样例的入口，并按当前输入在本地做稳定相关性排序；认识页显示场景索引覆盖和最近索引错误。未运行测试、生产服务、真实群聊、检索供应商请求、下载媒体或 B 站发送，故上述行为均仍待获准环境人工观察。检索模型具体 ID、维度、rerank 协议和服务权限未确认，配置保持 null；其他平台未实现。

计划项静态交付核对：M0.1—M0.4、M1、M2、M3、M4、M5、M6.1、M6.2、M7、P1、P2 的代码、配置与文档入口均已存在；P3 按本轮只批准 B 站的范围不增加其他平台。仍缺的是运行环境观察：模型供应商协议与凭据、真实索引覆盖、真实摘要找回、OneBot video/audio 接受情况、B 站播放地址有效期和真实回执。
`download_media` 现在只能接收本场景已保存的 `parse_link` 结果引用，不接受模型自拟 URL；rerank 必须在根配置中填写已确认协议，否则不会发起请求。初始插件资料中的视频/音频附件只作为可发送媒体登记，不误报为已读图片像素。
语义检索还需要在每个场景设置中明确开启；未开启的群不会触发索引或查询提供方请求。
场景设置页现已提供该开关，认识页可对已开启场景执行显式索引重建，并显示认识与历史摘要各自的覆盖量。
本次只读检查未发现 11307 面板或 LenBot 进程；13000/13001 为现有 Docker 监听，未通过它们启动或发送任何消息。

## 2026-09-12 06129e4 复审 R1—R4 修复

按 `LenBot_06129e4_review.md` 修复四项剩余问题：embedding 原始响应显式请求 `encoding_format="float"`；语义 query 的 embedding 与 rerank 共用场景 epoch guard，并在本地调用记账等待后、实际 HTTP 请求前再次核对关闭状态；下载成功结果从已验证资产 MIME 派生媒体类型；历史摘要日期范围下推到向量候选 SQL 后再取 top-k。

本轮尚未提交；已完成的检查为源码编译与差异格式检查，未调用业务模型、B 站、OneBot 或 QQ。真实提供方的浮点响应、媒体容器和送达仍需获准环境人工核对。

## 2026-09-12 严格审查 F1—F7 修复

针对 `5eeb738` 审查报告的 F1—F7 已完成源码修复：向量候选和覆盖统计现在连接当前认识原表并复用人物、类别、时间、状态、有效期与修订条件；重建清理不再使用开始时的旧 ID 快照；场景关闭语义检索后，索引批次在并发位、请求和写入边界停止并记录取消；历史摘要保留词面/语义并集、遵守读取截点且区分批次身份与工具资料引用；初始插件资料在最终图片窗口形成后核对全部必需图片；OneBot 音频出站映射为 `record`；媒体下载按实际容器字节校验，非媒体响应不登记为成功资产，播放地址请求只发生在明确下载入口。

本轮实际执行 `uv run python -m compileall -q src/len_bot` 和 `git diff --check` 均成功。尚未启动生产、调用真实检索供应商或 B 站、发送 OneBot/QQ 消息；因此外部协议接收、真实媒体有效期和送达回执仍未作运行验收。
终结 `respond` Schema 和插件直接提交路径也已开放 `video`/`audio` 片段，并在提交前按素材 MIME 类型复核，避免仅在发送适配器层支持而模型无法提出媒体。
相同场景、相同已确认播放地址会复用已有完整媒体文件；再次发送仍需新的提案和送达回执，不把缓存命中当作已发送。
检索结果在索引尚未覆盖完整账本时返回 `partial` 并在 Trace 写入候选、重排和覆盖状态；显式重建会清理旧来源的派生行。Embedding/rerank 的长期 HTTP 客户端随 Runtime 停止释放。
# 2026-09-12 下一阶段计划实施

基线：`fb73fa9`。本轮实现共享亮色卡片、已有插件入口、GSUID Core 桥接、隔离 Python worker 与浏览器 worker；后三项默认保持停用，运行验收仍待环境、Core 版本和隔离后端信息明确。

已改动：

- 新增 `src/len_bot/cards/` 共享亮色主题、文本测量与绘制基础组件。

- 日历、直播、群报告和动态模板改用 LightThemeV1；群报告成品现在以有序附件列表交付（旧单图字段继续兼容）。
- 直播公告保留原文案并同一次提交卡片；日历引用不会因引用卡片而被宽泛吞掉；动态增加只读的单条卡片渲染入口。
- 新增默认停用的 `gscore_adapter`、`python_workspace` 和 `browser_agent`。Core 适配、隔离容器与域名白名单浏览器均沿现有插件和 ToolResult 边界工作，见 `docs/gscore-adapter.md` 与 `docs/execution-boundaries.md`。
- `gscore_adapter` 已按 `MessageReceive`/`MessageSend`/`recall_message_id` 帧边界实现，Core 输出的群目标经过场景校验，实际 QQ 回执由 `after_delivery` 观察后再回 Core。`workspace` 工具只在已有 work 中按 scene/requester/job 归属执行；控制面板提供产物清单和分页读取入口。浏览器使用工作级 page_ref 与观察版本，默认拒绝交互和空白主机白名单。

本轮仍未启动 Core、容器、Playwright、模型、OneBot 或 QQ；配置、协议版本、隔离运行时和浏览器依赖需要部署环境确认后再做运行验收。H01—H04（Hermes 式程序化工具编排、技能复用、有状态内核/长期浏览器、多后端及其他暂缓能力）保留为后续 TODO，不在本轮启用。

本次静态核对包括全部 Python AST、内置插件目录发现、实际 `ConfigStore.load()`、示例配置模型解析、卡片渲染冒烟和 `git diff --check`。前端用 `npx vite build --configLoader runner --outDir /tmp/lenbot-dist` 成功构建 442 个模块；受当前文件系统权限限制，不能覆盖仓库内已有 `web/static/dist`，因此生产静态构建产物仍需在有写权限的工作区重新生成。未运行测试、生产服务或真实外部连接。
- 日历与群报告使用亮色 token；直播公告增加同场次事实卡片。
- 日历引用不再因宽泛 `interaction` 字段被插件吞掉；直播提交校验允许正文与图片组成同一交付。

未确认：实际 QQ 发送、直播卡片的线上视觉效果、Core 服务、隔离执行后端和浏览器网络边界均未在本轮启动或实发验证。

继续推进：修正浏览器 worker 的 DNS 检查，使已在域名白名单中的主机解析到公网地址时不被错误拒绝，同时仍拦截私网、回环、链路本地、保留和未指定地址；JobRunner 在工作最终结果保存后统一调用插件资源清理，结束浏览器页面生命周期。以上仅为源码修改，未启动浏览器或工作容器验证。

2026-09-12 评审 E01—E08 修复：Python 控制脚本和输入改为宿主独立只读挂载，工作执行按工作串行；运行超时、取消和产物总字节/文件数超限按唯一容器名终止并清理，stdout/stderr 改为流式限量读取，文本读取不再整文件入内存。工作区图片导出复用 `save_image` 登记媒体，面板新增带工作授权的原文件下载入口。浏览器入口保存 `PluginContext`，页面上下文禁用 Service Worker，并覆盖创建页面时的取消清理；浏览器 OS/网络隔离仍未实现，插件继续默认停用。Core 连接创建/关闭共用生命周期锁；动态卡片参数统一使用已解析的 `result_id`。本轮只做源码、文档和前端构建检查，未启动容器/浏览器/Core、调用外部模型或发送 QQ。
群报告分页高度改为沿用 Pillow 实际字体测量，直播标题超出四行时显示省略号；这改善确定性布局但仍未替代获准环境中的 QQ 压缩与可读性观察。

只读环境核对：主机可访问 Docker Engine，但默认 `python:3.13-slim` worker 镜像尚未存在；当前 uv Python 环境未安装 Playwright；运行中的容器与 LenBot 新能力无关。根配置未启用 `gscore_adapter`、`workspace` 或 `browser_agent`，也未发现可用于 Core 联调的已确认端点。未执行拉取镜像、安装依赖、启动服务或外部发送。

2026-09-12 配置准备：按用户要求保持 `gscore_adapter` 未加入根配置；根配置与样例已加入 workspace/browser_agent 的安全默认参数并保持全局及两群停用。浏览器白名单设为 `*` 时仅允许任意公网主机，私网/回环等地址仍由 worker 拦截；Docker 镜像拉取因当前 Docker socket 权限不足且命令被中止，未改变运行环境。浏览器隔离和 Playwright 依赖未完成前不启用 browser_agent。

2026-09-12 测试环境准备：Docker Desktop 已拉取 `python:3.13-slim` 并构建本地 `lenbot-workspace:py313` 镜像，预装 NumPy、Pandas、Matplotlib、Pillow 和 Noto CJK 字体；宿主 uv 环境安装 Playwright 1.62.0 并取得 Chromium。实际根配置已将 workspace 全局及两群启用，使用 `/opt/homebrew/bin/docker`、镜像 `lenbot-workspace:py313` 和本机非 root UID/GID `501:20`；browser_agent 仍全局及两群停用，GSUID Core 未配置。尚未启动 LenBot 或进行真实群聊发送。
2026-09-12 浏览器功能测试批准：运营者明确批准同进程浏览器的任意公网主机功能测试；实际根配置已启用 browser_agent 全局及两群，GSUID Core 仍未配置。该启用不代表 OS/网络隔离验收通过，未启动 LenBot、未访问网页、未发送 QQ。

2026-09-12 W1—W5 修复：workspace 未显式指定容器用户时跟随启动进程 UID/GID；宿主读取统一使用目录描述符、`O_NOFOLLOW` 和 `fstat`，避免检查与打开之间的路径替换；终止流程记录 kill/inspect/rm 结局；进程退出后执行最终字节数和文件数核对，超限产物保留 `over_limit` 标记；图片登记失败返回 `partial` 及具体错误，产物媒体字段改为 `asset_id`。本轮仅完成源码编译和差异检查，未运行容器、浏览器、Core、模型或 QQ。

W1—W5 复审落实：上述 workspace 边界修复已实际写入当前工作树；浏览器功能测试仍按运营者批准运行，未宣称隔离完成。运行环境状态（Docker 镜像、Playwright、根配置）与源码提交分开记录，未把未运行的容器或 QQ 发送写成通过。

## 2026-09-12 20:22—20:37 实际运行审查

本次只读审查已运行实例、SQLite 原始事件/观察/调用账/Trace，以及现有登录面板的运行日志与插件状态。实例 PID 1906 于 20:22:02 启动，晚于当前 `60ca881` 提交；统计截点为 20:37:16。未修改运行配置、重启服务、调用业务工具、重试失败批次或发送消息；本节是本次新增的审查记录。

- **收发链已实际工作。** 此范围收到群 `126300994` 的 97 条原始群消息；11 个模型对话轮次中 3 个表达、8 个沉默，另有一次运营撤销认识提交。17 次对话模型请求、1 次历史维护模型请求；没有新信息工作。3 条发送均有 `delivery_status=sent`、真实 OneBot message_id 和 `simulated=false`，全部为纯文字。原话到回执耗时分别 14.904、10.540、12.140 秒。没有本轮失败/未知发送事件。
- **精确日历命令未完成。** 20:22:44 和 20:24:21 的 `get_live_schedule` 均返回 `network_error`，原错误为 `Error: ConnectError: ；本次网络请求失败，未取得来源。`。第二次来自人类原话“今日直播”（`38e1a9ce-3e4f-490a-a66d-5a7c05efd8b4`），入口已消费，handler 随后失败；`trc_f387f7a55ded` 保存 `日程来源未完整取得：Error: ConnectError: ；本次网络请求失败，未取得来源。`，未产生相应卡片或群内失败反馈。当前证据没有 HTTP 状态，不能归因于原来的 403 或 UA。
- **历史维护发生容量失败。** 批次 `c1f8e18575f046839dfa48f828829480` 的 `trc_96b74bd9253c` 在 20:32:40 记录：`历史维护请求需要 20658 token，可用输入容量为 19904；原区间未推进`。第一次模型请求已成功，随后调用 `query_memory({})`；工具正文增加约 4677 个本地估算 token，第二次模型请求在容量检查处结束。失败范围 `12747:0 → 12858:229` 没有成功摘要。第一请求的批次测量没有解决后续工具正文加入后的容量问题；不能将调用账的“请求完成”当作批次成功。
- **B 站访问成功，但未验证 Playwright。** 20:28:48 的明确访问请求（`dbbe949b-977f-4697-b4d3-df96b70b179d`）仅调用 `read_page`，观察 `22e63eb43c764e1c882b2d9ea25aadd5` 返回 HTML 提取文字与图片链接，包含 KPL 轮播图标签，attachments 为空。回复“看过了~B站首页一切正常，轮播图正在播KPL，还有好多热门视频呢！”已送达（`154b41ca-a215-45d3-84b9-83b693c5c180`）。KPL 描述有文字来源；“一切正常”超出了文字读取能确认的交互/渲染范围。本轮没有 `browser_open/snapshot/interact/capture`、`run_python` 或工作产物导出调用。两插件在日志和面板中已加载，但工具仅开放 work 角色，普通访问问题本次未创建工作，不能作为其执行验收。
- **人格表达仍扩写无依据的现实行动。** 20:29:20 的“你不是在直播吗”（`ee2cb259-1200-4a62-9524-f8c4f87a7cc3`）取得嘉然房间正在直播的样本后，Bot 回复“诶？！被抓包了！趁换场偷偷溜出来看一眼，这就回去播啦~”。`trc_59162328e15b` 与真实回执 `058fe741-0697-4af4-baf0-2920f2de76ec` 对应。样本只证明房间状态，没有换场或 Bot 离开/返回直播的证据；这属于角色演绎，不能沉淀为真实经历。
- **其他能力的边界。** 20:22 的官方动态成功提供当晚 8 点 A-SOUL 夜谈及房间链接，因此首条回复有来源。直播监测已持续取得新鲜样本；样本中的开播时间为 19:49:54，早于本次进程启动，当前没有新场次公告事件，不能以没发开播卡片判定公告失败。日历卡片、动态渲染、群报告、Python 执行/取消/产物登记和浏览器操作均缺本轮正常使用证据。GSUID Core 继续未配置。

## 2026-09-12 20:45 上下文容量调整

运营者要求将各处偏紧的上下文容量放大至约 50,000 token。操作时面板出现 `Failed to fetch`；只读核对原 PID 1903/1906、11307 监听端口及 `len_bot.db` 句柄，均已关闭。因此在停机状态备份并修改实际根配置，未通过运行中的文件编辑覆盖面板状态。

| 实际配置字段 | 原值 | 已保存值 |
|---|---:|---:|
| `runtime.conversation_context_tokens` | 24,000 | 50,000 |
| `runtime.maintenance_context_tokens` | 24,000 | 50,000 |
| `runtime.job_context_tokens` | 64,000 | 64,000 |
| `plugins.bilibili_live_sensor.config.announcement_context_tokens` | 24,000 | 50,000 |
| `plugins.local_clock.config.context_tokens` | 8,000 | 50,000 |

输出预留仍为会话/历史维护/直播公告各 4096、工作 16384、本地时钟简报 1000；最近原话容量及历史分批参数保留原值。历史维护的可用输入容量将由 19,904 增至 45,904，增加工具结果进入后续请求的空间。本次只扩大实际配置，不修改请求算法或自动重试策略。

原根配置普通备份保存在 `.backups/context-budgets-20260912-204505/lenbot.config.json`。配置已重新读取核对；未运行测试、模型或 QQ 发送，未重启服务、重试失败区间。新值需下次正常启动后加载，尚无新容量下的历史维护成功证据；日历来源连接失败也未因此修复。

## 2026-09-12 能力审查复核与定向修复

复核 `LenBot_60ca881_capability_review.md` 后确认 F01、F02、F03、F05、F06 属于当前代码可直接触发的缺口，已完成最小范围修复：

- 取消分支绑定 `CancelledError` 实例，保留终止信息并避免追踪写入时二次 `UnboundLocalError`。
- 工作区最终文件打开加入 `O_NONBLOCK`，随后仍以同一文件描述符执行普通文件检查；FIFO 不会阻塞事件循环。
- 终止流程改为清理后再做最终 inspect，返回 `container_name`，并在状态无法确认时写入工作级阻断标记；同一工作后续执行、读取、导出和面板下载均停止复用，直到运营者核对。
- 工作区路径校验统一支持 Unicode 和空格文件名，仍拒绝绝对路径、空组件、`.`/`..`、NUL 与反斜杠；清单、读取和导出共用该边界。
- 浏览器快照增加 `text_offset`/`text_limit`，返回总长度、下一偏移和截断标记，可通过既有 `browser_snapshot` 续读长正文；Chromium 启动显式开启 sandbox。
- 浏览器截图结果把“媒体已登记”和“像素已装配进后续模型请求”分开标记；登记完成不再提前声明 `pixels_loaded`。

F04 所述宿主浏览器网络出口与资源隔离不是一个只改一处即可完成的边界，当前仅启用 Chromium sandbox，未将任意公网域名配置宣称为安全验收通过。浏览器正式开放仍需单独的执行环境、出口和资源验收。未运行测试、容器、浏览器、模型或 QQ；仅执行源码静态核对与正常编译前的差异检查。

## 2026-09-12 群聊社会 Agent 改进计划

用户确认进入下一阶段方案收敛，详细计划写入 [`docs/social-agent-improvement-plan.md`](social-agent-improvement-plan.md)。本轮只记录已确认的产品与部署决策，未实施新的运行代码、配置迁移或 VPS 部署；后续按该文件的 M0—M9 分阶段交付和人工验收。当前计划特别区分代码/配置就绪与 Linux VPS、OneBot、GSUID、B 站登录态、worker 和真实回执的运行证据。该文件的阶段编号 `M0—M9` 与 [`社会 Agent 完整实施计划`](LenBot_社会Agent_完整实施计划_7a4152d.md) 的模块编号 `M01—M20`、提交编号 `C00—C29` 是三套不同索引，执行时按完整实施计划为准。
