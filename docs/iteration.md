# 当前任务

更新时间：2026-09-16。本轮提交：`d0971e6`（文档收敛）、`c57c13e`（加载与凭据）、`5146cfb`（取消与产物身份）、`4ce5fd3`（预算记录口径）、`a281144`（面板）。其前为 `141ec2e`（C11 运营说明收口），社会 Agent 计划基线 `7a4152d`。本页只记当前状态、本轮实际核对和未确认项，历史过程通过 Git 提交回查（见文末）。

## 本轮范围与授权

本轮按 `LenBot_P0文档收敛与修复_P1配置面板改进计划_141ec2e.md` 推进 P0-D 文档收敛、P0-C01—C09 正确性修复与 P1-U 面板改进。不改真实根配置（样例保持 worker 模式、`gateway: null`）、不启动生产、不运行容器、不新增或运行测试、不发送、不重置数据库。正式切换到 gateway 仍需运营者改根配置、部署网关服务与镜像并补离线证据。

## P0-C 行为变化（源码已接，仅编译与解释器核对）

- **C01 加载与后端入口**：`python_workspace` 与 `workspace` 共用 `WorkspacePluginConfig`，`python_workspace/config.py` 删除（旧 ID 原本会因缺 `call_timeout_seconds` 在加载时抛 `AttributeError`）。`execution_timeout_seconds`／`call_timeout_seconds` 只读取当前选中的后端，纯 gateway 配置不再解引用 `worker`。
- **C02 嵌套凭据**：新增 `plugins/credentials.py`，按 Schema 路径（任意深度、支持 `$ref`）扫描凭据字段；`query_service.plugins()` 返回的 `config` 在所有深度去掉凭据，另给 `config_set`（点路径 → 是否已配置）与 `secret_fields`。保存走 `merge_config`：省略／空串保持原值、非空替换、显式 `null` 清除；占位值不会作为真实密钥回写。
- **C03 取消与对账**：`run_python` 从提交前到轮询结束走同一条取消路径（`asyncio.shield` 保护取消调用再抛 `WorkspaceCancelled`）；`GatewayConflict` 不再重复提交，只轮询同一执行 ID；`GatewayRefused`／`GatewayUnavailable`／`GatewayResultUnknown` 分开处理；`_reconcile_workspace()` 对同一工作区的未终结行（含 `termination_unconfirmed`）给出 `available`／`occupied`／`unknown` 结论而非只记日志。宿主调用携带被准入时的 `job_revision`，跨修订不再续接。
- **C04 产物身份**：当前读取只取最新一次**已确认（终态）**执行的快照，没有记录时明确报错而不是返回空列表；历史产物必须显式带 `execution_id`，不再隐式回退到更早的执行；`artifact_chunks` 流式读取并用增量 UTF-8 解码按字符分页，文件名按 RFC 5987 编码。
- **C05／C08 预算与余额**：新增 `recorded_work_ceiling()` 区分“写出的无维度上限”和“没有可核对的上限记录”，后者按拒绝准入处理而不是当作无限；工作面板返回 `token_limit_state: recorded|unrecorded`。混策略账户不再显示由默认策略推导出的假余额，`policy_name`（曾是 grant id）改为 `grant_reference` + `policy_reference_names` 与 `mixed_scope_required` 状态。
- **C06 输入与收尾**：Gateway 输入文件先全部解码校验到暂存目录再 `os.replace` 就位，被拒绝的输入不留半成品；停止的执行会记录有限输出；权限准备改为核对实际组访问位，不满足时按部署错误结束本次执行而不是记成脚本失败。
- **C07 初次草稿**：`workspace` 的 `worker`／`gateway` 用 `x-lenbot-exclusive` 声明为互斥组，表单渲染成单选并在切换分支时清掉未选分支，初次配置不会再同时提交两个对象。
- **C09 单后端收口**：两条路径互斥、无运行时回落，切换是运营者改根配置的操作；本轮文档与面板按此描述。

## P1-U 面板改进

- 主导航按日常任务分组：概览／日常（群聊与播报、工作与文件、提醒与等待、插件、模型与额度）／资料／系统／排查。复用既有路由与深链接，诊断页面只是换组，没有被删除。
- 群详情默认两个主入口“消息”和“配置”；注意力、摘要覆盖、参与者、互动偏好、工作与发送记录移到同一页的诊断入口。设置里明确“已保存／全局启用／插件加载成功／本群已加入插件”是四件独立事实。
- 配置表单按 Schema 渲染，互斥组渲染为单选、隐藏未选分支；不含手写枚举或内部 ID。
- 凭据字段按 Schema 路径显示“已保存，留空保留／尚未配置”，不显示旧值。
- 额度页改为账户／预占中／已结算／准入余量／依据，并说明混策略时的范围要求。
- 授权表单改为“谁 → 在哪个场景 → 允许什么 → 有效期 → 使用哪项额度策略”：能力改成多选（`GET /api/settings/capabilities` 提供名称与是否实现，未实现的标注为尚未实现），有效期改用日期时间选择器、保存时换算绝对时间，授予 ID 与版本由服务端按内容递增、签发者取当前登录账号；保存前给影响摘要并说明撤销只阻止后续操作。
- 仍未做：U03 插件列表按“用途／配置／开放范围”分组与轻量搜索，U06 保存／生效／错误反馈的统一表达（含键盘与窄屏人工核对）。

## 实际核对与限制

- `uv run python -m py_compile` 通过本批改动文件；`WorkspacePluginConfig` 对示例配置、纯 gateway（`call_timeout_seconds` 90.0、执行期限 30.0）、空配置、同时给出两个后端四种输入的接受／拒绝已用解释器核对；凭据的替换／保持／清除与 `x-lenbot-exclusive` 的存在用解释器核对；`/api/settings/capabilities` 的返回用解释器直接调用核对。
- `src/len_bot/web/frontend` 执行 `npm run build` 成功（1.25s）；**未**打开浏览器面板，未取得任何像素或人工页面核对，导航分组、群页分区、授权表单与窄屏表现都只有静态代码结论。
- 未运行网关、容器或真实工作；未改真实根配置；未发消息；未重置数据库。
- 未确认：宿主 `execution_runs` 与网关日志在超时／重启后的对账、取消 park 回原工作、历史产物按 `execution_id` 读取、`mixed_scope_required` 账户在真实数据下的显示、Linux UID/卷映射与 chown 实效、凭据保持／清除在真实保存往返中的表现、授权表单保存后服务端版本递增与签发者写入的真实结果。
- 已知未修（记录在案，非本轮任务）：`plugins/net_policy.py` 解析-连接间隙（DNS rebinding 窗口）；面板端口如对外暴露仍建议反代加固；`.backups/` 内多份真实配置副本（含密钥）需人工清理；数据库无保留策略；`reset_conversation_data` 的清理表列表未含 usage_reservations、execution_runs、execution_events。
- `uv run pytest` 在 collection 阶段因 5 个测试文件引用已删除符号中断（0 用例执行）；`ci.yml` 不运行 pytest。按项目约束本批未新增或修改任何测试。

## P0-D 文档收敛结果

- 从工作树删除：`docs/archive/` 全部七份 Markdown（六份历史正文与目录说明），以及 `docs/product.md`、`docs/execution-boundaries.md`、`docs/gscore-adapter.md`。保留下来的当前文档只有本文、`architecture.md`、`operations.md`、`plugins.md`、`README.md` 与保护计划。
- 独有内容去向：用户可见语义进 `README.md`；权限、失败与发送语义、执行后端与浏览器边界进 `architecture.md`；部署、资源参数、未知执行处置与 Core 配置入口进 `operations.md`；`x-lenbot-exclusive` 与插件配置保存契约进 `plugins.md`；未来目标仍在保护计划。
- 保护文件 `LenBot_社会Agent_完整实施计划_7a4152d.md` 与 `docs/persona/diana/**` 未删改，只做了因删除文件引起的机械链接替换（改指固定提交 `141ec2e` 的历史文件链接）。计划正文、阶段编号、目标与裁决未变。
- 未达标的部分：非保护工程 Markdown（含本计划全文）当前约 165 KiB，高于计划里“收敛到不超过 100 KiB”的目标值；达标需要删掉本计划在工作树中的副本（其验收要求见该计划 D01/D04），本轮未做，故如实记录为未达成。已完成的一次收敛是：`docs/archive/` 七份文件（269,897 字节）与 `product.md`／`execution-boundaries.md`／`gscore-adapter.md` 三份（8,041 字节）从工作树消失，内容合并进现行文档。

## 仍未解决（跨轮记录）

- 系统发起的工作没有生产者：类型化 system/plugin 发起者只在源码路径上存在，没有 Scheduler／心跳接通，不能声称已有自主系统工作。
- 宿主 `execution_runs` 与 `execution_events` 没有生产消费者，面板也没有执行记录页；现场核对只能通过网关 API 或直接读日志库。
- 真实供应商计费无法用本地估算证明：估算不能保证真实 usage，需要先确认真实 prompt/completion 形状才能声明硬性计费上限。
- 本地估算与实际计费之间的差额处理（A19/A20）已在源码中按同一准入入口与幂等结算实现，但只有静态路径，没有真实调用核对。
- 运营可编辑的人格文本仍可能包含“真实经历”式内容，没有校验；该约束目前只在提示层。
- 旧 `FX01—FX13` 编号与本轮 P0-C 编号指向同一批代码路径，但两者没有写成对照表；原合同只存在于 Git 提交 `141ec2e` 的 `docs/archive/social-agent-c01-c10-fix.md`。

## 未完成（社会 Agent 计划 C12—C20 未开始）

C12 资料导入导出、C13 出口网络、C14 独立浏览器、C15 动作审查、C16 B 站研究原语、C17 公共兴趣、C18 心跳、C19 睡眠、C20 持久延期交付均未动工。C11 正式放行需要运营者在真实根配置改选 gateway、部署网关服务与镜像、补离线实机证据。

## 下一步

按依赖顺序实施 C12、C13、C15、C16、C17、C18、C19、C20（C14 视浏览器后端形态另定）。P1-U 剩余项为 U03 插件列表分组与 U06 统一保存／生效／错误反馈；面板改动都需要一次真实人工核对（浏览器打开、键盘路径、窄屏）。

## 历史回查入口

被删除的文档、旧 `iteration` 全文与旧运行手册都在 Git 历史中：`git show 141ec2e:docs/archive/<文件名>`。它们只说明当时的情况，不作为当前配置、授权或机器状态的依据。
