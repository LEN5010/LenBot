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
| C04 `feat(chat): make participation topic- and addressee-aware` | C01—C03 | 未开始 | 未运行 |
| C05 `feat(context): expose delegable capabilities and focused references` | C04 | 未开始 | 未运行 |

C01、C02、C03 都只依赖 C00 且互不影响；本文件按完成顺序记录，编号只标识计划第 8.2 节的范围。

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
