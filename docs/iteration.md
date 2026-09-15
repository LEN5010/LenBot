# 当前任务：C11 离线 Python 切换到隔离 Gateway（源码已接）

更新时间：2026-09-16。本批相对 `35f2694`；计划比较基线 `7a4152d`。上一批（复审回归 + FX03/FX05/FX12/FX13 + Gateway 复审批 + 审计小修）已提交为 `35f2694`，其记录并入本文件历史；C01—C10 与 FX 合同见 [archive/social-agent-c01-c10-fix.md](archive/social-agent-c01-c10-fix.md)。

## 当前结论与授权范围

用户要求按计划推进 C11—C20 并逐段提交。本批完成 C11 源码；不改真实根配置（示例配置保持 worker 模式、`gateway: null`）、不启动生产、不运行容器、不新增或运行测试。正式切换仍需运营者在根配置改选 gateway 后端并补实机证据。

## C11 行为变化（源码已接，仅编译核对）

- `execution/protocol.py`：`ExecutionRequest` 新增 `input_files`（宿主导出的文本/base64 输入，名称无路径分隔、不重复；`input_assets` 只作来源登记）；`execution/journal.py` 把 `input_files` 纳入同 ID 重复提交的身份核对。
- `services/worker_gateway/runner.py`：登记前把输入落盘到执行控制区（`manifest.json` 保持容器内原路径 `/lenbot-control/manifest.json`，其余进 `/lenbot-control/input/`），合计字节受工作目录上限约束，坏 base64 在占用执行身份前拒绝；worker GID 权限同时覆盖输入文件；按资产 ID 拉取输入被明确拒绝（字节只能来自宿主）。
- `execution/client.py`：`WorkerGatewayConfig` 新增 `execution_timeout_seconds`、`poll_interval_seconds`。
- `execution/service.py`：新增 `GatewayWorkspaceService`——`run_python` 组装 `ExecutionRequest`（工作的类型化发起者、修订、工作区、期限取网关配置与工作剩余期限的较小值），先写宿主侧 `execution_runs` 行再发请求；超时/未知只轮询同一执行 ID，不重复提交；取消经网关执行并把终止回执 park 回原工作；网关终态回读镜像进宿主日志；启动前对本工作区做一次对账（网关从未见过的行记失败，已终结的补记）。文件列表/分页读取/导出/面板下载全部改走网关产物 API（最新执行的登记产物即目录终态），图片导出仍走原 `save_image` 登记链。
- `plugins/builtin/workspace/`：配置改为 `worker` 与 `gateway` 二选一（互斥校验），插件按配置选择后端，两者之间没有运行时回落；卸载时关闭网关客户端。`lenbot.config.example.json` 的 workspace 配置补 `"gateway": null`。
- 所属文档：`execution-boundaries.md`、`operations.md`、`product.md`、`architecture.md` 改为描述配置可选后端，不再把 Gateway 写成未接线。

实际核对：`py_compile` 通过；`WorkspacePluginConfig` 对示例配置、纯 gateway 配置、空配置三种输入的接受/拒绝已用解释器核对。未运行网关、容器或真实工作。未改真实根配置。

## 未完成（C12—C20 未开始）

C12 资料导入导出、C13 出口网络、C14 独立浏览器、C15 动作审查、C16 B 站研究原语、C17 公共兴趣、C18 心跳、C19 睡眠、C20 持久延期交付均未动工。上一批遗留未修事项（net_policy TOCTOU、`.backups/` 密钥副本、无保留策略）不变。`uv run pytest` 收集仍中断（5 个旧测试文件引用已删符号），按约束未触碰。

## 下一步

按依赖顺序实施 C12、C13、C15、C16、C17、C18、C19、C20（C14 视浏览器后端形态另定）。C11 正式放行需要：运营者在真实根配置把 workspace 后端改为 gateway、部署网关服务与镜像、离线实机证据（A22/A23）。


## 上一批行为变化（35f2694，源码已接，仅编译核对）

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
