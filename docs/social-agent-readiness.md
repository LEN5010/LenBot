# 社会 Agent 前期准备与阶段落点

> 制定日期：2026-09-13。计划基线 `LEN5010/LenBot@7a4152d`，本文核对基线为本仓库 `76ef637`（分支 `social-agent-m0-foundation`）。
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
| 调用账与用量记录 | 部分具备 | `model_calls` 表 `cognition/call_store.py:37-42`（含 `purpose`/`usage_json`/`estimate_json`），估算方法 `call_store.py:11-32`，聚合 `:90-119` | C07 |
| 预算维度 | 部分具备（仅次数与已用时长） | `AgentBudget` 为次数维度 `cognition/budget.py:9-46`；工作三维 `runtime/job_runner.py:354-356`，落地在 `runtime/job_store.py:69-70`、`job_checkpoint():376-395` | C07、C08 |
| 额度原子预占与结算 | 不存在 | 无 `usage_reservations`、无 daily/quota/settle 逻辑 | C07 |
| 绝对 deadline / 累计 token 上限 | 部分具备 | 现有 `job_max_seconds` 是单次执行超时（`execution/workspace.py:209`、`job_runner.py` 预算快照），不是“首次开始后的绝对期限”，且恢复会重新计时 | C08、C09 |
| 三类身份（Human/System/Plugin） | 部分具备 | 工作创建强制人类来源 `cognition/jobs.py:19-20、29-33`，二次校验 `runtime/job_store.py:151-153`；Gate 逐条要求人类请求来源 `runtime/gate.py:156-161`。`system:`/`plugin:` 前缀已用于事件 `actor_id`，但没有显式起始者类型，也没有 system/plugin 发起工作的路径 | C06 |
| CapabilityGrant / 能力集合 | 不存在 | `AccessSettings` 只有 `qq_reply_whitelist`（`config_store.py:50-52`）；无 grant/capability 字段 | C06 |
| 插件声明 `required_capabilities` | 不存在 | `PluginSpec`（`plugins/catalog.py:21-38`）无该字段；现有分类是 `sensory/tool/scheduled/hybrid`（`plugins/models.py:131-135`） | C06、C19 |
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

对应计划第 8.2 节。这一批不依赖 Gateway、网络出口、账号或 Core。

| 提交 | 范围 | 本轮落点 |
|---|---|---|
| C01 `fix(execution): preserve cancellation and termination outcomes` | 取消传播、总期限/清理期限、终止记录 | 疑点 1；`plugins/host.py`、`execution/workspace.py`、`runtime/job_runner.py` |
| C02 `fix(memory): page maintenance reads within request budget` | 维护读取容量与续读 | `memory/reflector.py`、`memory/history.py`、`tools/retrieval.py` |
| C03 `fix(calendar): deliver explicit source failure cards` | 日历来源失败状态卡 | 疑点 2；`plugins/builtin/asoul_calendar/` |
| C04 `feat(chat): make participation topic- and addressee-aware` | 话题与对象感知的参与 | 依赖 C01—C03（计划在此处给出的是完整依赖，实施时按实际范围说明） |
| C05 `feat(context): expose delegable capabilities and focused references` | 可委托能力摘要与聚焦引用 | `cognition/context.py`、`tools/discovery.py`、记忆呈现 |

## 6. 当前不可宣称的能力

以下内容在计划对应阶段完成并取得人工运行证据前，不得写入产品文档的“已具备”，也不得在面板显示为可用：公共兴趣与跨群兴趣分享、心跳与睡眠、独立 Worker Gateway 与执行出网、独立浏览器与持久 profile/登录态、B 站账号读写动作、文件上传与 50MB/10 次额度、视频片段与音频转写、`proactive_chat`/`interest_share`/`send_file` 独立授权、GSUID Core 支持矩阵。

已交付状态仍以 [`当前任务`](iteration.md) 与[产品文档](product.md)的现状章节为准。
