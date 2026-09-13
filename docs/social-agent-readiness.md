# 社会 Agent 前期准备与阶段落点

> 制定日期：2026-09-13。计划基线 `LEN5010/LenBot@7a4152d`，第 2 节表格原始核对基线为本仓库 `76ef637`（分支 `social-agent-m0-foundation`）；C06 落地后该表中身份与能力授予两行已更新为当前落点。
> 本文是实施前的准备材料，不是交付报告，也不是能力清单。计划条款见 [`LenBot 社会 Agent 完整实施计划`](LenBot_社会Agent_完整实施计划_7a4152d.md)。

## 1. 本次范围

只做阅读、文档归档和静态事实核对：仓库 `git diff --check`、`uv run --no-dev python -m compileall -q src/len_bot`（退出码 0）以及按计划条款逐项定位当前代码落点。未修改运行代码、根配置、数据库、模型绑定、人格、Shadow 或生产群名单；未启动服务、容器、浏览器、Core、模型或 OneBot，未进行真实发送。

计划第 6 章给出的“建议新增文件”目前均不存在，本清单如实标注，不因为文档归档而改变仓库现状。计划第 8 章的提交编号 `C00—C29` 与第 6 章的模块编号 `M01—M20` 是两套索引；[`social-agent-improvement-plan.md`](social-agent-improvement-plan.md) 里的 `M0—M9` 是更早的粗粒度阶段，本文件不沿用该编号。

归档工作已提交为 `docs: archive social agent implementation plan`、`docs: mark prior phase contracts as archived`、`docs: scope the optional-capability gate to this phase`（`product`、`architecture`、`operations` 中残留的“下一阶段…”标题已改为已完成归档说明，避免两处并列的“下一阶段”相互冲突）。

## 2. 计划条款的当前落点

“落点”指当前实现该事实的确切位置。计划建议的新增文件若标为“不存在”，表示本次核对未在仓库中找到。

| 计划条款 | 当前实现 | 落点 | 计划阶段 |
|---|---|---|---|
| 唯一社会入口、保留既有链 | 已具备 | `cognition/social_core.py`、`runtime/gate.py`、`runtime/attention.py`、`scenes/actor.py` | 保留 |
| 三项模型绑定（conversation/work/maintenance） | 已具备 | `cognition/providers.py:68-73`；`purpose` 白名单 `cognition/gateway.py:42`；work 绑定冻结在 `agent_jobs.model_binding_json`（`runtime/job_store.py:72`） | 保留 |
| 调用账与用量记录 | 已具备（C07 起含用量/估算区分） | `model_calls` 表 `cognition/call_store.py`（含 `purpose`/`usage_json`/`estimate_json`）；一次调用的真实用量与本地估算由 `measured_call_tokens()` 分开算出，缓存与推理 token 不重复相加；C08 起同一 `job_id` 的汇总（`job_measured_tokens`）也是执行期 token 停止的输入 | C07 |
| 预算维度 | 已具备（C08 起含次数、期限与累计 token 三维） | `AgentBudget`（`cognition/budget.py`）同时持有次数、绝对 `deadline` 与从持久记录读到的 token 上限；`count_remaining`/`tightest` 是所有循环判定的唯一算法；工作三维快照 `runtime/job_runner.py:budget_state` | C08 |
| 额度原子预占与结算 | 已具备（C07 起，C08 起成为执行期硬上限） | 策略 `cognition/budget.py:ReservationPolicy`；创建期预占在 `commit_proposal_transaction` 的同一写事务内（`runtime/job_store.py:reserve_job_budget_in_transaction`）；结算与释放 `cognition/call_store.py:settle/release/close_reservation_in_transaction`；页面 `/api/models/reservations` 与模型页“工作额度预占” | C07 |
| 日额度策略的编辑与生效 | 已具备（C07 起，默认不新增限制） | 根配置 `resources.policies`（`config_store.py:ResourceSettings`）；`CapabilityGrant.resource_policy` 按名称解析 `runtime/capabilities.py:policy_for_grant`；页面 `/api/settings/resources` 与系统设置页“额度策略” | C07 |
| 绝对 deadline / 累计 token 上限 | 已具备（C08 起） | 工作时间与 token 维度由 `runtime/job_runner.py:budget_state` 从 `agent_jobs` 与 `usage_reservations` 读出，`AgentBudget._refusal()` 在启动下一次调用前拒绝；工作期限是首次开始后的绝对量，恢复读持久累计时长，不重新计时；对话轮次可用 `runtime.conversation_window_seconds` 配置自己的绝对期限（默认 `null`，即不设该维度），`ConversationResume.elapsed_seconds_limit` 使等待恢复沿用同一窗口 | C08 |
| 三类身份（Human/System/Plugin） | 已具备（C06 起） | 类型定义 `events/models.py` 的 `HumanInitiator`/`SystemInitiator`/`PluginInitiator`；`JobProposal.initiator`（`cognition/jobs.py`）；事务内按类型分支校验 `runtime/job_store.py:_validate_job_initiator_in_transaction`；Gate 非人类分支走独立能力检查 `runtime/gate.py:_capability_refusal` | C06 |
| CapabilityGrant / 能力集合 | 已具备（C06 起，默认空） | `runtime/capabilities.py` 的能力词汇与 `CapabilityAuthority`；根配置 `access.capability_grants`（`config_store.py`）；页面 `/api/settings/access` 与系统设置页 | C06 |
| 插件声明 `required_capabilities` | 不存在（C06 有意不加） | `PluginSpec`（`plugins/catalog.py:21-38`）无该字段；现有分类是 `sensory/tool/scheduled/hybrid`（`plugins/models.py:131-135`）。C06 只有能力词汇与授予结构；逐工具的能力要求随真正需要它的工具（文件上传、账号动作等）各自提交 |
| Gate 发送前检查 | 已具备（无额度/睡眠维度） | `runtime/gate.py:120-313`；发送前 `actions/queue.py:114-115` → `agent_runtime.py:106` → `validate_outbound_action():525-548`；插件来源 `plugin_interactions.py:115-131` | 保留 |
| 离线 Python worker | 已具备 | `--network none` 硬编码 `execution/workspace.py:192`，容器参数 `:192-198`（只读根、cap drop、非 root、pids/内存/CPU/tmpfs 限额），镜像 `containers/workspace/Dockerfile` | 保留现状 |
| 独立 Worker Gateway、`execution_runs` | 不存在 | 无 `execution/protocol.py`、`execution/client.py`、`services/worker_gateway/`；当前在 LenBot 进程内直接调用 `docker` CLI（`execution/workspace.py:202`） | C10、C11 |
| 执行终止与未确认阻断 | 已具备 | `_terminate()` `execution/workspace.py:280-328`（confirmed_absent/confirmed_stopped/unconfirmed，未确认时写 `.termination_unconfirmed` 并拒绝复用 `:110-117`） | 保留 |
| 浏览器 | 部分具备（同进程、无独立隔离） | 同进程 Playwright `browser/worker_v2.py:90-96`；无持久 profile、无 `user_data_dir`/`storage_state` | C14 |
| 公开研究与 B 站读取原语 | 部分具备 | 现有 `web_search`、`bilibili_content`、`link_parser`、`asoul_dynamics` 插件与 `tools/retrieval.py`；无统一 research facade、无评论/字幕专用读取 | C16 |
| 公共兴趣 `AgentInterestMemory`/`InterestItem` | 不存在 | 认识账本只有 `MemoryKind`（address/preference/relationship/fact/group_norm）`memory/models.py:11-16`，无 interests 模块或类型 | C17 |
| 心跳 | 不存在 | 无 `heartbeat` 字段、无 `runtime/heartbeat.py`、无 `HeartbeatCycle` | C18 |
| 睡眠与叫醒 | 不存在 | 无 `SleepState`、无 `runtime/sleep_policy.py`；`time.afternoon_start/end` 不是睡眠窗口（`config_store.py:71-75` 仅本地校验引用） | C19 |
| 持久延迟交付 | 不存在 | 调度 task kind 目前只有 `agent_job`（`runtime/job_store.py:203`）与 `reminder`（`cognition/proposals.py:362`）；发送队列是内存 `asyncio.Queue`（`actions/queue.py:22`），`validate_before_send` 无 defer 语义 | C20 |
| 主动发布与统一卡片 | 部分具备 | 卡片 `cards/` 与各业务插件已具备；`proactive_chat`/`interest_share` 独立授权不存在 | C21 |
| 视频片段与媒体分析 | 不存在 | 无 `containers/media/`、无 ffmpeg 依赖、无转写绑定 | C22 |
| 文件资产与 OneBot 上传 | 不存在 | `media_assets` 无有效期列（`media/store.py:36-41`）；`MessageSegment` 仅 text/image/video/audio/at（`media/models.py:14`）；`ActionType` 仅 `SEND_GROUP_MESSAGE`/`SEND_PRIVATE_MESSAGE`（`actions/models.py:22-24`）；`adapters/onebot.py:235-260` 无 file 段与 `upload_group_file` | C23、C24 |
| B 站登录态 | 部分具备（位置与计划不同） | 凭据当前在插件配置 `plugins/builtin/bilibili_content/config.py:4-9`（`sessdata`/`bili_jct`），消费处 `plugin.py:63-65、:125`；序列化已做遮蔽（`web/query_service.py:1010`）。计划要求收回根配置专用字段并隔离账号读取 | C25、C26 |
| 点赞/收藏 | 不存在 | 无窄动作提案、无账号写路径 | C26 |
| 可选 GSUID Core | 已具备（未配置） | `plugins/builtin/gscore_adapter/`，现状见 [`gscore-adapter.md`](gscore-adapter.md)；实际根配置未启用 | C27 |
| 面板 | 部分具备 | 现有 Jobs/Scenes/Models/Plugins/Memory/TasksLoops 视图（`web/frontend/src/views/`）与 `web/query_service.py`；缺 Interest、Artifacts 与跨模块来源收口 | C28 |
| 工程约束与验收方式 | 已具备 | [`AGENTS.md`](../AGENTS.md)。仓库虽存在 `tests/`，但约束禁止新增、修改或运行测试；本计划验收只走静态编译、`git diff --check` 与获准环境中的人工业务核对 | 全程 |

## 3. 实施前建议复核的存量疑点

以下只来自源码阅读，未运行、未复验，不构成“已确认缺陷”；实施相应提交前先按真实路径核对是否有更晚修复。

1. **工作区取消可能被降级成普通结果。** `execution/workspace.py:227-231` 抛出的是 `WorkspaceCancelled(asyncio.CancelledError)`（`execution/workspace.py:66-69`），`runtime/job_runner.py:825-841` 会记录 `workspace_termination` 并重抛；但插件工具入口 `plugins/host.py:765-769` 把它转成 `ToolResult.failure(..., 'workspace_cancelled')`。计划 M01 要求“不要在宿主捕获所有 `WorkspaceCancelled` 后一律把它变成普通 ToolResult，让被取消 Agent 继续循环”。需确认 `host.py:742-745` 的 `asyncio.wait_for` 超时外层是否会把该取消吞成普通失败结果，再决定 C01 的改动范围。
2. **日历来源失败没有独立状态卡。** 来源异常直接 `raise`（`plugins/builtin/asoul_calendar/calendar.py:247-254`），精确命令入口 `plugin.py:86-90` 对非 `ok/no_results` 状态 `raise ValueError`。计划 M01/C03 要求区分“成功 / 查询成功但无日程 / 来源失败”并向群交付明确状态卡，而不是复用正常卡片或静默无结果。

## 4. 待确认信息（计划第 13 章）

| 缺口 | 影响阶段 | 本地当前已知 |
|---|---|---|
| 目标 Linux VPS 架构、CPU/RAM/磁盘、用户映射、容器运行方式 | C11—C14、C29 正式放行 | 本机为 darwin/arm64；实际根配置使用 `/opt/homebrew/bin/docker`、镜像 `lenbot-workspace:py313`、`container_user 501:20` |
| 实际 OneBot 实现/版本、文件上传方式与返回结构 | C24 | 适配器实现 `send_group_msg`/`send_private_msg`（`adapters/onebot.py:236`），无文件上传分支 |
| B 站专用账号、凭据有效性、允许的收藏夹、必要资源域 | C25—C26 | 插件配置已有 `sessdata`/`bili_jct` 字段位置，实际值不在本次核对范围 |
| 可用音频转写能力及计量方式 | C22 音频分支 | 无转写绑定，`models.routing` 只有 conversation/work/maintenance 与 retrieval |
| Core 地址、版本、首批游戏命令与消息类型 | C27 | 实际根配置未启用 `gscore_adapter` |
| D01—D12 推荐裁决是否采纳 | 仅对应业务 | 计划第 2 章已给出推荐值并在文档中标注为“推荐裁决”，尚无用户逐项确认 |

## 5. 首批提交边界（C01—C05）

对应计划第 8.2 节。这一批不依赖 Gateway、网络出口、账号或 Core。C06 属第二批，见第 6 节。

| 提交 | 范围 | 本轮落点 |
|---|---|---|
| C01 `fix(execution): preserve cancellation and termination outcomes` | 取消传播、总期限/清理期限、终止记录 | 疑点 1；`plugins/host.py`、`execution/workspace.py`、`runtime/job_runner.py` |
| C02 `fix(memory): page maintenance reads within request budget` | 维护读取容量与续读 | `memory/reflector.py`、`memory/history.py`、`tools/retrieval.py` |
| C03 `fix(calendar): deliver explicit source failure cards` | 日历来源失败状态卡 | 疑点 2；`plugins/builtin/asoul_calendar/` |
| C04 `feat(chat): make participation topic- and addressee-aware` | 话题与对象感知的参与 | 依赖 C01—C03（计划在此处给出的是完整依赖，实施时按实际范围说明） |
| C05 `feat(context): expose delegable capabilities and focused references` | 可委托能力摘要与聚焦引用 | `cognition/context.py`、`tools/discovery.py`、记忆呈现 |

## 6. 第二批提交边界（C06）
对应计划第 8.2 节的 C06 `feat(auth): add typed initiators and capability grants`：事件/工作/提案/调用模型、根配置、ScenePolicy/Gate、权限页面；完成条件是 human/system/plugin 分支明确、grant 不能由模型伪造、未配置新能力不扩权。

| 落点 | 本轮改动 |
|---|---|
| 事件与身份类型 | `events/models.py` 新增 `HumanInitiator`/`SystemInitiator`/`PluginInitiator` 与 `Initiator` 判别联合，以及只从真实事件取人的 `human_event_uid`/`human_initiator_for` |
| 工作提案 | `cognition/jobs.py` 增加 `initiator`；创建时必须有且只有一个明确分支，控制操作不替换原发起者 |
| 提案暂存 | `cognition/proposals.py` 的人类来源校验改用同一 `human_event_uid`，并在 `start_work` 与插件 `stage_work` 填typed人类发起者 |
| 插件调用模型 | `plugins/models.py` 的 `PluginCallContext` 带上 `initiator`；`plugins/host.py` 按真实事件类型决定 human/plugin，缺来源时保持 None 而不是读成系统 |
| 工作存储 | `runtime/job_store.py` 按类型分支二次校验（human/plugin/system 各自对应真实事件前缀），并把发起者写入工作 payload；旧记录只按自身确切字段转换 |
| 能力检查 | 新增窄 `runtime/capabilities.py`：能力词汇、`CapabilityGrant`、检查顺序前 3 步与唯一的 `check()`；后续步骤仍留在原有位置 |
| 根配置与页面 | `config_store.py` 的 `access.capability_grants`（默认空）、`/api/settings/access` 与系统设置页“能力授予” |
| Gate | `runtime/gate.py` 对非人类发起的工作创建走独立能力分支，缺授予即拒绝，不借用人类请求路径 |

## 7. 第二批提交边界（C07）

对应计划第 8.2 节的 C07 `feat(budget): reserve and settle shared usage atomically`：`AgentBudget`、CallStore/JobStore、`usage_reservations`、模型页面；完成条件是 user+scene 并发预占一致、usage 与估算可区分、子调用归同一账。

| 落点 | 本轮改动 |
|---|---|
| 额度策略 | `cognition/budget.py` 新增 `ReservationPolicy`：单工作上限、账号日上限、场景日上限与账务日边界；缺省为 10M/工作、30M/账号/日、场景维度未配置 |
| 调用计量 | `cognition/call_store.py:measured_call_tokens()` 把一次调用拆成真实 usage 与本地估算两列；缓存 token 与推理 token 不重复相加；供应商无 usage 时不是 0，而是本地估算加配置的输出上限 |
| 预占与结算 | `usage_reservations` 表；`reserve_work_in_transaction`（含账号日额度与场景日额度拒绝）、`settle_reservation_in_transaction`（按 `job_id` 汇总 `model_calls`）、`release_reservation_in_transaction`、`close_reservation_in_transaction` |
| 创建期预占 | `runtime/job_store.py:reserve_job_budget_in_transaction` 在 `apply_job_proposals_in_transaction` 内、`commit_proposal_transaction` 已有写事务里执行，工作行与预占同生共死；并发创建不会读到同一份空闲额度 |
| 结束期结算 | `complete_job`、`interrupt_job` 与取消操作在同一事务内把预占换成真实消费；未发生模型调用的工作整份释放 |
| 策略解析 | `runtime/capabilities.py:policy_for_grant` 按名称解析 `CapabilityGrant.resource_policy`；未命名或名称失效时回到默认策略，不推测、不复制数值 |
| 运行期接线 | `runtime/agent_runtime.py:_apply_budget_configuration` 把 `RuntimeConfig` 的执行预算、`CapabilityAuthority` 与既有业务时区交给存储；`update_root_settings('resources')` 后立即重取 |
| 根配置与页面 | `config_store.py` 的 `resources.policies`、`/api/settings/resources` 与系统设置页“额度策略”；`/api/models/reservations` 与模型页“工作额度预占” |

## 8. 第二批提交边界（C08）

对应计划第 8.2 节的 C08 `feat(agent): enforce resource budgets across native loops`：`AgentLoop`、social/work/maintenance 与插件专用入口；完成条件是 None 次数模式无漏算/类型错误、deadline 与 token 真实停止、预留终结能力。

| 落点 | 本轮改动 |
|---|---|
| `None` 的语义 | 新增 `count_remaining(limit, used)` 与 `tightest(*bounds)`（`cognition/budget.py`）：`None` 表示该维度不参与停止判定，既不当作 0 也不参与减法 |
| 循环判定 | `AgentLoop` 每轮的余量 = 本调用参数、账本计数、调用方报告余量三者取最紧；`step_index == max_steps - 1` 这类比较被删除；工具批量检查同样走 `tightest` |
| 对话 | `conversation_max_steps`/`conversation_max_tool_calls` 允许 `None`；新增可选 `conversation_window_seconds`；`ConversationResume.elapsed_seconds_limit` 让等待恢复沿用同一窗口 |
| 工作 | `budget_state()` 读 `agent_jobs` 的累计次数与时长、`usage_reservations` 的预占额与同一 `job_id` 的全部 `model_calls` 汇总；`remaining_seconds()` 用持久累计时长算剩余，恢复不重置 |
| 终结预留 | `terminal_seconds_reserve`（30 秒与上限四分之一取小）与 `terminal_token_reserve`（一次完整请求的容量，工作取 `job_context_tokens + work_output_tokens`） |
| 配置 | `budgets_fit` 拒绝“次数与期限同时为 `null`”的无停止条件组合；`maintenance_max_steps` 保持必填，因为该循环没有期限维度 |
| 运行期接线 | `runtime/job_runner.py`、`runtime/plugin_interactions.py`、`memory/reflector.py`、`runtime/work_context.py`、`plugins/builtin/group_summary/analysis.py`、`runtime/job_store.py`、`runtime/agent_runtime.py` |
| 页面 | 运行参数页“执行预算”表增加“每轮对话绝对期限”并按 `null` 显示“不设限（由其他维度停止）”；Jobs 页用量行同样 |

## 9. 当前不可宣称的能力

以下内容在计划对应阶段完成并取得人工运行证据前，不得写入产品文档的“已具备”，也不得在面板显示为可用：公共兴趣与跨群兴趣分享、心跳与睡眠、独立 Worker Gateway 与执行出网、独立浏览器与持久 profile/登录态、B 站账号读写动作、文件上传与 50MB/10 次额度、视频片段与音频转写、`proactive_chat`/`interest_share`/`send_file` 独立授权、GSUID Core 支持矩阵。C06 只建立能力词汇、授予结构与检查顺序；上述能力本身仍未实现，授予结构里出现某个能力名不代表该能力可用。C07 做额度预占与结算，C08 让工作的时间与 token 维度在执行期真正停止（含为终结本身预留输出与时间）。**C08 没有把对话轮次的 token 维度做成配置项**：对话仍按次数与可选期限停止，凡“超过累计 token 会自动停止”的说法只对工作成立。D05 的 1800 秒与 D06 的具体数值仍未获逐项确认。

已交付状态仍以 [`当前任务`](iteration.md) 与[产品文档](product.md)的现状章节为准。
