# 社会 Agent 实施设计过程

> 计划合同：[`LenBot 社会 Agent 完整实施计划`](LenBot_社会Agent_完整实施计划_7a4152d.md)
> 前期落点：[`社会 Agent 前期准备`](social-agent-readiness.md)
> 工作分支：`social-agent-m0-foundation`
> 约束：[`AGENTS.md`](../AGENTS.md)。每个阶段一次提交，提交后在此追加一节。

本文记录每个阶段的设计判断、实际改动、静态核对结果和尚未取得运行证据的部分。措辞遵循计划第 10.3 节：统一使用“实现完成”“部署就绪”“实际链路通过”三种状态，不用“编译通过”代替功能完成，也不用“没有报错”代表全面通过。

## 状态总览

| 提交 | 计划依赖 | 代码状态 | 运行验证 |
|---|---|---|---|
| C01 `fix(execution): preserve cancellation and termination outcomes` | C00 | 实现完成 | 未运行 |
| C02 `fix(memory): page maintenance reads within request budget` | C00 | 实现完成 | 未运行 |
| C03 `fix(calendar): deliver explicit source failure cards` | C00 | 实现完成 | 未运行 |
| C04 `feat(chat): make participation topic- and addressee-aware` | C01—C03 | 实现完成 | 未运行 |
| C05 `feat(context): expose delegable capabilities and focused references` | C04 | 实现完成 | 未运行 |
| C06 `feat(auth): add typed initiators and capability grants` | C00 | 实现完成 | 未运行 |
| C07 `feat(budget): reserve and settle shared usage atomically` | C06 | 实现完成 | 未运行 |
| C08 `feat(agent): enforce resource budgets across native loops` | C07 | 实现完成 | 未运行 |
| C09 `feat(jobs): preserve revisions and budget ownership on resume` | C08 | 实现完成 | 未运行 |
| C10 `feat(execution): define owned worker protocol and journal` | C09 | 实现完成 | 未运行（无 Docker 环境，未启动容器） |

C01、C02、C03 都只依赖 C00 且互不影响；本文件按完成顺序记录，编号只标识计划第 8.2 节的范围。

实际提交顺序是 C01、C03、C02、C05、C04，与计划编号不同：

- C02 与 C03 互不影响，先落地 C03 是因为它牵连同一批精确命令路径，读码集中一次完成。
- C05 先于 C04 落地：计划把 C05 的依赖写成 C04，指的是同一批文件（`context.py`、`social_core.py`）上的后续改动，而不是 C05 的验收项需要 C04 的参与判定。C05 只增加“可委托”这一层事实与措辞，C04 的输入表示与措辞改动都发生在它之上，两处不冲突。
- C04 依赖 C01—C03 中的 C03 最紧（同一批插件与对话路径），在 C05 之后落地时只改了 `context.py` 的输入表示与系统提示，没有回改 C05 引入的 `delegable_purposes` 措辞。

## C00 契约与文档收口

已在前置工作中完成，见 [`social-agent-readiness.md`](social-agent-readiness.md) 与提交 `76ef637`、`01241ec`、`f328e64`、`4014f3d`、`61969fa`。未修改实际根配置。

## C01 取消与终止结果保留

### 设计判断

计划 M01 的三条要求落到当前代码上分别是：

1. **“不要在宿主捕获所有 `WorkspaceCancelled` 后一律把它变成普通 ToolResult，让被取消 Agent 继续循环。”** 当前 `plugins/host.py` 捕获 `WorkspaceCancelled` 后返回 `ToolResult.failure(..., 'workspace_cancelled')`，被取消的工作循环因此拿到一条失败结果并继续下一步。相同处理模式也存在于核心工具入口 `tools/retrieval.py` 的 `except Exception`。
2. **“外层总期限应涵盖输入准备、执行和明确的清理窗口，不与内层执行期限设成同一个数再相互争抢。”** 当前 workspace 插件的 `call_timeout` 返回的正是 `config.worker.timeout_seconds`，外层与内层同一数值：内层超时后还要执行 `docker kill`、`killpg`、`docker rm -f`、`docker inspect` 四个清理步骤，却已经没有剩余时间。
3. **“终止状态要在不会被 `wait_for` 异常转换丢失的位置记录，所有清理子进程本身也必须被回收。”** 当前 `_terminate` 对自身创建的子进程只 `wait_for(...wait(), timeout=5)`，未在超时后杀掉该客户端进程；四个清理步骤各有独立 5 秒上限，合计最长 20 秒，没有总预算。

改动方向按最小范围：让取消保持取消语义、让外层期限严格大于内层并覆盖清理、让清理自身有确定上限并回收进程。不引入新的执行层、状态表或第二个终止机制。`asyncio.wait_for` 的超时分支只在自身期限内触发才会把 `CancelledError` 改写成 `TimeoutError`，因此不需要额外包一层 `shield` 去对抗它。

### 实际改动

- `src/len_bot/plugins/host.py`
  - `except WorkspaceCancelled` 分支不再返回 `ToolResult`：等待该工具任务真正结束后（`suppress(asyncio.CancelledError)`）用裸 `raise` 继续向上抛出原始取消与 termination，使 Agent 循环停止而不是把它当作普通工具失败。
  - 插件工具的 `except Exception` 分支沿用既有语义；到达该分支的异常按原逻辑记录并返回失败结果。
- `src/len_bot/tools/retrieval.py`
  - `execute_observation` 中插件只读工具的调用改为先捕获取消、按 `workspace_cancelled` 记录一条真实观察（含 termination 终止身份），再把取消继续抛出。这样“已发生的外部取消”有据可查，同时不再被 `except Exception` 降级为工具结果。
- `src/len_bot/execution/workspace.py`
  - 新增 `CLEANUP_TIMEOUT_SECONDS = 5.0` 与 `_run_cleanup(*argv)`：清理子进程有统一上限，超时后杀死该客户端进程并回收，避免清理步骤自身泄漏；`_cleanup_output` 同时读取 stdout/stderr，供 inspect 判断状态。
  - `_terminate` 改为使用 `_run_cleanup`，并在进入时记录单调起始时间；每步检查剩余清理预算，预算耗尽即返回 `unconfirmed` 说明，而不是无限做下一步。
  - 新增 `TERMINATION_PARKS` 与 `park_termination` / `parked_termination`：终止结果在被记录时同步写入停车区，保证即使取消在返回给 Agent 的过程中被再次打断，终止身份仍可被工作循环读取。
- `src/len_bot/plugins/builtin/workspace/__init__.py`
  - 插件 `call_timeout` 改为 `worker.timeout_seconds + WORKSPACE_CALL_MARGIN_SECONDS`（后者 30 秒），使外层工具期限严格大于内层执行期限，并覆盖输入导出、容器清理和结果整理。
- `src/len_bot/runtime/job_runner.py`
  - 新增 `_cancellation_termination`：优先读异常自带的 termination，缺失且该工作属于 workspace 插件时回退读取 `parked_termination(workspace_id)`，使终止状态不因异常转换丢失。

### 未做的事

- 没有改动 `AgentLoop`、`Gate`、`ActionQueue` 的既有异常语义；`asyncio.CancelledError` 在这些位置的传播路径保持原样。
- 没有为清理新增持久表或第二个终止记录系统，仅用现有 control 目录标记、`_record_termination` 与模块内停车区。
- 没有把 `--network none`、容器用户、只读根等执行边界改成可配置。

### 静态核对

- `git diff --check`
- `uv run --no-dev python -m compileall -q src/len_bot`
- 未运行测试、容器、浏览器、模型、OneBot 或真实发送。

### 未确认项

- 取消传播只在源码路径上核对；“外部取消不再继续下一步模型调用”需要一次真实的长工作取消才能确认。
- 外层 30 秒余量是本地可读的常量，部署后内层 `timeout_seconds` 若被调大，外层仍随之增大，不需要额外配置；但实际清理耗时只能在目标机器上核对。
- 终止停车区是模块内状态，只用于同一进程内读取；跨进程恢复仍以 control 目录的 `.termination_unconfirmed` 标记和既有工作记录为准。

## C03 日历来源失败状态卡

### 设计判断

计划 M01 对日历的要求是三种业务结果分开，且都不新增模型调用：

| 结果 | 应有表现 |
|---|---|
| 成功取到源日程（含查询成功但本日为 0 条） | 现有日程卡片 |
| 来源取得失败 | 明确“日程暂未取得”状态卡，不表示今天没有直播 |
| 渲染本身失败 | 保留真实失败，不声称发出了错误卡 |

当前实现里，`CalendarService.snapshot()` 在来源异常时直接 `raise`，精确命令入口 `on_command` 对非 `ok/no_results` 状态 `raise ValueError`。结果是精确命令消费了这条消息却没有任何群内结果。

同时有一个必须先解决的技术前提：这类精确命令与卡片由插件 `handler` 直接调用 `call.submit_message(...)`，而 `submit_message` 路径（`plugin_interactions.py:186-189`）位于 Actor 写事务之外，没有任何捕获异常的边界。如果在这里让失败路径抛异常，会变成未处理的任务异常，而不是“明确失败”。因此本提交的状态卡必须在同一个 handler 调用内完成，不能依赖抛错后再由别处补发。

### 实际改动

- `src/len_bot/plugins/builtin/asoul_calendar/calendar.py`
  - 新增 `ScheduleSourceUnavailable`，携带 `source_url` 与 `attempted_at`；`snapshot()` 的来源异常改为抛出它，保留 `last_error_at`/`last_error` 的既有记录语义。
  - 新增 `source_failure()`，返回最近一次真实来源失败（且其后没有成功读取），因此不会把更早的失败当成当前事实。
- `src/len_bot/plugins/builtin/asoul_calendar/plugin.py`
  - `get_live_schedule` 工具不再让来源失败冒泡：返回 `ToolResult.failure(..., 'source_unavailable', sources=[...])`，措辞明确“这不表示今天没有直播，也不表示能力永久不可用”。
  - `on_command` 区分三种结果：`source_unavailable` → 渲染状态卡并提交；`ok`/`no_results` → 现有日程卡片；其他状态 → 保持原有明确失败。
  - 新增 `failure_lines()`：只给出对群可公开的措辞（哪个范围的日程、读取时间），不含源 URL 和内部判断细节，与其他来源失败的公开措辞保持一致。
- `src/len_bot/plugins/builtin/asoul_calendar/render.py`
  - 新增 `StatusCardRenderer`，沿用同一套 `cards` tokens、字体与版式；卡片明确写“日程暂未取得”。它不是降级渲染器：成功与空日程仍走原 `ScheduleRenderer`。

### 未做的事

- 没有增加备用源、没有在失败时改调模型、没有新增通用降级框架。
- `ToolResult.failure` 的构造会添加 `Error: ` 前缀（`tools/results.py:140`）；本提交沿用该既有约定，未为此改动公共结果模型。该前缀只出现在保存的观察文本里，群内交付的是状态卡片本身。
- 没有改动 `ScheduleResult` 的字段或既有卡片版式。

### 静态核对

- `git diff --check`
- `uv run --no-dev python -m compileall -q src/len_bot`
- 未运行测试、模型、OneBot 或真实日历源；来源失败卡片未在真实群里出现过。

### 未确认项

- “来源失败时不新增 LLM 调用”属于控制流事实（handler 内直接渲染），未在真实运行中测量调用账。
- 渲染失败仍走原有失败路径（提交不成立），需要在真实业务里确认不会留下“消息被消费但无结果”的情况。

## C02 维护读取容量

### 设计判断

计划 M01 对摘要逻辑的要求里，与本提交相关的两条是：

1. **“旧认识使用少量完整记录、明确分页和回读入口。”**
2. **“不能强行截断认识中的否定与条件，不把只返回条目目录计为已经读过其原始证据。”**

当前维护循环（`memory/reflector.py`）的唯一工具 `query_memory` 接受 `subject`/`query`/`include_history`，一次返回最多 `retrieval_default_limit`（实际根配置 15）条**完整** `MemoryItem`；`MemoryLookup` 没有 `limit`、没有 `kind`、没有续读入口。单页容量是固定的，模型既不能缩小一页、也不能在装不下时继续读下一页。

批次侧已经具备计划要求的另一半：`history_batches` 保留 `start_rowid/start_offset/end_rowid/end_offset` 与 `complete` 标记，`prepare_request` 在超容量时抛错且不推进覆盖游标。本提交不动这两处。

### 实际改动

- `src/len_bot/memory/reflector.py`
  - `MemoryLookup` 增加 `kind`、`offset`、`limit`（1—200）；`limit` 由模型给出，不再固定为配置默认值。
  - `query_memory` 多读一条用于判断是否还有下一页，返回结构化分页结果：`records`（含 `memory_id`、`scope`、`subject`、`kind`、`basis`、`statement`、`created_at`、`expires_at`、`status`）、`offset`、`returned`、`next_offset`。
  - 记录字段显式列出，不再把整个 `MemoryItem` 序列化后一次性塞进工具结果；同一条认识的证据列表不会再随每页重复出现。
  - 工具说明与维护系统提示都写明分页语义：用 `next_offset` 续读同一查询，不要用更大的 `limit` 重问或反复重试同一页；一页目录不等于读过对应原文。
- `src/len_bot/memory/store.py`
  - `query_memories` 增加 `offset`（非负整数校验）。无 `query` 路径改为 `LIMIT ? OFFSET ?`；有 `query` 的词面排序路径把保留堆扩到 `limit + offset` 后再切片，使分页在同一完整排序上进行，不因页边界丢记录，也不放弃“约束先于排序”。

### 未做的事

- 没有改 `query_memory` 之外的工具、没有改 `MemoryStore` 的表结构、没有改 `commit_memory_proposal` 写入路径。
- 没有修改批次投影、覆盖游标、`begin_history_batch` 的容量判断或 `history_batches` 记录。
- 没有把 `query_memories` 的 `limit` 校验放宽；`offset` 与 `limit` 的组合仍然只读取已经过滤后的账本。

### 静态核对

- `git diff --check`
- `uv run --no-dev python -m compileall -q src/len_bot`
- 未运行测试、模型或真实群；分页续读没有在真实维护批次中走过。

### 未确认项

- “后续请求可结束、失败不推进覆盖”依赖现有的 `prepare_request` 与批处理逻辑，只能通过一次真实的大批量维护观察确认。
- 现有数据库中的旧批次记录不受影响；本提交不新增列、不新增表，因此不需要离线结构转换。
- 词面排序分页会把 `limit + offset` 条候选留在堆内；这是分页正确性的必要代价，未做缓存或第二套索引。

## C05 可委托能力摘要

### 设计判断

计划 M03 的能力提示要求是：对话模型应看到简短的可委托能力说明，例如“可以建立工作运行 Python/浏览网页”，而不是只有“不属于 conversation 的工具”；同时这不是把 work 的全部 schema 复制给对话，也不是把任何打开链接的请求强制升级为长工作。

当前 `capability_facts()`（`plugins/host.py`）只列出**当前角色可直接调用**的工具所属模块：workspace 的四个工具 `roles=('work',)`，浏览器工具同样是 work-only，因此它们从不出现在对话的能力事实里。结果是对话看到的清单里根本没有 Python 和浏览能力，模型只能从“这不是 conversation 工具”推断出负面结论，这与计划要求相反。

第二个缺口是容量无关性与一致性的冲突：`facts_message` 只在 `facts` 非空时才返回消息，`capabilities` 本身已经进入事实；但现有措辞没有说明“缺失即不可用”，因此“模块被省略”与“模块不可用”在模型看来是同一件事。

本提交只补“可委托”这一层语义：把 work-only 模块的用途摘要以 `delegable_purposes` 形式列出，并明确它是可委托说明、不是已授予额度或权限；不新增配置开关，不改动任何授权判定。

### 实际改动

- `src/len_bot/plugins/host.py`
  - `capability_facts()` 对“模块已加载但当前角色没有可直接调用工具”的情况不再直接跳过：若该模块存在 `available is None` 的可发现工具，则输出 `delegable_purposes`（用途去重排序）与说明“本模块的能力属于长工作，不在当前对话直接调用；需要时用 `start_work` 交给工作执行”。不输出工具名、参数 schema 或入口。
  - 已加载且当前角色有工具可用的模块仍按原格式输出 `purposes`；无法区分的条目保持原样跳过。
- `src/len_bot/cognition/context.py`
  - 系统提示在能力段补充：带 `delegable_purposes` 的模块属于长工作，需要时用 `start_work`；它是可委托说明，不是已授予的额度或权限；能力说明里没有出现的模块就是当前不可用，不能凭名字推测已启用。
  - 能力清单本身改为由 `_delegable_hint` 决定是否进入本轮事实：装配位置不变，仍受既有容量检查与 `omit` 记录保护。
- `src/len_bot/cognition/social_core.py`
  - 装配上下文时按本轮场景与请求者设置 `_delegable_hint`。
- `src/len_bot/runtime/scene_policy.py`
  - 新增 `delegable_work_allowed(scene_id, requester_qq_uid)`，直接复用既有 `chat_allowed` 判定：不能回应的场景也就不再被额外告知这里能做什么。它只决定提示是否出现，不授予预算、工具或执行——这些仍走原有的工作、插件与 Gate 检查，没有新增第二个准入判定。

### 未做的事

- 没有新增 `SceneSettings` 字段、没有新增配置项，也没有改根配置样例；因此不需要配置迁移，也不需要前端改动。
- 没有把 work 工具 schema 暴露给对话，没有新增第二套工具发现渠道：`tool_search` 行为不变。
- 没有改变 `capabilities` 的可见性判定（仍由现有 scene/plugin/chat 检查决定），也没有让它出现在被省略的事实里。当 `facts_message` 整体无容量时，能力清单与其它事实一样被 `omit` 记录。

### 静态核对

- `git diff --check`
- `uv run --no-dev python -m compileall -q src/len_bot`
- 未运行测试、模型或真实群；未在真实对话中确认模型据此改变“能不能用 Python/浏览器”的表述。

### 未确认项

- 计划 M03 的“样例不挤掉原话”属于现有容量优先级机制（`voice_examples` 在容量不足时先被省略，原话在 `fit_request` 中保持），本提交未改动该顺序，需要一次真实长上下文观察确认。
- `delegable_purposes` 只描述用途文本，具体 capability 词汇表属于计划 C06/C07；在 C06 之前这里不出现 `network_python` 等能力 ID，避免提前声明尚不存在的授权。

## C04 话题与对象感知的参与

### 设计判断

计划 M02 要求对话能分清“这是一次观察机会”和“这是一项请求”，并按话题与真实对象决定参与、沉默或委托；同时明确不新增固定前置分类模型、不为本提交增加模型调用。逐条核对当前实现：

| 计划要求 | 当前实现 | 本提交是否改动 |
|---|---|---|
| 精确命令先完成归属，普通消息进入既有注意力策略 | `plugin_interactions.classify_event` 在注意力之前写 `plugin_consumed`/`interaction`，`AttentionPolicy.apply` 只在 `plugin_consumed` 之外产生唤醒 | 不改 |
| 强唤醒与关键词/随机机会分开，公开话题只扩展弱机会 | `attention.py:52-85`：`mention`/`reply_to_bot`/`private_message`/`address_name`/`continuing_interaction`/`in_flight_follow_up`/`work_participant`/`awaiting_response` 为 certain；`keyword_opportunity`/`sample_opportunity` 为非 certain | 不改判定，改为把判定结果交给模型 |
| 一个 Social Core 决定回应、沉默、澄清、委托，不增加固定规划模型 | `SocialCognitionCore.run` 单入口，respond 单终结；本轮不新增模型调用 | 不改 |
| 回复针对话题或真实对方 | `MessageProposal.addressed_to` + Gate 的 `response_actor_ids` + `TurnMessage.addressed_to` 字段已具备 | 不改字段，补措辞 |
| 角色不虚构现实经历 | 人格段落只有“可以角色扮演，但不编造刚刚直播、吃饭、见队友等现实经历” | 在系统提示补通用禁止项 |
| 弱机会发言不延长关注窗口、不触发新心跳 | `actor._focus_renewal_actors` 只对 `mention`/`reply_to_bot`/`private_message`/`awaiting_response` 或任务/工作关系续期，`sample_opportunity`、`keyword_opportunity` 不在其中 | 不改（读码确认） |

真正的缺口在输入表示：`input_status` 只列出 `ref`、`original_complete` 和 `attention_signals`（`at_bot`/`reply_to_bot`/`name_matches`/`direct_message`）。同一批来源里，**随机抽到的普通消息**和**针对 Bot 的连续交流**在这一层看起来几乎一样：两者都可能带上 `name_matches` 或什么信号都没有。模型只能从原话内容猜这是不是冲自己来的，于是两个方向都出错——把抽样机会当成请求去查资料或建工作，或者把明确的连续交流当成路过而沉默。

计划禁止新增固定前置分类模型，也禁止为分流再跑一次模型，因此本提交不新增判定，而是把**已经算好的注意力判定**暴露给模型：唤醒本身就携带 `reasons` 与 `certain`，本来也随 `pending_directory` 出现在请求里，只是没有与被唤醒的那条原话绑在一起。现在在 `input_status` 的每条待处理来源上补充同一份判定，并在系统提示说明 certain 与 non-certain 的差别、弱机会不能当作委托、沉默是正常结果。

第二处改动是现实经历的表述：原措辞只写在人格资料段落（“可以角色扮演，但不编造刚刚直播、吃饭、见队友等现实经历”），而角色资料是用户可编辑的文本。把同一约束写进系统提示的参与段，并明确“直播、房间和订阅类来源只支持它实际记录的状态”，避免把来源里的房间状态说成 Bot 自己的现实行动。

### 实际改动

- `src/len_bot/cognition/context.py`
  - `input_message`：待处理来源条目增加 `wake: {reasons, certain}`，直接取自 `session.pending_wakes`；只对确实进入本轮的唤醒附加，未产生唤醒的相关原话不带该字段。不新增存储、不新增模型调用、不改动 `attention.py` 的任何判定。
  - 系统提示参与段写明：`certain=true`（专门找你、回应你的发言、私聊、你正在进行的交流、明确委托）需要有处理结果；`certain=false`（关键词或随机抽到的公开话题）只是可以接一句的机会，别人互相讨论或话题与你无关时旁听即可，这种机会不是委托，不能据它建立工作、提醒或长期认识；沉默是正常结果，不需要为了参与另找话题。
  - 系统提示补现实事实边界：没有可核对来源时不声称刚结束直播、正在忙现实中的事、离开/回到某处或参加活动，也不写进旁白。

### 未做的事

- 没有增加前置分类器、没有增加第二次模型调用、没有新增话题图谱或情绪状态：判定仍来自既有 `AttentionPolicy`，参与决定仍由同一个 Social Core 的 respond 给出。
- 没有新增配置项、没有改 `AttentionPolicy` 的某些/非某些判定、没有改关注窗口续期规则。
- 没有把 `certain=false` 做成硬校验（例如禁止以弱机会来源建立工作）。计划把发起者类型与计费主体放在 C06/C07，本轮只在提示中说明边界；工具层仍按原有“请求来源必须是已读人类原话”校验，不提前引入第二套授权判定。
- 没有新增参与理由字段：Trace 里已有的 `respond.note`（`decision_reason`）与 `response_actor_ids` 继续承担参与理由与对象关系，本轮不改 Trace 结构。

### 静态核对

- `git diff --check`
- `uv run --no-dev python -m compileall -q src/len_bot`
- 未运行测试、模型或真实群；certain/non-certain 的区分没有在真实对话里确认过模型是否据此改变参与。

### 未确认项

- “弱机会不再被误当成请求”只有输入表示与措辞两处依据，需要真实群聊对照（同一轮里抽样到的无关消息与针对 Bot 的连续交流各一次）才能确认。
- 现实经历禁止项是提示级约束，`character_context` 仍可被运营者改成包含现实经历的文本；本提交不校验人格字段内容。
- `_focus_renewal_actors` 对弱机会不续期属于当前实现的读码结论，未在真实发送回执上核对抽样参与后的关注窗口是否真的没有延长。

---

## C06 类型化发起者与能力授予

### 设计判断

计划 M04 的要求可以拆成四件互不替代的事，C06 只做前三件的结构与判定，第四件（各类新能力自身的实现与放开）留给依赖它们的提交。

| 计划要求 | 当前实现（C06 之前） | 本提交的做法 |
|---|---|---|
| human/system/plugin 分支明确，不填假 QQ 号 | 工作创建要求人类来源，但“哪一类发起者”只从是否有 `requester_qq_uid` 推断；无 system/plugin 发起工作的路径 | 新增判别联合 `Initiator = Human \| System \| Plugin`；创建时必须恰有一个分支，且各分支有各自成立条件 |
| 不以 None/空 UID 绕过原请求来源验证 | 人类来源校验是 `actor_id != 'user:' + proposal.requester_qq_uid`；一条 `requester_qq_uid=None` 必然不相等，因此“拒绝”本身依赖字符串拼接的副作用 | 抽出唯一判定 `human_event_uid(event)`（事件类型 + `user:` 前缀 + 纯数字 UID），调用方按类型分支，系统/插件来源不再经过人类分支 |
| grant 不能由模型伪造 | 不存在 grant | 授予只在根配置 `access.capability_grants`，只由已登录面板写入；模型面向的工具参数里没有该字段，提案模型也不导出它 |
| 未配置新能力不扩权 | 不存在能力概念 | 授予默认空列表；`CapabilityAuthority.check()` 在缺失、停用或过期时一律拒绝，且不缩小用户目标或改写额度 |
| 检查顺序 | 分散在各处 | 新窄模块封装前三步（当前场景 → 真实来源与发起者 → 当前 grant），后四步仍留在原位置 |

三处判断值得单独说明：

1. **为什么新增 `runtime/capabilities.py` 而不是扩展现有判定。** 计划 M04 明确允许“可新增窄 `runtime/capabilities.py`，仅封装本项目能力检查”，同时 [`AGENTS.md`](../AGENTS.md) 禁止“不新增…权限平台”。两者的分界在于：这里只放本项目的能力词汇、一条授予记录和一次检查函数，不引入策略引擎、评分、签名或第二份 ACL。数据库里没有第二份可编辑授权表——这是计划第 5.2 节对 CapabilityGrant 存放位置的直接要求。

2. **为什么旧的 `requester_qq_uid`/`request_source_event_id` 两个字段保留。** 它们已经是工作记录、面板、Gate 白名单判定和插件调用的既有事实来源，删除它们会牵动 `scenes/actor.py`、`cognition/context.py`、`tools/retrieval.py` 等与本次业务缺口无关的路径。计划第 9.3 节要求“旧工作能从明确 requester/source 字段形成 Human initiator；缺乏确切来源的旧记录保留 `legacy_unknown`”。因此保留字段，并让 `legacy_initiator()` 只在这两个字段都确切存在时形成人类分支；否则 `initiator` 保持 `None`（即计划所说的缺少确切来源，如实保留为空，不猜、不补默认值）。

3. **`JobProposal.initiator` 对模型不可见。** 计划 D04 要求“系统事件 ID 由 Scheduler/Runtime 生成，模型只能引用，不能自己构造一条‘已获授权’的 system-origin”。`start_work` 只让模型填 `request_source`（一个已读原话引用），`stage()` 与 `stage_plugin_work()` 再从该真实事件构造人类分支；`JobProposal` 是内部模型，不从模型 JSON 直接解析，模型提交的 `Respond`/`StartWork` schema 里没有 `initiator`。

### 实际改动

- `src/len_bot/events/models.py`
  - 新增 `HumanInitiator(user_id, request_event_id)`、`SystemInitiator(agent_id, trigger_event_id, purpose?, cycle_id?)`、`PluginInitiator(plugin_id, source_event_id, run_id)`，三者各自 `frozen`、`extra='forbid'`、`strict`，并各自给出 `billing_subject`（`user:<真实UID>` 或 `system:<明确用途>`）。
  - 新增判别联合 `Initiator`（`principal_type` 判别）与 `legacy_initiator(requester, request_source)`：只有两个字段都确切存在才转换，否则返回 `None`。
  - 新增 `human_event_uid(event)`：本项目唯一判断“这条事件能否代表一个人”的位置——事件类型必须是群/私聊入站，`actor_id` 必须是 `user:` 前缀且剩余部分是正整数；否则返回 `None`。新增 `human_initiator_for(event, bot_actor_id)` 在其上构造人类分支。
- `src/len_bot/cognition/jobs.py`
  - `JobProposal` 增加 `initiator: Initiator | None`；新增 `human_initiator` 只读属性。
  - 创建分支改走 `_resolve_initiator()`：先按旧的明确字段尝试转换人类分支；仍为空则明确要求 typed system/plugin；然后要求请求锚点必须出现在本次证据中，并逐项核对人类分支的 UID 与请求原话和旧字段一致。非人类分支携带 `requester_qq_uid` 直接拒绝。
  - 控制操作（revise/cancel/resume）不接受自带 `initiator`：计划要求它们沿用原工作发起者，不能借控制替换身份。
- `src/len_bot/cognition/proposals.py`
  - `request_event()` 的人类来源校验改用 `human_event_uid()`，语义不变（当前场景、截点内、已读、非 Bot 自己）。
  - 新增 `human_source(event)`：从真实事件构造人类分支，失败即拒绝。`start_work` 与插件 `stage_work` 都填该值，模型参数不参与。
- `src/len_bot/plugins/models.py` / `src/len_bot/plugins/host.py`
  - `PluginCallContext` 增加 `initiator`，`None` 表示本次调用未建立发起者，明确区别于“系统发起”。
  - 新增 `handler_initiator(event, origin, bot_actor_id)`：真实插件事件 → 插件分支；人类原话 → 人类分支；其余（含 Bot 自己的消息、系统事件）→ `None`。`handler_call()` 使用该结果。
- `src/len_bot/runtime/job_store.py`
  - 创建校验从单条字符串比较改为 `_validate_job_initiator_in_transaction()`：先确认请求锚点是本场景中真实存在且列入证据的事件，再按 `principal_type` 分三条互斥路径——human 要求 `GROUP/PRIVATE_MESSAGE_RECEIVED` 且 `actor_id == 'user:' + user_id`；plugin 要求 `actor_id == 'plugin:' + plugin_id`；system 要求 `actor_id` 以 `system:` 开头且 `agent_id` 与实际生产者一致。三条路径都额外要求发起者引用的原话与其请求锚点一致，非人类分支一律不得携带 QQ 号。
  - 工作 payload 写入 `initiator`；`_decode_job()` 读回时，旧记录按自身 `requester_qq_uid`/`request_source_event_id` 转换，缺锚点则保持 `None`。
  - 同一人类请求的重复创建仍按原样比对，新增比对 `initiator`，避免同一请求锚点下出现不同身份的工作。
- `src/len_bot/runtime/capabilities.py`（新增，窄）
  - `Capability` 词汇表按计划 M04 建议：`long_work`、`public_research`、`network_python`、`proactive_chat`、`interest_share`、`send_file`、`bilibili_authenticated_read`、`bilibili_like`、`bilibili_favorite`。能力是本次项目自己的词，不把工具名当权限。
  - `CapabilityGrant` 按计划最小字段：`grant_id`、`revision`、`operator_id`、`principal_type`/`principal_id`、`scene_id` **或** `system_scope`、`capabilities`、`expires_at`、`resource_policy`、`concurrency`、`enabled`。`resource_policy` 只是既有策略的名称引用，不在此复制额度数值。
  - `CapabilityAuthority`：`required_for_work()` 给出“这一类工作需要哪些能力”（目前 information 工作对应 `public_research`，其余未知类型回落到 `long_work`）；`check()` 按**当前场景 → 当前 grant** 顺序判定，返回带 `step` 与 `reason` 的 `CapabilityDecision`，便于真实拒绝理由可读。
- `src/len_bot/config_store.py`
  - `AccessSettings` 增加 `capability_grants`（默认空）与 `grant_id` 唯一性校验。`qq_reply_whitelist` 语义未变。
- `src/len_bot/runtime/gate.py` / `src/len_bot/runtime/agent_runtime.py`
  - `RuntimeGate` 增加 `capability_authority`；在既有“工作已启用”检查之后，对 `operation=='create'` 且非人类分支的提案执行 `_capability_refusal()`，任一必需能力未被授予即整个提交以 `SILENCE` 拒绝，不落到事务里。
  - 人类发起的工作创建完全不走该分支；白名单与普通聊天判定未改。同时把 `requesters` 收集的 `None` 过滤掉（此前 `requester_qq_uid=None` 会进入集合并被 `chat_allowed` 判为不合格，属于隐式依赖）。
- `src/len_bot/web/routes/settings.py` / `src/len_bot/web/frontend/src/views/SettingsView.vue`
  - `GET /api/settings/access` 继续返回该节完整内容（现在含 `capability_grants`）。
  - `PUT` 改为 `AccessSettingsRequest`：`capability_grants` 省略时保留当前授予（旧页面只提交白名单不会静默清空授权）；每次保存把签发运营者改写为当前登录账号（`user` 由既有鉴权给出），并在授予集合实际变化时写一条 `capability_grants` 的 `OPERATOR_ACTION`。路由仍只挂在既有登录态之后。
  - 系统设置“QQ 回复资格”页在原有白名单下新增“能力授予”区：每条授予可编辑 ID、版本、主体类型与标识、生效场景或系统范围、能力列表、有效期、资源策略引用、并发与启停；写明未配置/停用/过期一律不放行，撤销只阻止后续操作。
  - 前端已实际重新构建（`npm run build`，442 modules → `SettingsView-CZQASS93.js` 51.69 kB），生成产物与后端同批进入本次提交。

### 未做的事

- 没有为任何插件加 `required_capabilities` 字段，也没有按工具名生成授权：现有工具准入仍由 `plugin_allowed`/`roles`/`available` 决定。**逐工具的能力要求随真正需要它的能力（文件上传、账号动作等）在各自提交引入**，此时提前加一个没有调用方的字段只会制造第二份不生效的判定。
- 没有实现 `proactive_chat`、`interest_share`、`send_file`、B 站读写的任何行为：本提交只有能力词汇与授予结构，出现某个能力名不等于该能力可用。
- 没有建立 `usage_reservations`、日额度或任何计费逻辑（计划 C07）；`CapabilityGrant.resource_policy` 目前只是一个名称引用，没有解析器。
- 没有新增系统工作入口：`SystemInitiator` 类型与事务校验已就位，但当前没有 Scheduler/心跳创建系统工作的路径（属计划 C12/M12），因此非人类分支在本轮只有插件事件处理器这一条真实来源。
- 没有改动 `cognition/models.py` 的 `EpisodeOutcome`、`TaskProposal`、`MessageProposal` 字段结构；消息与提醒的既有归属规则未动。
- 没有改 `ScenePolicy` 的既有判定（`enabled`/`chat_allowed`/`plugin_allowed`/`maintenance_allowed` 全部保持原样）；`delegable_work_allowed` 仍是 C05 的派生实现。
- 没有改实际根配置 `lenbot.config.json`：它仍由原解析器读为 `capability_grants: []`（本次只读核对了加载结果）。样例配置补上显式空列表，便于人工初始化时看到该字段存在。
- 没有新增、修改或运行测试、夹具或断言式探针。

### 静态核对

- `git diff --check`
- `uv run --no-dev python -m compileall -q src/len_bot`（退出码 0）
- `npm run build`（`src/len_bot/web/frontend`，442 modules，成功）
- `uv run --no-dev python -c "ConfigStore.load()"`：实际根配置仍能加载，`access = {'qq_reply_whitelist': [], 'capability_grants': []}`
- 本地对象级核对（非运行服务）：已授予/未授予/已过期/已停用/场景关闭五种情况各返回预期判定；`handler_initiator` 对群消息、插件事件、Bot 自身消息、系统事件分别返回人类、插件、`None`、`None`。
- 未运行测试、模型、OneBot、容器、浏览器或真实群；未启动服务。

### 未确认项

- **D04/D06/D09 的具体取值仍未获用户确认**：本提交按计划第 2 章的推荐裁决实现（D04 显式区分三类发起者、不填假 QQ 号；D09 uncertain ≠ allow——C06 尚未引入审查，故当前只是“无授予即拒绝”）。若用户选择不同取值，只需调整依赖该条的判定，不影响其余模块。
- **真实 system 来源路径未经验证**：`SystemInitiator` 的事务校验只有在存在系统工作创建者时才会实际走到；本轮没有该路径，因此该分支只有源码级依据。
- **插件的真实来源前缀**：校验假定插件事件 `actor_id == 'plugin:' + plugin_id`（读码确认 `plugins/base.py:117` 如此写入），未在真实插件事件上核对过。
- **撤销语义**：计划要求“保存配置成功后更新生效版本；已经发送的字节无法撤回”，本提交实现了保存即生效与 `OPERATOR_ACTION` 记录，但“正在执行的工作按能力策略停止并收尾”属于 C09/C10 范围，本轮未实现，因此不能在文档中宣称撤销会停止进行中的工作。
- **`legacy_unknown`**：计划第 9.3 节提到的这个标记名本轮未落库——缺少确切来源的旧记录在读取时 `initiator` 为 `None`，没有新增列或状态值。是否需要显式标记名，取决于后续是否有依赖它的迁移动作。

### 涉及持久字段

- 工作 `tasks.payload` 增加 `initiator`（可选对象）。新记录总是写入；旧记录读回时按自身字段转换，没有离线转换步骤，也不需要停机迁移。
- 根配置 `access.capability_grants` 增加字段，默认空；实际根配置未改动。
- 没有 `CREATE TABLE`、`ALTER TABLE`，没有新增列。

---

## C07 共享用量原子预占与结算

### 设计判断

计划 M05 的额度模型有三条互不替代的要求：创建期一次性预占（D06：每日额度只在创建时检查会超发）、调用期可区分真实 usage 与本地估算、以及子调用归同一账。C07 落前两条的全部与第三条的收集，**执行期逐次扣减不属于本提交**（计划 C08）。

| 计划要求 | 当前实现（C07 之前） | 本提交的做法 |
|---|---|---|
| 创建时原子预占，并发不重复用同一余额 | 无任何额度概念；只有 `RuntimeConfig` 的单次执行预算（步数/上下文/输出） | 新增 `usage_reservations`；预占写入 `apply_job_proposals_in_transaction`，它已经在 `commit_proposal_transaction` 的 `BEGIN IMMEDIATE` 写事务内，因此工作行与预占同生共死 |
| 每次调用结算、按唯一 call_id 不重复扣除 | `model_calls` 已按 `call_id` 记录 `usage_json`/`estimate_json`，但没有汇总语义 | `settle_reservation_in_transaction` 按 `job_id` 汇总全部 `model_calls`，一次工作只写一行结论 |
| 输入输出各计一次，缓存与推理不重复相加 | 无 | `measured_call_tokens()`：`prompt_tokens + completion_tokens` 即整笔计量，`cached_tokens`/`reasoning_tokens` 已含在内，不再二次相加 |
| 供应商无 usage 时记录估算方法与保守预留，界面不得称为真实计费 | `estimate_json` 已存在，但没有任何路径说明“什么时候它是唯一的数字” | 无可用 usage 时记 `usage_tokens=0`、`estimated_tokens=本地输入估算 + 配置输出上限`；两列在库里分开，页面列名为“实际/本地估算” |
| 用户额度按真实 UID 在账务时区一天内全局计算，跨群不重复 | 无 | 账务主体取 C06 的 `billing_subject`（`user:<真实UID>`/`system:<用途>`）；`ReservationPolicy.day_key(ts, timezone)` 用既有 `time.timezone` 计日；同一天同一账号跨场景只累加一次 |
| 跨日归属按工作预占日固定 | 无 | `day_key` 在预占行上落库，结算只改该行状态，不重算日期 |
| 策略数值只有一个可编辑来源 | 无 | 根配置新增 `resources.policies`；`CapabilityGrant.resource_policy` 只存名称，由 `CapabilityAuthority.policy_for_grant` 解析 |

四处判断值得单独说明：

1. **预占额由既有执行预算推导，而不是新增一个可编辑数字。** 计划要求“创建时预占该工作的预算，不超过 10M”。本提交把预占额算成 `min(work_token_limit, job_max_steps × (job_context_tokens + work_output_tokens))`，即 `min(10_000_000, 24 × (64000 + 16384)) = 1,929,216`。这样预占永远不可能大于运行器实际会执行的量，运营者改根配置的执行预算时预占自动跟随，不需要在第二处同步数字，也不会出现“预占比它能花的更多”这种把并发额度虚占光的形态。`work_token_limit=0` 时预占为 0——那是运营者明确的“本工作不得计费”而不是“不限”。

2. **拒绝文案不静默改写目标。** 计划 D06 要求“授权撤销仍即时限制新操作”，同时 M05 要求“不能静默改写目标或额度”。额度不足时预占直接抛错并上抛为整个提交的 `SILENCE`（`runtime/gate.py:296` 的既有 catch），工作不创建；错误文本写明账号、日期、已占用、本次需要、上限，并指明“可以明确要求一个较小的工作范围”。没有“自动降级到小模型”“自动缩小目标”的分支。

3. **结束方式分三种，取消不能抹掉已花的钱。** `close_reservation_in_transaction` 先查该工作有没有任何 `model_calls`：没有则整份释放（`released`，不占余额）；有则结算为真实消费（`settled`）。`complete_job`、`interrupt_job` 与 `plan` 里的取消操作都走这条判定，且都在各自的既有事务内。因此取消一个尚未开始的工作不消耗额度，取消一个已经调过模型的工作保留其已消费。

4. **`resources.policies` 默认值为空字典，升级不改变行为。** 计划第 5.2 节要求额度策略只有一个可编辑来源。若默认就写入一份 10M/30M 的策略，等于给所有既有部署凭空加了一条限制；因此默认是 `{}`，由 `RuntimePolicy` 的字段默认值（10M/30M/场景不限）兜底，只有运营者显式在根配置或页面里新建策略才改变行为。`CapabilityGrant.resource_policy` 指向一个不存在的名字时同样回到默认，而不是报错或赋零。

### 实际改动

- `src/len_bot/cognition/budget.py`
  - 新增 `ReservationPolicy`（pydantic，`extra='forbid'`、`strict`）：`work_token_limit`（默认 10M，`None` 表示不设该维度上限）、`daily_user_token_limit`（默认 30M）、`daily_scene_token_limit`（默认 `None`，即本场景未配置）。附 `day_key(timestamp, timezone)` 与 `work_reservation(model_steps, context_tokens, output_tokens)` 两个纯函数。
  - 既有 `AgentBudget` 未改一行：它仍是执行期次数维度账本，token 维度在执行期的强制属 C08。
- `src/len_bot/cognition/call_store.py`
  - 新增纯函数 `measured_call_tokens(usage, estimate, *, conservative_output_tokens) -> (usage_tokens, estimated_tokens)`：两个字段都是数值才算真实 usage（`prompt+completion` 一次计完）；否则记 0 真实 + 本地输入估算加配置输出上限。半份 usage（只有输入没有输出）不拆分，按估算保留，理由写在 docstring。
  - `initialize_model_calls` 新增 `usage_reservations` 表与 `(subject, day_key)` 索引。
  - 新增 `account_used_tokens_in_transaction`（held 记 `reserved_tokens`，settled 记 `usage+estimated`）、`reserve_work_in_transaction`（账号日额度与场景日额度两道拒绝）、`release_reservation_in_transaction`、`settle_reservation_in_transaction`、`close_reservation_in_transaction`。全部是“在调用方事务内运行”的原语，自己不开事务、不加锁。
- `src/len_bot/runtime/job_store.py`
  - `billing_subject_for()`：账务主体取 C06 的 typed initiator；没有明确身份时 `subject_for` 抛错，不回落成群级或空账号。
  - `reservation_policy_for()`：取默认策略，若该发起者在当前场景有一条 `long_work` 授予且授予指向可解析的策略名，则用该策略，并把 `grant_id` 记进预占行。
  - `reserve_job_budget_in_transaction()` / `settle_job_budget()`、`job_reservation()` / `list_job_reservations()`。
  - 接线：创建分支在 `INSERT INTO agent_jobs` 之后、`_queue_job_event` 之前预占；控制分支 `operation=='cancel'` 关闭预占；`complete_job` 与 `interrupt_job` 在各自 `commit` 之前关闭预占。
- `src/len_bot/events/store.py`
  - `EventStore.__init__` 新增三个由 Runtime 注入的属性：`budget_config`（既有 `RuntimeConfig`）、`capability_authority`、`billing_timezone`。缺省时按项目默认策略预占，工作不会静默变成不限额度。
- `src/len_bot/config_store.py`
  - 新增 `ResourceSettings(policies: dict[str, ReservationPolicy])` 与 `RootConfig.resources`，默认空。
- `src/len_bot/runtime/capabilities.py`
  - `CapabilityAuthority.policy_for_grant(grant)`：按名称解析，未命名/失效/无授予一律返回 `None`，由调用方使用默认策略。
- `src/len_bot/runtime/agent_runtime.py`
  - `_apply_budget_configuration()` 在构造期与 `update_root_settings('resources')` 之后注入上述三项配置；`'resources'` 加入允许的根配置节。
- `src/len_bot/web/query_service.py` / `src/len_bot/web/routes/`
  - `resource_settings()` 与 `GET/PUT /api/settings/resources`。
  - `model_reservations()` 与 `GET /api/models/reservations`：按当前账务日给出 limits、逐账号 held/used/available、逐工作预占明细（含 `usage_tokens` 与 `estimated_tokens` 两列）。只读，数字来自预占事务写下的同一批行。
- `src/len_bot/web/frontend/src/views/`
  - 模型页新增“工作额度预占”卡片（账务日、三维上限、逐账号余额、逐工作明细，工作 ID 走既有 `EntityLink`）；系统设置页新增“额度策略”页签（JSON 编辑 `policies`），能力授予的 `resource_policy` 提示改为“只填名称”。
  - 前端已实际重新构建（`npm run build`，442 modules → `ModelsView-B4IlBVo0.js` 49.58 kB、`SettingsView-CuDxwB_Z.js` 53.78 kB），产物与后端同批进入本次提交。
- `lenbot.config.example.json`
  - 增加 `"resources": {"policies": {}}`，便于人工初始化时看到该节存在；实际根配置未改动。

### 未做的事

- **没有把 token 维度接进执行期。** `AgentBudget` 仍是次数维度；`usage_reservations` 只在创建与结束时读写，执行中的每次模型调用不会实时扣减工作余额。计划 M05 的“每次调用：检查工作余额 → 预留本次估算 → 结算”属 C08 `feat(agent): enforce resource budgets across native loops` 的完成条件（“deadline 与 token 真实停止”）。本提交因此不能宣称“超过额度会自动停止执行”。
- **没有新增绝对 deadline 或累计 token 硬停。** 既有 `job_max_seconds` 仍是单次执行超时且恢复会重新计时（见 [`readiness`](social-agent-readiness.md) 第 2 节的“绝对 deadline / 累计 token 上限”行），计划 C08/C09 处理。C08 落地后该行已更新。
- **没有做跨日结转或历史回填。** 旧工作没有预占行，`list_job_reservations` 只列现有行；不追溯、不补算。
- **没有为场景维度填默认数值。** `daily_scene_token_limit` 默认 `None`；计划第 2 章未给出场景维度的推荐值，本提交不替运营者决定。
- **没有新增自动迁移脚本。** `usage_reservations` 由既有 `CREATE TABLE IF NOT EXISTS` 建；没有 ALTER、没有离线转换、没有校验和清单。
- **没有新增、修改或运行测试、夹具或断言式探针。**

### 静态核对

- `git diff --check`（退出码 0）
- `uv run --no-dev python -m compileall -q src/len_bot`（退出码 0）
- `npm run build`（`src/len_bot/web/frontend`，442 modules，成功）
- `uv run --no-dev python -c "ConfigStore.load()"`：实际根配置仍能加载，`resources.policies == {}`，`access` 两字段不变
- 本地对象级核对（非运行服务，临时库 `/tmp`，核对后删除）：
  - 策略解析：授予指向可解析名称 → 用该策略；指向失效名称/未命名/无授予 → 回到默认（10M/30M/场景不限）。
  - `work_reservation`：24 步 → 1,929,216；100 步被 10M 上限截断 → 8,038,400；`work_token_limit=0` → 0；`None` → 算术上限。
  - `measured_call_tokens`：完整 usage `{1200,300}` → `(1500,0)`；带 `cached_tokens`/`reasoning_tokens` 的同一笔仍 → `(1500,0)`（不重复相加）；只有输入 → `(0, 17484)`；无 usage 且估算为 0 → `(0,4096)`。
  - 日边界：同一时刻在 `Asia/Shanghai` 与 UTC 下 `day_key` 分别为 `2026-09-14` / `2026-09-13`。
  - 预占生命周期：两次 10M 预占后账号已占用 20,000,000；第三次 10,000,001 被账号日额度拒绝（返回写明的拒绝文本）；场景维度独立计数并按场景上限拒绝；无模型调用的工作关闭后为 `released` 且不再计入；已调用模型的工作结算为 `(1500, 0)`，账号占用从 20,000,000 降为 1500；次日为 0。
  - 拒绝原子性：在 `BEGIN IMMEDIATE` 中先插入工作行再触发额度拒绝并回滚，工作行与预占行均为 0，证实拒绝不会留下半成品。
  - 并发：两个创建在同一把写锁上竞争同一份 10 余额，一个 `held`、一个被拒，账号最终占用 6。
  - 子调用归同账：同一 `job_id` 下的工作调用、压缩调用与插件子代理调用全部汇总进该行的 `usage_tokens`（1000+200+400+100=1700），无 usage 的子调用按估算计入；另一工作的调用未被计入。
- 未运行测试、模型、OneBot、容器、浏览器或真实群；未启动服务。

### 未确认项

- **D06 的具体数值仍未获用户确认**：本提交按计划第 2 章推荐裁决实现（创建时原子预占、并发不重复用同一余额），但“单工作 10M / 账号日 30M / 场景维度是否设限”这三项取值来自计划 M05 的叙述与 `RuntimeConfig` 既有默认，未获逐项确认。运营者可随时在“额度策略”页改；改成别的数值不需要改代码。
- **真实并发创建未验证**：上述并发核对是在同一个进程内、同一把 `asyncio.Lock` 上完成的。真实的两个群同时委派工作、或重启后并发，未在真实服务上核对过。
- **真实供应商 usage 形态未验证**：`prompt_tokens`/`completion_tokens` 之外的字段（如某些供应商的 `total_tokens` 语义）未在真实响应上核对；当前只把它们当作“已含在前两项内”。
- **执行期额度未生效**：见“未做的事”第一条，任何“超额度会停止”的表述都不成立。
- **恢复工作的额度归属**：`resume` 目前沿用原预占行（`job_id` 不变，`reservation_policy_for` 不会重写既有行），但“修订后的工作是否需要重新预占、原预占是否随修订放大”未定，计划 C09 `feat(jobs): preserve revisions and budget ownership on resume` 处理。
- **`policy_name` 存的是 grant_id**：预占行记的是签发它的授予 ID，不是策略名——策略名可以改指，授予 ID 是当时的授权事实。页面“策略”列当前只显示限额，未显示 `policy_name`。

### 涉及持久字段

- 新增表 `usage_reservations`（`job_id` 主键；`scene_id`、`subject`、`day_key`、`policy_name`、`reserved_tokens`、`status`、`usage_tokens`、`estimated_tokens`、`created_at`、`settled_at`），由既有 `CREATE TABLE IF NOT EXISTS` 建立，不需要停机迁移；索引 `idx_usage_reservations_day(subject, day_key)`。
- 根配置新增 `resources.policies`（默认空）；实际根配置未改动。
- 既有 `model_calls`、`agent_jobs`、`tasks` 的列没有变化；旧工作没有预占行，读回时不补算。

## C08 跨循环资源预算强制

### 设计判断

计划 M05 要求“不能保留未经检查的 `>= None`、减法或最后一轮判断”，并把停止写成“预算接近结束时，使用同一个 Agent 的预算消息让其收束；终结本身预留必要的输出与时间”。计划第 8.3 节把两个半成品形态明确列为禁止：只让 `AgentBudget` 接受 `None` 而不同时改循环终结判断，以及后端改了而提交旧前端产物。因此本提交不是“给类型加 `| None`”，而是三件事一起做：**把每个 `None` 语义定死、把每个判定改到同一条规则上、给不设次数的那一档一个真实的停止条件**。

| 计划要求 | 当前实现（C08 之前） | 本提交的做法 |
|---|---|---|
| 调用次数可以配置为有限或 `None` | `RuntimeConfig` 的六个次数字段全是必填 `int`；`PluginAgentRequest.max_steps`/`max_tool_calls` 同样是必填 | 六个次数字段与插件 Agent 的两个字段改为 `int | None`；`ConversationResume.model_calls_limit`/`tool_calls_limit` 同样 |
| 不能保留未经检查的 `>= None`、减法 | `social_core.next_is_final()` 直接减；`proposals.terminal_definition` 比 `<=1`；`job_runner` 与 `work_context` 做 `job_max_steps - model_steps`；`plugin_interactions` 做 `min(request.max_steps, limit-used-reserve)`；`group_summary/analysis` 做 `limit-used`；`reflector` 比 `max_steps == 1` | 新增 `count_remaining(limit, used) -> int | None` 与 `tightest(*bounds) -> int | None` 两个纯函数，所有判定经它们收口；`AgentBudget` 内部同样只走这两个函数 |
| `None` 次数模式没有漏算或类型错误 | — | `AgentBudget.state()/local_state()`、`take_model`/`take_tool`、`force_terminal`、`_refusal` 全部按 `None` 读；`AgentLoop.run` 的入口校验拆成显式分支，`None` 不再进入序比较 |
| deadline 与 token 真实停止 | `job_max_seconds` 只在执行段开头检查一次并作为 `asyncio.timeout`；恢复会重新计时；token 维度完全没有进入执行期 | `AgentBudget` 增加 `deadline`（绝对 `time.monotonic()` 时刻）与通过 `read_state` 读到的 `tokens_limit`/`tokens_used`；`_refusal()` 在启动下一次模型调用前给出 `elapsed_time`/`model_steps`/`tokens` 三种拒绝；工作的时间与 token 维度由 `budget_state()` 从持久记录读出，不由执行段自己计时 |
| 预留终结能力 | 只有次数维度预留（“最后一个模型步骤预留终结工具”） | `terminal_seconds_reserve` 与 `terminal_token_reserve`；`force_terminal()` 在剩余额度只够终结时也返回真，等价于既有“最后一次调用只给终结工具”的行为，但对期限与 token 同样成立 |
| 循环终结判断与恢复字段同步 | — | `ConversationResume.elapsed_seconds_limit` 新增，等待恢复继续用同一个窗口而不是重新计时；`job_runner.remaining_seconds()` 读持久累计时长 |

五处判断值得单独说明：

1. **`None` 的含义只有一种：这一维度不是停止条件。** 计划允许“可配置为有限或 None”，所以 `None` 不是“0 次”“不限但仍按 0 算”或“缺省待填”，而是运营者明确声明“不要用这个维度停止”。因此 `count_remaining(None, used)` 返回 `None` 而不是 0，`tightest()` 把 `None` 当作“本条不构成约束”而不是“最小值为 0”。这条规则让 `None` 在比较、减法与容量计算里都不会伪装成耗尽。

2. **停止条件必须真实存在，配置文件拒绝“全都不设限”。** 一个既没有次数上限、也没有期限的循环没有任何停止条件，`RuntimeConfig.budgets_fit` 因此直接拒绝这种组合（对话次数与期限不能同时为 `null`；工具次数与期限不能同时为 `null`）。历史维护循环本来没有期限维度，所以 `maintenance_max_steps` 保持必填——它不是“本轮新加的限制”，而是既有配置本来就提供的唯一停止条件；`maintenance_max_tool_calls` 才允许 `null`。插件 Agent 借用父账户，只有在父账户本身不存在时（handler 在循环外直接调用）才要求请求自己给出至少一个次数维度，否则运行期直接报错。这些校验都在配置解析时拒绝，不靠运行到一半才发现。

3. **删除 `step_index == max_steps - 1` 而不是给它加 `None` 分支。** `None` 时 `step_index` 由 `itertools.count` 生成、没有上界，保留那句比较就必然要写成 `max_steps is not None and ...`，而它想表达的事实是“这是最后一次调用”。本提交把“是不是最后一次”统一交给 `remaining`（三个来源取最紧：本调用参数、账本计数、调用方报告的持久余量），`agent_loop` 与 `social_core` 的判断因此同源，不会出现两处对同一事实的不同算法。

4. **工作的时间与 token 都由持久记录说话，执行段不再自己计时。** `budget_state()` 从 `agent_jobs` 读累计时长与累计调用次数，并从该工作的 `usage_reservations` 行读它持有的 token 上限、从 `model_calls` 汇总已用 token（含压缩、技能维护与插件子 Agent）。因此“绝对期限”就是既有 `elapsed_seconds` 的语义加一个绝对判定：恢复不重置、排队未开始不计入，D05 说的三件事在同一列数据上成立，不需要新增计时表。`terminal_token_reserve` 取一整次请求（`job_context_tokens + work_output_tokens`），含义是“剩下不足一次请求时才停止”，而不是“剩一万 token 就停”——比它更粗的阈值会让正常收尾被提前打断。

5. **C07 的预占在这一步才真正变成硬上限。** 创建期预占给出该工作最多能花多少，本提交让执行期的 `tokens_limit` 就是那个预占额，两者是同一个数字而不是两个可能互相矛盾的配置。这样 M05 的“每次调用：检查工作余额”与 C07 的“创建时原子预占”闭合成一个环：预占决定上限，上限决定停止，停止时的真实消费又写回同一行。

### 实际改动

- `src/len_bot/cognition/budget.py`
  - 新增纯函数 `count_remaining(limit, used)`（`None` → `None`）与 `tightest(*bounds)`（忽略 `None`，全为 `None` 时返回 `None`）；新增 `terminal_seconds_reserve(seconds_limit)`（30 秒与上限四分之一取小，`None` → 0）与 `window_deadline(seconds_limit, elapsed)`（绝对窗口，恢复按已用时间继续倒计时）。
  - `AgentBudget`：`model_limit`/`tool_limit` 允许 `None`；新增 `deadline`、`terminal_token_reserve`、`terminal_seconds_reserve`；新增 `local_state()`（不 await 的计数快照，保留最近一次持久快照的时间与 token 维度）、`_seconds_left()`、`_refusal()`、`deadline_seconds()`、`force_terminal()`；`take_model`/`take_tool` 按 `count_remaining` 判定，拒绝时带 `budget_kind`。
  - `ReservationPolicy.work_token_limit` 改为 `int | None`（默认仍 10M）；`work_reservation(model_steps=None)` 在没有次数上限时返回策略自身的单工作上限（策略也不设时返回 0）。
- `src/len_bot/cognition/agent_loop.py`
  - `max_steps`/`max_tool_calls` 允许 `None`；入口校验拆成显式分支，不再写 `not 0 <= initial < max_steps` 这类会碰到 `None` 的表达式。
  - 步骤序列：有限用 `range`，不设限用 `itertools.count`；每轮 `remaining = tightest(本调用余量, 账本余量, 调用方报告余量)`，`remaining == 0` 才抛 `AgentBudgetExhausted`，`remaining == 1` 或 `account.force_terminal(state)` 就强制终结。
  - 删除 `step_index == max_steps - 1` 的两处判断；工具批量检查改为 `tightest(count_remaining(max_tool_calls, initial+local), count_remaining(账本))`。
  - `execution_budget_message` 在 `None` 档输出 `null` 与“本次执行的调用次数未设上限；期限与累计 token 才是停止条件”，并透出 `tokens_remaining`；`install_budget` 同时返回 view 与 state。
- `src/len_bot/config.py`
  - `conversation_max_steps`、`conversation_max_tool_calls`、`job_max_steps`、`job_max_tool_calls`、`maintenance_max_tool_calls` 改为 `int | None`；新增 `conversation_window_seconds`（默认 `None`）。
  - `budgets_fit` 新增两条拒绝：对话次数与该轮期限不能同时为 `null`，否则该轮没有停止条件。`maintenance_max_steps` 保持必填，理由见设计判断第 2 条。
  - `EXECUTION_BUDGET_FIELDS` 增加 `conversation_window_seconds`，页面“执行预算”表因此能显示该维度。
- `src/len_bot/cognition/models.py`
  - `ConversationResume.model_calls_limit`/`tool_calls_limit` 允许 `None`；新增 `elapsed_seconds_limit`（默认 `None`，即本轮没有期限维度）。
- `src/len_bot/cognition/social_core.py`
  - `AgentBudget` 构造带上 `window_deadline(config.conversation_window_seconds, 已用时长)`、`terminal_seconds_reserve(...)` 与 `terminal_token_reserve=conversation_output_tokens`；恢复按 `resume.elapsed_seconds` 继续同一个窗口。
  - `next_is_final()` 改为问 `force_terminal`；`append_update` 的 `can_absorb` 用 `count_remaining`；`ledger.remaining_model_calls` 改为返回 `None` 表示不设限；`ConversationResume` 保存 `elapsed_seconds_limit`。
  - `plugin_interactions._respond_agent` 不再为恢复重建一个独立账户，直接用 C07 起父执行持有的那一个（否则新窗口会在恢复时被覆盖）。
- `src/len_bot/cognition/proposals.py`
  - `terminal_definition` 与 `finish` 的续跑判断按 `None` 读：不设限时终结工具保留完整 action 集合，不再因为“余量 0”拒绝 `continue`/`wait`。
- `src/len_bot/runtime/job_runner.py`
  - `budget_state()` 增加 `tokens_limit`（该工作的预占额）与 `tokens_used`（该 `job_id` 下全部模型调用按 C07 的 `job_measured_tokens` 汇总）；新增 `remaining_seconds()` 从持久累计时长算剩余。
  - `execution.budget` 带上期限与两个终结预留；`AgentLoop(...)` 的 `max_steps`/`max_tool_calls` 改为 `count_remaining(...)`，`request_definitions` 只在真的只剩一次调用或工具额度用尽时才只给终结。
  - `finish` 的 partial 原因增加 `token_budget_exhausted_at_finish`，并让每个维度只在真的带数值时参与判定。
- `src/len_bot/runtime/plugin_interactions.py`
  - 子 Agent 的 `steps` 改为 `tightest(request.max_steps, count_remaining(账本))` 再减保留位，任一维度不设限都不再参与。
- `src/len_bot/memory/reflector.py`、`runtime/work_context.py`、`plugins/builtin/group_summary/analysis.py`、`runtime/job_store.py`、`runtime/agent_runtime.py`
  - 维护、压缩、报告批次与工作恢复的判定全部改到 `count_remaining`/显式 `is not None`，不再做“上限减已用”。
- `src/len_bot/web/frontend/src/views/`
  - 运行参数页“执行预算”表增加“每轮对话绝对期限”，并把 `null` 显示为“不设限（由其他维度停止）”而不是空白；Jobs 页用量行同样按 `null` 显示“不设限”。前端已实际重建（`npm run build`，442 modules，成功），产物与后端同批进入本次提交。
- `lenbot.config.example.json`
  - 增加 `"conversation_window_seconds": null`，让人工初始化时看得到该维度存在。实际根配置未改动。

### 未做的事

- **没有新增绝对期限的独立计时表。** 计划 D05 的“首次开始后绝对 1800 秒”由既有 `agent_jobs.elapsed_seconds` 加上本次的绝对判定实现；没有新增列、没有第二份计时。
- **没有把 `conversation_token_limit` 做成配置项。** 对话轮次的 token 维度目前只有“终结预留”而没有独立上限：计划第 2 章只给工作和账号日规定了 token 上限，对话轮次的停止条件是次数与（可配的）期限。因此在 `budgets_fit` 里拒绝“次数与期限同时为空”，而不是凭空加一个没人配置的 token 上限。
- **没有修改任何既有默认值。** `conversation_window_seconds` 默认 `None`，六个次数字段的数值不变，实际根配置一行未改；升级不会因此多出任何限制。
- **没有做跨进程恢复的 deadline 重建。** 等待型恢复（`ConversationResume`）保留原窗口；工作恢复读持久累计时长。两者的“进程重启后旧挂起”仍按既有 `review_required` 处理，不自动续跑。
- **没有新增、修改或运行测试、夹具或断言式探针。**

### 静态核对

- `git diff --check`（退出码 0）
- `uv run --no-dev python -m compileall -q src/len_bot`（退出码 0）
- `npm run build`（`src/len_bot/web/frontend`，442 modules，成功）
- `uv run --no-dev python -c "ConfigStore.load()"`：实际根配置仍能加载，`conversation 8 16 None`、`job 24 48 600.0`、`maintenance 3 2`、`resources.policies == {}`
- `lenbot.config.example.json` 的 `runtime` 节可直接解析（`conversation_window_seconds=None`）
- 本地对象级核对（非运行服务，临时库 `/tmp`，核对后删除）：
  - `count_remaining(None, 7) → None`、`count_remaining(5, 7) → 0`、`count_remaining(5, 2) → 3`；`tightest(None, None) → None`、`tightest(None, 3, 5) → 3`。
  - 不设次数的循环 + 30 token 额度、每次调用 15：第 3 次调用被 `tokens` 拒绝（`calls 2`、工具 2 次），即停在一个真实的拒绝上而不是死循环，也不是第一轮就误判耗尽。
  - 不设次数 + 已过期的绝对期限：第一次调用前即以 `elapsed_time` 拒绝（`calls 0`）。
  - 只剩终结预留时（100 额度、已用 96、预留 5）：`force_terminal` 为真，第 1 次调用就只给终结工具、工具一次都没跑，正常提交结果。
  - 额度充足（10000）：工具继续可用，3 次调用后正常终结，未提前强制。
  - `window_deadline(600, 0) → 600 秒`、`window_deadline(600, 400) → 200 秒`（恢复不重置）；`terminal_seconds_reserve(600) → 30.0`、`(None) → 0.0`。
  - 预占推导：`model_steps=None` → 10,000,000（策略自身的单工作上限制），24 步 → 1,929,216。
  - 有限次数 3：第 3 次调用被强制为终结（`calls 3`、工具 0 次），与 C08 之前“最后一个模型步骤预留终结工具”的行为一致。
  - 工作账户：同一 `job_id` 下的工作调用、压缩调用与无 usage 的维护调用全部汇总（hold 1000 时合计 18134，`count_remaining` 为 0）；按真实规模预占（1,929,216）时单次调用后剩余 1,865,216，正常继续。
- 未运行测试、模型、OneBot、容器、浏览器或真实群；未启动服务。

### 未确认项

- **D05 的 1800 秒仍未获用户确认。** 本提交实现的是“绝对期限不重置、排队不计入”这一语义，默认值仍是根配置既有的 `job_max_seconds=600`（样例与既有部署都是 600，不是计划叙述里的 1800）。改成 1800 只需改根配置，不需要改代码。
- **对话轮次的期限维度默认关闭。** `conversation_window_seconds` 默认 `None`，即对话仍只按次数停止；要按计划第 2 章对“心跳 30 分钟”一类准确定义，仍需运营者显式设置。
- **token 停止只在工作时真正生效。** 对话轮次没有 token 上限（见“未做的事”），所以“超过累计 token 会停止”目前只对工作成立。
- **真实供应商的 token 计数未验证。** `tokens_used` 用的是 C07 的 `job_measured_tokens`，其口径（`prompt+completion`，无 usage 时按本地估算）仍属未在真实响应上核对的部分。
- **真实并发与恢复场景未运行。** 上述核对都在单个进程内的对象级调用上完成；真实运行中“期限到点正在等模型返回”“压缩消耗了最后一次调用”等竞态没有现场证据。

### 涉及持久字段

- 没有新增表或列。`agent_jobs.elapsed_seconds`、`model_steps`、`tool_calls` 与 `usage_reservations` 都是既有字段，本提交只是让执行期真正读它们。
- 新增索引 `idx_model_calls_job(job_id, started_at)`。它解决的具体缺口是：`budget_state()` 每次模型调用前后都要按 `job_id` 汇总该工作的全部调用，没有该索引时每次都是一次全表扫描；这不是缓存层，只是让这次既有查询走索引。
- `ConversationResume` 新增 `elapsed_seconds_limit`（存在于事件 metadata 的 `conversation_resume` 包内）。旧挂起包缺该字段时按 `None` 读，即“该轮没有期限维度”，与升级前行为一致。
- 根配置未改动；`lenbot.config.example.json` 增加一个键。

---

## C09 工作修订与预算归属保留

对应计划第 8.2 节的 C09 `feat(jobs): preserve revisions and budget ownership on resume`：`job_store`/`job_runner`、Gate、操作接口；完成条件是**继续不重置模型/预算/deadline**、**授权撤销限制后续操作**、**旧执行不会写新修订**。

这一条在计划里写得很短，但把 C07/C08 的账与 C06 的授权接到一起，落点不是三处新判定，而是让**同一条已存在的规则在控制路径上也成立**：额度只在创建时预占，恢复既然是“同一工作的下一次执行”，就必须重新受同一份额度约束；授权既然是“当前生效事实”，恢复既然是“下一次执行”，就必须重新过当前 grant；修订既然是“新版本”，旧版本的执行就不得再写它。

### 计划要求 / 本提交做法

| 计划要求 | 本提交做法 |
|---|---|
| 继续不重置模型 | 未改 `model_binding_json`（`bind_job_model` 仍只在首次绑定且 `revision` 匹配时写入），恢复沿用同一绑定；`_context()` 的 `resume_from` 提示不变。 |
| 继续不重置预算 | `apply_job_proposals_in_transaction` 在 resume/revise 改回可执行状态时，用**工作自己的 initiator** 把它**重新预占**到同一个累计上限（`rehold_job_budget_in_transaction`），并保留原预占行与原有 `day_key`；已消费的 token 不带回来，上限本身不放大。 |
| 继续不重置 deadline | 未改：`agent_jobs.elapsed_seconds` 在 resume 时不清零，C08 的 `remaining_seconds()` 继续读它。 |
| 授权撤销限制后续操作 | Gate 增加 resume 分支：用该工作**创建时的主体**过当前 grant，撤销/停用/过期的授予直接拒绝恢复；revise 与 cancel 不启动执行，保持原样不扩权。 |
| 旧执行不会写新修订 | 未改：既有写入口都带 `revision` 匹配（`job_checkpoint`/`complete_job`/`update_work_state`/`save_plugin_work_progress`/`pin_job_skill`/`save_job_compression`），`save_job_exchange` 只接受 `1 <= revision <= job['revision']`。本提交只核对，不新增。 |
| 操作接口 | 面板 `POST /api/cockpit/jobs/{id}/{operation}` 与模型工具 `resume_work` 走的是同一条 `JobProposal` → Gate → 事务路径，因此两处自动获得上述判定。 |

### 设计判断

1. **恢复必须重新预占，否则“继续不重置预算”是空话。** C07 在创建时预占，在 `complete_job`/`interrupt_job`/取消时把预占**换成实际消费**（`settled`）或**整份释放**（`released`）。恢复之后这份行已经不再表示“还能花多少”：`settled` 行的 `reserved_tokens` 等于它**已经花掉**的数，直接拿来做 C08 的 `tokens_limit`，工作会在自己的第一次调用上就因为“已用量 ≥ 上限”被拒绝——即“恢复”变成“立刻停止”。所以本提交把 resume/revise 改回可执行状态时显式重新预占。
2. **重预占不新增额度，也不搬日子。** 重新预占用的是工作**自己**的累计上限（`work_reservation` 的同一推导），不叠加、不与原值相加；行还在，`day_key` 不变，所以过去消费仍记在它被接受的那一天，D06 的“跨日归属按工作预占日固定”继续成立。重预占时的日/群额度检查用 `exclude_job_id` 把自己排除，避免把“自己那份结算”再算一遍而把一个工作收两次费。上限的**数值**与新建工作时同源——由当前策略与当前运行参数推出——所以运营者若在两次执行之间调高或调低了 `job_max_steps`，继续执行的工作按调整后的配置重开（这与新建一个工作同源，不是本提交新增的延期入口）；调低到已消费之下时，工作会以 `tokens` 在第一次调用前停止并说明原因。
3. **上限只给已有的预占行补上，不给旧工作发明一个。** 计划第 9.3 节写明“没有预算信息不能填零”；C07 之前创建的工作没有预占行，也就没有可靠口径的已用量。这类工作恢复时保持 `tokens_limit=None`（即“这一维度不是停止条件”，由期限继续停止），而不是拿一个凭空算出的数字当上限。反过来，**有**预占行却没有确切发起者的记录（既没有 `initiator`、也没有可转换的 `requester`+来源）由重预占本身拒绝并给出原因：那说明这笔账没有可归属的主体，不能猜一个来记账。
4. **恢复要过“当前”授权，而不是“创建时”授权。** 计划 M04 的撤销条款是“撤销阻止未来入场、后续敏感操作和发布”，而恢复恰恰是“未来入场”。因此 resume 用工作创建时的**主体**去过**当下**的 grant：grant 仍在则放行，被停用/撤销/过期或场景失效则拒绝并说明。判断的主体来自工作自身已存的发起者，不来自这次控制提案，所以控制者不能借恢复把自己的权限套到别人的工作上。人类主体的工作不走这支——它们的许可由既有的白名单与 `chat_allowed` 决定，恢复不新增也没减少那条老路。
5. **修订不重开额度，也不因此被拒。** 修订同样是“下一次执行”，同样需要一份活的预占，所以它与恢复走同一分支；但修订改的是目标与约束，不改变工作的累计上限，这一点由第 2 条保证。把 revise 也纳入这一支是刻意的：只处理 resume 会留下“修订后的工作没有预占行、于是没有 token 维度”的缺口，而这恰好是计划第 8.3 节要避免的那种半成品。

### 实际改动

- `cognition/call_store.py`
  - `account_used_tokens_in_transaction()` 增加可选 `exclude_job_id`，供重预占时把工作自己排除在外。
  - 新增 `rehold_work_in_transaction()`：只作用于 `settled`/`released` 的行，把 `reserved_tokens` 改回工作的累计上限、状态改回 `held`、清空 `settled_at`；`held` 行原样返回（控制可以落在工作仍在运行时，此时没有需要重开的账户）；先按 `exclude_job_id` 复核账号日与场景日额度，不足则拒绝并给出可读文本。
- `runtime/job_store.py`
  - 新增 `rehold_job_budget_in_transaction(job_id, initiator, scene_id)`：解析策略（仍走 `CapabilityGrant.resource_policy` 的名称解析）后调用上面的方法；没有 typed initiator 时直接拒绝。
  - 新增 `JobStoreMixin.initiator_of(job)`：读出 `_decode_job` 已经转换好的发起者，控制路径不再自己重建一份。
  - `apply_job_proposals_in_transaction()` 的 revise/resume 分支（`proposal.operation in {'resume','revise'}`）在有既存预占行时重预占，并把理由写进注释；cancel 分支不变。
- `runtime/gate.py`
  - `_capability_refusal()` 增加 `on_behalf_of` 参数，允许用“工作创建时的主体”过一次当前 grant。
  - `evaluate_and_commit()` 的授权循环改为按操作分支：`create`（非人类来源）照旧；`resume` 读出工作的发起者，非人类时用当前 grant 复核；其余操作明确 `continue`，不做未声明的扩权。

### 未做的事

- **没有让修订重置 `elapsed_seconds`。** 计划 D05 写的是“恢复不重置”，修订在计划里同样是同一工作的新版本（`revision+1`、`result_json` 清空），累计执行时间保留。若运营者需要更多时间，按计划是显式修订策略，不是自动延期。
- **没有给“额度不足”的恢复提供绕行入口。** 重预占失败即在路径失败中结束工作并保留原因；没有缩小目标、没有改模型、没有从别的账号借额度。
- **没有改 `job_resume_issue()` 的既有判定。** 那一层仍然决定“这个工作当前是否可继续”（次数/期限/绑定/插件可用性），本提交只在真正要开始执行时补上额度与授权的复核；两层职责不重叠。
- **没有新增表、列或配置字段。** 重预占只更新既有 `usage_reservations` 行的三个字段，无迁移。
- **没有新增、修改或运行测试、夹具或断言式探针。**

### 静态核对

- `git diff --check`（退出码 0）
- `uv run --no-dev python -m compileall -q src/len_bot`（退出码 0）
- `uv run --no-dev python -c "ConfigStore.load()"`：实际根配置仍能加载，`resources.policies == {}`、`job 24 48 600.0`，未改根配置
- 本地对象级核对（非运行服务，临时库 `/tmp`，核对后删除）：
  - 结算后再恢复：预占行从 `settled 1500` 变回 `held 1929216`，`day_key` 仍是原来那一天（`2023-11-14`）；恢复后的计数为 `revision 2 / model_steps 6 / tool_calls 5 / elapsed_seconds 38`，均未清零。
  - 修订走同一分支：`held 1929216`，任务回到 `pending`，累计消费仍计在同一工作账上。
  - 账号总额只记一次：该账号当天的占用为两个工作各 1,929,216（`3858432`），没有因为重预占把同一个工作算两次。
  - 无预占行的旧工作被拒绝并给出原文：`This work has no typed initiator; continuing it would have no account to hold against`。
  - 授权复核（对象级，用假配置源注入 grant）：grant 存在时恢复无拒绝；`enabled=false` 与“grant 不存在”两种情况都以 `Capability check refused at step 当前 grant: 当前配置没有向该主体授予 public_research；未配置的能力保持关闭` 拒绝。
  - 旧执行写新修订：`save_job_compression(..., revision=0, ...)` 与对已取消工作的 `revision=1` 均被 `JobChanged: Compression belongs to obsolete work` 拒绝（既有规则，本次核对确认没有被改动破坏）。
- 未运行测试、模型、OneBot、容器、浏览器或真实群；未启动服务。

### 未确认项

- **D06 的“授权撤销仍即时限制新操作”只在对象级核对。** 上述 grant 判定是在真实 `RuntimeGate._capability_refusal()` 与真实 `CapabilityAuthority` 上调用得到的，但没有跑一次真实“先建工作、再撤 grant、再恢复”的端到端流程，也没有经过面板接口。
- **重预占的额度不足路径未在真实规模上触发。** 核对用的是默认策略（单工作 10M、账号日 30M），没有构造真实账号日额度被打满的场景，因此那条中文拒绝文本只在代码中审阅，未在运行时打印。
- **重预占的上限跟着当前配置走。** 两次执行之间运营者若调低 `job_max_steps`（进而调低推导出的单工作额度），继续执行的工作会按调低后的上限重开。这与“新建工作用当前配置”一致，但确实意味着**没有**“恢复一律沿用创建时的额度数值”这条更强的性质；计划写的是“恢复保留已用额度、不重新赠送 10M”，本提交满足该条（上限不放大、已消费不退还），未实现的是把数值也冻结在创建那一刻。
- **计划里的 `usage_reservations` 重开没有独立的“再次预占”时刻。** 本提交把它做成控制事务内的一步（与工作状态变更同生共死），因此面板上的“预占”只反映控制提交后的状态；控制提交前的一瞬间仍是 `settled`。这与“预占是工作执行的前提”一致，但与“预占=创建时那一次”的朴素读法不同，记录在此。
- **真实并发未运行。** “控制提交与正在执行重叠”（工作仍在 `processing` 时收到修订）只会走到 `held` 分支，没有现场证据。

### 涉及持久字段

- 没有新增表或列。`usage_reservations` 的 `reserved_tokens`/`status`/`settled_at`/`policy_name` 是既有字段，本提交让 resume/revise 复用它们而不是新开一张表。
- 没有新增配置字段，根配置与样例均未改动。
- 无离线转换：C07 之前的工作没有 `usage_reservations` 行，按上面第 3 条保持“没有 token 维度”而不是补一个数字。

---

## C10 归属明确的 worker 协议与执行日志

对应计划第 8.2 节的 C10 `feat(execution): define owned worker protocol and journal`：`execution` 协议/客户端、`execution_runs`、最小 Gateway 服务；完成条件是**幂等接收**、**状态查询**、**取消**与**事件续读**可用，且**技术状态不冒充业务完成**。

计划 M06 把执行位置从 LenBot 进程内搬到独立服务，但明确要求“对外工具不变”：`run_python`、`list_workspace_files`、`read_workspace_file`、`export_workspace_artifact` 仍由原工作 Agent 调用。因此这一条**不把 Python 执行切到 Gateway**（那是 C11），它只做两件事：把“一个执行”的对外合同与归属固定下来，并把它的生命周期记进 `execution_runs`。计划第 8.3 节把“添加 Gateway 客户端后继续在失败时回落宿主 `docker run`”列为禁止的半成品，本提交按此处理：**在 C11 完成切换之前，宿主路径保持原样、Gateway 路径没有被接进 `run_python`**，两条路径不会同时可用。

### 计划要求 / 本提交做法

| 计划要求 | 本提交做法 |
|---|---|
| 请求合同只接受宿主组装的字段 | `execution/protocol.py:ExecutionRequest` 只含 execution_id、job/revision、scene、workspace_id、typed `initiator`、worker_type、script、**已登记** `input_assets`、`image_ref`/`network_policy` 引用与 `deadline_seconds`；没有 owner、mount、宿主目录、Docker 参数或镜像名字段，模型能填的只有脚本与业务参数。 |
| 先持久化再启动 | `POST /v1/executions` 先在网关自己的日志里写入这一行（`state=accepted`）再创建容器任务；写入失败即返回失败，不会出现“跑了但查不到”。 |
| 同 ID 不重复启动 | `record_execution()` 以 execution_id 为主键；重复提交返回**已存行**并标 `accepted=false`，不建第二个容器。同一 ID 内容不同（脚本、归属、引用、input_assets、initiator 任一不同）以 409 拒绝，而不是悄悄按新内容跑。 |
| 重试不延长期限 | `deadline_at` 在首次接受时算好并落库；重复提交不重算，因此重试不能把绝对期限推后。 |
| 状态机 | `accepted → starting → running → exited/failed/cancel_requested → termination_confirmed/termination_unconfirmed`；转换表在 `execution/journal.py:ALLOWED_TRANSITIONS`，非法转换由日志层拒绝（事件与状态同事务，拒绝时两者都不写）。 |
| 技术状态不冒充业务完成 | `ExecutionRecord` 里只有技术事实（状态、返回码、终止、stdout/stderr 与截断标记）；没有任何 completed/partial/failed 业务字段。`exited` 只表示进程退出，工作是否完成仍读 `agent_jobs`/`tasks`。 |
| 取消可确认或未确认 | `cancel_requested` 只是“收到请求”；`_terminate()` 依次 kill → 回收 client → rm -f → inspect，只有 inspect 确认消失/停止才写 `confirmed_absent`/`confirmed_stopped`，否则 `unconfirmed` 并记原因。 |
| 事件续读不重复 | 每个事件带自增 `sequence`，`GET .../events?after=` 只返回更大的序号，重复读取同一窗口不重复采用。 |
| 服务间认证 | `Authorization: Bearer <token>`（常量时间比较），401 不透露哪部分不对；认证只说明“来自 LenBot”，归属与状态仍按 job/execution 核对，网关不接受自己生成系统授权。 |
| 租约与修订代次 | 每行记 `job_id` + `job_revision`；`executions_for_job()` 可按工作读回全部代次，旧修订的执行与当前修订各自有行、各自有终态。 |
| Gateway 按本地期限收尾 | 期限在**接受时**落库；`_watch()` 与周期性 `sweep()` 都按这个存储值停止，因此 LenBot 不在也能停。 |
| 重启只核对、不重放 | `sweep()` 只读旧记录：超期就停；无法接续监视的执行写成 `termination_unconfirmed` 并说明需运营者核对，**不重跑脚本、不自动接管容器**。 |
| Bot 容器没有 Docker socket | 本提交没有把任何容器运行能力放进 LenBot 进程：Gateway 是独立服务与独立配置，Docker 控制只存在于它内部。 |

### 设计判断

1. **请求类型本身就是权限边界。** `ExecutionRequest` 里没有任何“选镜像/选挂载/选用户”的字段，镜像与网络策略是**引用名**，只在网关自己的部署配置里解析（`GatewayConfig.worker_for`/`policy_for`）。这样“模型写了代码”与“模型决定了在哪跑”被结构性地分开，而不是靠一层校验去拦。引用不存在时**拒绝**而不是回退成默认：一个部署没有建的镜像或出口，不能被读成“运行时没说要，于是随便用一个”。
2. **执行身份是幂等键，因此“重试”不是“再跑一次”。** 客户端超时后最危险的动作是提交一份新的执行：那会变成第二个容器、第二份副作用。所以同一 ID 重复提交返回**原来那行**，期限也不重算；内容不同则明确 409，让调用者去查询而不是覆盖。这是计划验收里“客户端超时后能查询同一执行、重复提交不会创建第二容器”的实现方式。
3. **`cancel_requested` 与终止结果是两件事，分开记录。** 计划写“请求取消不是已停止”。因此取消先写 `cancel_requested`（这是一条真实事件），再由 `_terminate()` 实际执行并写 `termination_*`；无法确认时状态是 `termination_unconfirmed` 而不是“大概停了”。这条与 C01 在宿主 worker 里建立的口径一致，但记录位置从进程内停车区换成了持久日志——跨进程、跨重启都读得到。
4. **网关有自己的日志，因为“控制服务不在”正是它要覆盖的场景。** 若执行记录只存在 LenBot 的库里，那么 LenBot 停机时没有任何组件知道“还有一个容器在跑、它还有个原定期限”。所以网关的日志是它自己的 SQLite 文件（`GatewayConfig.database_path`），表结构与状态机复用 `len_bot.execution.journal` 同一份定义——一个合同、两个进程，而不是两份会各自漂移的实现。
5. **`input_assets` 非空即失败，而不是“先跑起来再说”。** 输入字节的传输路径属于 C12；本版若把一个带输入资料的执行真的启动，脚本会读到一个不存在的文件，那看起来像“任务失败”而不是“这个功能还没有”。所以接受时明确记录 `inputs_unsupported` 并落到 `failed`，让缺失的能力显示为缺失。同理，`worker_type` 与引用所声明的类型必须一致（`py313` 实现的是 `python`），否则请求 `browser` 却拿到 `py313` 就是一次未声明的换实现。
6. **不自动接管上一次运行的容器。** 网关重启后，一个“已登记但本进程没有监视任务”的执行无法确认其容器的真实状态；`sweep()` 把它记为 `termination_unconfirmed`。这与计划“unconfirmed 保留目录并阻止复用”一致，也避免把“我看不到它”写成“它已经停了”。

### 实际改动

- `execution/protocol.py`（新增）：`ExecutionState`、`TERMINAL_STATES`、`TerminationReport`、`ExecutionEvent`、`ExecutionRecord`、`ExecutionRequest`、`is_terminal()`；引用名模式 `WORKER_TYPE_PATTERN`；输出上限常量 `MAX_OUTPUT_CHARS`（线上上限，不属于部署自己的输出限制）。
- `execution/journal.py`（新增）：`ExecutionJournalMixin` + `ALLOWED_TRANSITIONS` + `ExecutionIdentityConflict`。`initialize_executions()` 建 `execution_runs`（一行一执行，含工作代次、固定引用、状态、期限、终止、stdout/stderr 与截断标记、`last_sequence`）与 `execution_events`（`PRIMARY KEY(execution_id,sequence)`）；`record_execution()`、`get_execution()`、`execution_request_of()`、`executions_for_job()`、`append_execution_event()`、`read_execution_events()`、`execution_view()`。事件插入与状态更新在同一 `BEGIN IMMEDIATE` 内，拒绝即回滚。
- `execution/client.py`（新增）：`WorkerGatewayConfig`（base_url/token/两个引用/超时）、`WorkerGatewayClient`（submit/get/cancel/events/artifacts/artifact_bytes），并把两类失败分开：`GatewayRefused`（网关看到了并拒绝，什么都没启动）与 `GatewayUnavailable`（没得到答复，结局未知，只能查询同一执行 ID，不能新建）。
- `events/store.py`：`EventStore` 混入 `ExecutionJournalMixin`，`initialize()` 调用 `initialize_executions()`。这是 LenBot 侧的执行关联；网关侧用同一个 mixin，但库是它自己的文件。
- `services/worker_gateway/`（新增，部署为独立服务）：`config.py`（镜像/网络策略的引用注册表与 `worker_for`/`policy_for`）、`store.py`（网关自己的日志，复用上面同一份表结构与状态机；另加 `execution_artifacts` 登记清单）、`runner.py`（容器命令组装、启动确认、期限监视、取消与终止确认、重启核对、产物登记）、`app.py`（六条窄接口与 Bearer 认证）、`__main__.py`（`python -m len_bot.services.worker_gateway --config <网关配置文件>`）。

### 未做的事

- **没有把 `run_python` 切到 Gateway，也没有在任何地方保留宿主 `docker run` 回落。** `execution/workspace.py` 与 workspace 插件本提交未改动，宿主路径照旧；Gateway 客户端目前没有调用点。切换是 C11，且按计划第 8.3 节只能整体切换、不能并存。
- **没有做输入资料导入。** 见设计判断第 5 条：`input_assets` 非空即 `failed` 并说明原因，传输路径留给 C12。
- **没有做出网。** `NetworkPolicy.mode` 本版只允许 `none`；未知策略引用被拒绝，`network_python` 能力与出口网络属于 C13。
- **没有新增前端页面。** 面板仍显示既有内容；执行日志的呈现（工作详情里的 execution 行）不在本提交的验收条件里，也没有对应的构建产物改动。
- **没有新增能力授予或额度维度。** 执行本身不预占 token、不扣用户账；它只消耗计划 M06 说的 worker 容量（本版用 `max_concurrent` 显式拒绝而非排队）。
- **没有新增、修改或运行测试、夹具或断言式探针；没有真实容器、模型、OneBot 或群发送。**

### 静态核对

- `git diff --check`（退出码 0，无空白错误）
- `uv run --no-dev python -m compileall -q src/len_bot`（退出码 0）
- `uv run --no-dev python -c "import len_bot.services.worker_gateway.{app,config,runner,store}; import len_bot.execution.{protocol,client,journal}"`（导入成功；此前发现并修正了 `src/len_bot/services/worker_gateway/` 缺 `__init__.py` 导致整包不可导入的问题）
- 本机**没有可用的 Docker daemon**（`docker version` 报 `failed to connect to the docker API at unix:///Users/len5010/.docker/run/docker.sock`），因此**没有启动任何真实容器**，计划 A07/A08/A17 的相关项未取得运行证据。

### 未确认项

- **容器实际运行、期限停止与终止确认都没有现场证据。** 本机无 Docker daemon，无法核对“到点是否真的被 kill 并被 inspect 确认”。`_terminate()` 的判定逻辑与 C01 在宿主 worker 上已验证过的同构逻辑一致，但它在本服务里的实际行为未观察。
- **`max_concurrent` 的拒绝阈值未在真实并发下触发。** 该值取部署配置，默认 2；没有构造真实的并发占用场景。
- **重启核对（`sweep()`）只在代码层面成立。** 没有做“网关带一个在跑的容器被杀掉再拉起”的实际演练，因此“重新拉起后旧执行是否真的读到 `termination_unconfirmed`”未确认。
- **`execution_runs` 已建表但当前没有生产写入者。** 宿主路径不写它（C11 才接），网关只有被部署并接受请求时才会写。也就是说这次提交在真实运行环境中不改变任何现有行为。
- **网关配置尚未进入根配置。** 计划第 9.1 节把 `plugins.workspace` 指向 Gateway 地址；本提交只用网关自己的配置文件（`--config`），根配置与样例均未改动，接线留给 C11。
- **面板没有暴露执行状态。** 运维者目前只能通过网关接口或 `execution_runs` 直接查看，工作页不显示 execution 行。

### 涉及持久字段

- 新增两张表，均在 `EventStore.initialize()` 中随既有结构一起建立（`CREATE TABLE IF NOT EXISTS`，无需停机转换、无列变更）：
  - `execution_runs`：`execution_id`(PK)、`scene_id`、`job_id`、`job_revision`、`workspace_id`、`worker_type`、`image_ref`、`network_policy`、`request_json`、`state`、`accepted_at`、`deadline_at`、`started_at`、`ended_at`、`returncode`、`error`、`termination_json`、`stdout`、`stderr`、`stdout_truncated`、`stderr_truncated`、`last_sequence`。
  - `execution_events`：`(execution_id, sequence)` 主键、`kind`、`at`、`detail`。
- 网关侧另有 `execution_artifacts`（产物登记清单：`artifact_id`(PK)、`execution_id`、`path`、`size_bytes`、`media_type`、`registered_at`），只存在于网关自己的库里。
- 没有新增配置字段；根配置与 `lenbot.config.example.json` 均未改动。
- 无离线转换：两张表都是新增空表，旧记录不读也不改写。

---

> 按计划第 10.3 节的固定模板记录。状态措辞只用“实现完成 / 部署就绪 / 实际链路通过”三种；本批全部为**实现完成**，没有取得任何真实链路证据。核对方式遵守计划第 10.1 节与 [`AGENTS.md`](../AGENTS.md)：只做阅读、正常编译与 `git diff --check`，未新增或运行测试、夹具、断言式探针或自动截图，未启动服务、容器、浏览器、Core、模型或 OneBot，未进行真实发送。

## Commit / Parent / 审阅 HEAD

| 提交 | 父提交 | 标题 |
|---|---|---|
| `99be439` | `61969fa` | `fix(execution): preserve cancellation and termination outcomes` |
| `98227ff` | `99be439` | `fix(calendar): deliver explicit source failure cards` |
| `3b60fbc` | `98227ff` | `fix(memory): page maintenance reads within request budget` |
| `7564df9` | `3b60fbc` | `feat(context): expose delegable capabilities and focused references` |
| `70c9720` | `7564df9` | `feat(chat): make participation topic- and addressee-aware` |

审阅 HEAD：`70c9720`（分支 `social-agent-m0-foundation`）。C00 契约与文档收口在此之前完成（`76ef637`、`01241ec`、`f328e64`、`4014f3d`、`61969fa`），本批不重复提交。

本批共 14 个文件、`+592 / −65` 行：

```text
docs/social-agent-implementation-log.md            | 247 +++++
src/len_bot/cognition/context.py                   |  25 ++-
src/len_bot/cognition/social_core.py               |   2 +
src/len_bot/execution/workspace.py                 | 124 ++++---
src/len_bot/memory/reflector.py                    |  36 ++-
src/len_bot/memory/store.py                        |  15 +-
src/len_bot/plugins/builtin/asoul_calendar/calendar.py |  24 +-
src/len_bot/plugins/builtin/asoul_calendar/plugin.py   |  37 ++-
src/len_bot/plugins/builtin/asoul_calendar/render.py   |  50 +++
src/len_bot/plugins/builtin/workspace/__init__.py  |   7 +-
src/len_bot/plugins/host.py                        |  37 ++-
src/len_bot/runtime/job_runner.py                  |  22 +-
src/len_bot/runtime/scene_policy.py                |  12 +
src/len_bot/tools/retrieval.py                     |  19 +-
```

## 涉及模块与新增持久字段

| 提交 | 涉及模块 | 新增持久字段或表 |
|---|---|---|
| C01 | `execution/workspace.py`、`plugins/host.py`、`tools/retrieval.py`、`runtime/job_runner.py`、`plugins/builtin/workspace/__init__.py` | 无。`TERMINATION_PARKS` 是进程内模块状态，不落库；终止身份仍写在既有 control 目录标记与既有工作 trace 的 `workspace_termination` 字段里 |
| C02 | `memory/reflector.py`、`memory/store.py` | 无。`query_memories` 增加 `offset` 形参，不改表结构、不加列、不加索引 |
| C03 | `plugins/builtin/asoul_calendar/{calendar,plugin,render}.py` | 无。新增异常类型与渲染类，状态卡复用既有卡片资源与既有提交路径 |
| C04 | `cognition/context.py` | 无。`input_status` 的 `wake` 字段只存在于本轮请求 JSON，不写入事件或场景状态 |
| C05 | `plugins/host.py`、`cognition/context.py`、`cognition/social_core.py`、`runtime/scene_policy.py` | 无。`delegable_purposes` 只存在于本轮 `runtime_facts`；未新增配置字段、未改根配置、未改前端 |

全批没有 `CREATE TABLE`、`ALTER TABLE`、新列或新表；没有改 `src/len_bot/config.py`、`config_store.py`；`src/len_bot/web/**` 无改动，因此没有前端构建产物需要随提交更新。

## 本提交实现了什么、没有实现什么

**C01 实现完成**：取消在宿主与核心工具入口都不再被降级为普通 `ToolResult`；workspace 工具外层期限严格大于内层执行期限并覆盖清理窗口；清理子进程有统一上限并被回收；终止身份在异常转换之外保留可读。**没有**改动 AgentLoop/Gate/ActionQueue 的异常语义，**没有**把执行边界（`--network none`、只读根、cap-drop）改成可配置，**没有**为清理新增持久表。

**C02 实现完成**：维护循环的 `query_memory` 支持模型给出的 `limit`、`kind` 与 `offset`，返回结构化分页结果与 `next_offset`；词面排序路径在同一完整排序上分页。**没有**改批次投影、覆盖游标、`begin_history_batch` 容量判断或 `history_batches` 记录，**没有**改认识写入路径。

**C03 实现完成**：日历来源失败与“查询成功但本日 0 条”分开；后者仍走原日程卡片，前者在同一个 handler 调用内渲染并提交明确“日程暂未取得”状态卡；工具侧返回 `source_unavailable` 而不是冒泡异常。**没有**增加备用源、**没有**在失败时改调模型、**没有**新增通用降级框架。

**C04 实现完成**：`input_status` 的每条待处理来源带上既有的注意力判定（`reasons`、`certain`）；系统提示说明 certain 与弱机会的差别、弱机会不是委托、沉默是正常结果，并补上不虚构现实经历的通用边界。**没有**增加前置分类器、第二次模型调用、话题图谱或情绪状态，**没有**改动 `attention.py` 的任何判定或关注续期规则。

**C05 实现完成**：能力事实对“已加载但当前角色无直接工具”的模块给出 `delegable_purposes` 与“属于长工作、用 `start_work` 委托”的说明；系统提示说明该说明不是已授予额度或权限、未出现的模块即当前不可用。**没有**向对话暴露 work 工具 schema，**没有**新增工具发现渠道或配置开关；`_delegable_hint` 由既有 `chat_allowed` 派生。

## 输入 → 状态/资料 → 外部操作 → 结果/回执

```text
C01  外部取消/超时 → 容器终止与清理（含终止身份）→ 工作循环记录 termination → 上抛取消，不再进入下一步模型
C02  维护请求 → 现有批次投影不变 → query_memory 分页读取认识账本 → 结构化页与 next_offset 作为工具资料
C03  精确日历命令 → 来源读取结果 → 来源失败时同一次 handler 内渲染状态卡 → 既有 submit_message 提交与回执
C04  群消息 → 既有注意力判定（未改）→ input_status 带上该判定 → respond 决定参与/沉默/委托 → 既有 Gate 与发送回执
C05  对话轮次装配 → 既有场景与插件准入 → runtime_facts 附可委托用途摘要 → 模型表述变化，无新增外部操作
```

以上是源码路径上的数据流，四段链路都**没有**真实运行证据：没有真实的容器取消、没有真实的大批量维护、没有真实的日历来源失败、没有真实的群聊参与对照。

## 已完成静态核对

| 命令 | 结果 |
|---|---|
| `git diff --check` | 退出码 0，无空白错误 |
| `git diff --check 61969fa..HEAD` | 退出码 0，无空白错误 |
| `uv run --no-dev python -m compileall -q src/len_bot` | 退出码 0，无语法错误 |

这三项只证明代码可编译、补丁无空白问题。按计划第 10.1 节，它们**不构成**功能完成或运行通过。

## 已完成正常业务观察

无。本批没有在获准环境中启动服务、读取真实群消息、调用模型、调用 OneBot 或真实发送；表中所列行为都没有被实际观察到。

## 失败原文与所属阶段

本批开发过程中没有留下运行失败原文，因为未运行。开发中被修正的三处错误属于编码阶段，已记录在各自章节：`job_runner.py` 字典字面量中的海象表达式改为两条语句；`retrieval.py` 误用 `ToolResult.failure(content=...)` 改为构造后赋值；`context.py` 一次误删 `facts['capabilities']` 已恢复。三者都不在运行阶段，也不改变上述任何结论。

## 未确认的部署/模型/平台条件

| 未确认项 | 影响 | 所属 |
|---|---|---|
| 真实外部取消时“不再继续下一步模型调用” | 计划 A08 | C01 |
| 目标机器上容器清理的实际耗时 | 计划 A08 | C01 |
| 真实大批量维护的续读与批次推进 | 计划 A04 | C02 |
| “来源失败不新增 LLM 调用”的调用账 | 计划 A05 | C03 |
| 真实群聊中弱机会与针对 Bot 的连续交流是否被区分 | 计划 A01 | C04 |
| 真实对话中模型是否据此改说“能不能用 Python/浏览器” | 计划 C05 验收项 | C05 |
| 人格字段仍可由运营者写成含现实经历的文本 | 计划 A01 | C04（提示级约束，不校验字段内容） |

未确认项一律按“未运行”处理，不得写成通过。计划第 10.2 节矩阵中与本批相关的 A01、A02（部分）、A04、A05、A08 均待真实业务核对；A03、A06、A07、A09—A18 属于后续提交。

## 配置或数据库转换步骤

无。本批不新增配置字段、不新增表或列，现有数据库中的历史事件、认识、批次投影与 offset、已发 action 与 message_id、工作模型绑定均不改写。不需要停机迁移、离线转换或重新备份流程；C05 的 `_delegable_hint` 与 C02 的 `offset` 都由既有配置与调用方参数驱动。

## 停用与回退边界

本批全部是代码改动，回退方式是把上述五个提交按反序 revert：没有新写入的事实需要保留，也没有已完成的外部动作需要补偿。C03 的例外在于状态卡一旦真实发出即消失在群聊里，代码回退不会撤回已发消息，也不需要撤回——它就是当时的真实结果。C01 的进程内停车区随进程结束自然清空，回退后旧路径的 `ToolResult.failure(..., 'workspace_cancelled')` 行为恢复。

## 下一提交依赖

计划第 8.2 节的下一段是 C06—C09（来源身份与能力授权、共享预算预占、跨循环资源预算、工作修订与预算归属）。它们依赖 C00 与彼此之间存在明确顺序（C06 → C07 → C08 → C09），**不依赖** C10—C29，也不依赖本批的人工运行证据；但计划同一处写明“代码编写可并行”与“能力放行必须具备”是两件事：没有真实运行证据时，后续提交可以继续编写，不得据此放行任何新权限。

开始 C06 之前需要用户确认的前置项仍然有效：计划第 2 章 D01—D12 推荐裁决是否采纳，以及计划第 13 章的部署信息（Linux VPS、OneBot 文件协议、B 站专用账号、音频转写、可选 Core）。这些前置项不阻塞 C06—C09 的代码工作，但 D04/D06/D09 的具体取值会直接决定 C06/C07 的字段与判定。

C06 已按第 2 章的推荐裁决落地，C07、C08、C09 也已完成（见上文各自章节），D04/D06/D09 仍未获用户逐项确认。

C10 已完成（见上一节）。它只固定“一个执行”的对外合同与归属，**没有**把 Python 执行切到 Gateway：`run_python` 仍走宿主路径，Gateway 客户端目前没有调用点。计划第 8.3 节禁止“新客户端与旧宿主路径并存”，因此 C11 必须是**一次整体切换**，而不是在 `run_python` 里加一条“失败就回落”的分支。

下一提交是 **C11 `feat(worker): move offline Python to the isolated gateway`**：依赖 C10，范围是 Gateway 的 Docker 后端、workspace 接线、镜像构建与网络/卷部署，完成条件是 LenBot 容器没有 socket、离线 Python 能处理资料、**只有一个正式后端且没有宿主 fallback**。计划第 11.2 节写明“缺少目标机器数据不阻塞 C00—C09 的代码和接口工作，但阻塞独立执行后端的正式放行”，因此 C11 的代码可以继续，但独立执行后端在拿到目标机信息前不得宣布放行。计划第 13 章的部署信息（Linux VPS、OneBot 文件协议、B 站专用账号、音频转写、可选 Core）仍待用户提供。

## 本批不宣称的能力

公共兴趣与跨群分享、心跳与睡眠、独立 Worker Gateway 与执行出网、独立浏览器与持久登录态、B 站账号读写、文件上传与额度、视频片段与转写、`proactive_chat`/`interest_share`/`send_file` 独立授权、GSUID Core 支持矩阵：全部仍是计划条款，不是当前能力，不得写入产品文档的已具备章节，也不得在面板显示为可用。
