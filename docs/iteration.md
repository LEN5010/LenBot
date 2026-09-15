# 当前任务：复审回归与 FX03/FX05/FX12/FX13、Gateway 复审批修复

更新时间：2026-09-16。本批相对 `c590855`；计划比较基线 `7a4152d`。本文件只维护最新状态；C01—C10 与 FX01—FX13 的原始合同随本批收口移入 [archive/social-agent-c01-c10-fix.md](archive/social-agent-c01-c10-fix.md)，同批归档早期计划、readiness 与实施日志。

## 当前结论与授权范围

用户要求按外部复审队列直接修复并提交本批。不改根配置、不启动生产、不真实发送、不把 Gateway 接到 `run_python`（C11 仍关闭）、不新增或运行测试。

## 本批行为变化（源码已接，仅编译核对）

预算与工作账户：

- `cognition/agent_loop.py`、`cognition/budget.py`：预算消息与 `_seconds_left` 在有 `deadline_at` 时按绝对期限计剩余秒；值为 `None` 的维度不参与运算；`local_state()` 透传 `deadline_at`。修复 `f4115e4` 引入的绝对期限工作 `None - float` 回归。
- `cognition/call_store.py`：待处理（`pending`）技能候选成为结算统一门槛，无模型调用但有候选的工作不再提前 `released`；已 `settled` 账户拒绝无日账重占（`ValueError`）；启动时把 `ended_at IS NULL` 的在途调用记为 `failed/process_restart`，并结算只等这些回执的 `settling` 账户。
- `runtime/job_store.py::complete_job`：统一走 `close_reservation_in_transaction` 读取已持久化候选，不再按单次入参决定保留 `settling`。
- FX05：`reservation_policy_for` 人类主体按 `long_work` 选授予与具名策略（系统主体保持 information→public_research 映射，未映射操作回退 `long_work`）；`rehold_job_budget_in_transaction` 接收 `work_operation` 并做授予并发准入，resume/revise 调用点透传原 `work_operation`。
- 维护入口（`skills/learning.py`）：候选处理前按执行期同一规则复核当前访问（插件可用、非人类主体当前 grant、人类请求者对话资格）；不通过则候选记 `failed` 并结算账户；来源工作取消/缺失的作废候选同样补一次结算，避免账户永久 `settling`。

对话等待（FX03）：

- `cognition/models.py::ConversationResume` 新增 `deadline_at`（窗口关闭的绝对 Unix 时刻）。`cognition/social_core.py` 挂起时写入、恢复时按该时刻恢复预算期限，等待经过的时间同样计入窗口；旧记录回退到累计秒。`conversation_window_seconds` 恢复改为显式透传：原窗口为 `null` 时不再被当前配置的窗口替换。

历史维护（FX13）：

- `memory/reflector.py`：同一轮多条 `query_memory` 的展示改到 `prepare_tool_results` 内整组装填——按调用顺序共享一份剩余输入余量，先到先装，装不下的逐条退化为不推进分页的明确拒绝页；trace 记录 `memory_page_fitting`。不再出现各调用按同一余量各自裁剪后合并超限。

面板（FX12）：

- `web/query_service.py::model_reservations`：账户全天只在同一具名策略（grant 引用）下运行时，日上限与余量按该策略数字显示并附 `policy_name`；混用或无具名策略回落默认策略并列出 `policy_names`。

Gateway（仍未接线，属 C11 切换前合同）：

- `execution/journal.py`：`termination_unconfirmed → termination_confirmed` 成为唯一放行转移，其余终态仍不可变。
- `services/worker_gateway/runner.py`：输出读取任务随进程一起启动（不再等启动确认，避免管道充满阻塞容器）；启动确认区分"容器暂未创建"与"客户端已退出且容器不存在"，未确认启动同样进入 `_watch`，期限、外部取消与工作区上限对其全部生效；产物在写终态之前复制并登记（先字节后登记；产物 ID 用 `uuid5(execution_id:path)` 固定，崩溃后可续），产物源文件按目录逐层 `O_NOFOLLOW` 打开；`_terminate` 各步骤共享同一份清理预算；`chmod/chown` 失败记入 `permissions_warning` 事件而非静默；`EXITED/FAILED` 记录不再被改写成终止态（只做客户端清理）；`cancel` 与重启 `sweep` 对 `termination_unconfirmed` 复核，确认容器不在后转 `termination_confirmed`，释放容量与工作区。
- `services/worker_gateway/store.py`：新增 `unconfirmed_terminations()`；`register_artifact` 接受调用方已存字节的产物 ID。
- `services/worker_gateway/app.py`：下载文件名按 RFC 5987 编码（非 ASCII 文件名不再抛 `UnicodeEncodeError`），响应构造失败时关闭描述符不泄漏。
- `execution/client.py`：`accepted`/`requested` 缺失或非布尔按 `GatewayResultUnknown` 处理，不再静默当 `False`。

其他（本轮审计项）：

- `events/store.py`、`services/worker_gateway/store.py`：SQLite `busy_timeout=5000`。
- `web/auth.py`、`web/routes/auth.py`：登录失败节流（同 IP+用户名 15 分钟内 5 次后 429）；PBKDF2 经 `asyncio.to_thread` 移出事件循环。

## 实际核对与限制

- 仅 `uv run python -m py_compile` 通过全部改动文件；未运行真实工作、未启动生产、未运行 Gateway 容器、未打开浏览器面板。
- `uv run pytest` 在 collection 阶段因 5 个测试文件引用已删除符号中断（0 用例执行）；`ci.yml` 不运行 pytest。按项目约束本批未新增或修改任何测试。
- 未确认：Linux UID/卷映射与 chown 实效、短执行与取消竞态、真实容器下的启动确认与终止复核、恢复窗口在等待中过期时的真实中断表现、混策略账户的面板展示、面板登录与授予保存。
- 已知未修（记录在案）：`plugins/net_policy.py` 解析-连接间隙（DNS rebinding 窗口）；面板端口如对外暴露仍建议反代加固；`.backups/` 内多份真实配置副本（含密钥）需人工清理；数据库无保留策略。

## 下一步

获准环境中做正常短时工作与面板核对（A08/A22/A23/A24）；C11 切换另需当次授权，并先补离线 Gateway 实机证据。
