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
- **没有新增绝对 deadline 或累计 token 硬停。** 既有 `job_max_seconds` 仍是单次执行超时且恢复会重新计时（见 [`readiness`](social-agent-readiness.md) 第 25 行），计划 C08/C09 处理。
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

---

# 首批（C00—C05）验收记录

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

C06 已按第 2 章的推荐裁决落地（见上一节），D04/D06/D09 仍未获用户逐项确认。下一提交是 **C07 `feat(budget): reserve and settle shared usage atomically`**：依赖 C06，范围是 `AgentBudget`、`CallStore`/`JobStore`、新增 `usage_reservations` 与模型页面，完成条件是 user+scene 并发预占一致、usage 与估算可区分、子调用归同一账。C07 的创建期预占需要在 `events/store.py:commit_proposal_transaction` 的同一写事务内完成，`CapabilityGrant.resource_policy` 的解析也应在该提交内落到真实策略。D06（每日额度只在创建时检查可能超发）与本提交直接相关，取值需要在实现前明确。

## 本批不宣称的能力

公共兴趣与跨群分享、心跳与睡眠、独立 Worker Gateway 与执行出网、独立浏览器与持久登录态、B 站账号读写、文件上传与额度、视频片段与转写、`proactive_chat`/`interest_share`/`send_file` 独立授权、GSUID Core 支持矩阵：全部仍是计划条款，不是当前能力，不得写入产品文档的已具备章节，也不得在面板显示为可用。
