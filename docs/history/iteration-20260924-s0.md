# 2026-09-24 通用配置样例的群报告字体

任务状态只见[产品路线](../plan/README.md)，当前交接见[当前任务](../iteration.md)。本页归档对应批次的样例与操作说明，不是字体授权或现场装载证据。

阶段提交：`4fefe9d`。

分支 `feat/s0-product-contract`，基线 `c95e221`。原通用样例的 `plugins.group_summary.config.render_font_path` 指向 `../asoul_calendar/resources/font.ttf`，运行手册也推荐该跨插件路径。首个 Linux 路径已要求运营者为主进程提供可读字体，因此本批只把样例改成 `/replace/with/approved/report-font.ttf` 占位，并同步[运行手册](../operations.md#日常查看与处置)、[Linux 部署前置](../../deploy/linux/README.md#首个验收方向的报告与文件)与路线 S0-03／S5-01 证据。通用样例中群报告仍默认停用；启用前须换成实际文件，不读取 `/replace`，也不把占位当授权或可运行配置。

插件现有 `on_load` 文件核对和报告渲染路径不变；未移动、复制或改授字体，未改真实根配置、插件代码或其他人工样例。原[依赖测绘](../plan/s0-03-dependency-boundary.md)与[授权底稿](../plan/s0-04-06-license-and-support.md)仍是其标明基线的静态记录，旧路径事实不被回写。

`git diff --check` 退出 0；未构建、装载或运行插件，未新增、修改或运行测试、夹具、断言式探针、截图、回放、故障注入、压力或覆盖率任务。未读真实业务库／根配置，未调用模型／平台或实发。没有本批业务失败原文。字体／模板素材使用和再分发、实际字体文件、目标双架构现场、SnowLuma 文件回执、数据期限、公开承诺及 S2 原生交换仍未完成；仅阶段性本地提交。

## 兴趣候选出站复用已解析身份

阶段提交：`ae97090`。分支同上，基线 `4fefe9d`。原 `AgentRuntime.prepare_outbound_action` 在出站准备时再次导入 `plugins.builtin.interest_share.config.Candidate` 并解析已保存的候选事件；该事件在插件发出时要求注册的模型实例，Actor 接入时 `PluginHost.match_event` 也按描述符严格解析。因此出站改为直接读取原事件的 `interest_id`／`revision`，调用原 `publication_for` 回读当前兴趣、修订和匿名来源。来源缺失仍在原边界拒绝；原插件归属、Gate、配额、发送事务与回执不变。

同步[逐群表达边界](../architecture.md#公共兴趣的逐群表达)与路线 S0-03 证据。只去掉一处内核到业务插件模型的反向 import，不加公共接口、类型、配置、表、重试或兼容路径；其他 `interest_share` 业务 ID 分支仍在。`uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/runtime/agent_runtime.py` 与 `git diff --check` 均退出 0，只说明语法和差异格式。未运行插件事件、出站、真实送达或任何被禁止的验证任务；无业务失败原文。同版运行与剩余边界仍待后续，不因静态核对标记验收。
