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
| C02 `fix(memory): page maintenance reads within request budget` | C00 | 未开始 | 未运行 |
| C03 `fix(calendar): deliver explicit source failure cards` | C00 | 未开始 | 未运行 |
| C04 `feat(chat): make participation topic- and addressee-aware` | C01—C03 | 未开始 | 未运行 |
| C05 `feat(context): expose delegable capabilities and focused references` | C04 | 未开始 | 未运行 |

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
