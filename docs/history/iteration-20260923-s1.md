# 2026-09-23 S1 实施与复核记录

按批次保留源码核对与未确认项；当前状态见[路线入口](../plan/README.md)，当前交接见[当前任务](../iteration.md)。历史中的授权与未提交说明限定原批次。

## 事件诊断预览与下载

阶段提交：`dc919e8`。以下保留当批记录。

### 当前批次

- 分支 `feat/s0-product-contract`，基线 `13e8fdf`，开始时工作区干净。
- 用户授权持续推进可实施任务并阶段性本地提交，不授权推送、合并、部署或真实发送。前批提交构成实际进展，本轮沿现有查询补 S1-06 的事件诊断材料，不重建审计系统。
- 没有可用同版开发面板；不另起服务或用假数据代替验收。

### 实现与边界

原事件关联查询已有身份、状态和上限，但完整对象包含正文和动态材料。本批在 RuntimeQueryService 增加定向 event_diagnostics 投影，使用既有 relations，只选择事件、提交、轨迹、调用、工作、行动和工具资料的固定身份、状态、用量与回执计时字段。

新 GET 路由沿原 Cookie 登录，按 scene_id/event_id 定位，找不到返回 404，响应禁止缓存。没有正文、人格、参数、错误原文、请求快照、配置、媒体或凭据；仍有业务编号，不承诺匿名。生成不落库、不执行模型／工具／平台，不增加已读资格；多次查询的起止及原关联 limits/truncated 一并保存，不称原子快照或完整历史。

事件详情增加显式读取、预览与下载。新读取和切换对象清空旧结果；登录状态变化沿原客户端丢弃旧响应；过时异步响应不覆盖新对象。下载仅使用本次预览，不重新读取或外发。说明和操作方法已同步架构、运行手册。

### 实际核对、失败与未确认

- uv compileall 编译 query_service.py 与 routes/cockpit.py，退出 0；前端 npm run build 退出 0，491 个模块、1.72 秒，日志 /private/tmp/lenbot-s1-diagnostics-build.log。
- 源码阅读覆盖原登录依赖、同场景存在性查询、各类截断、SourceOutcome 真正字段及导出字段选择；git diff --check 无格式错误。
- 文档补丁初次使用不存在的 `### 接入与能力` 标题，返回 `Failed to find expected lines`；随后读取实际标题与段落重新定位，没有因此改变文档结构或业务源码。
- 实际 SQL、鉴权响应、文件下载、对象切换、截断及窄屏页面未人工验收；没有读取业务数据库或执行接口请求。本次没有运行测试、夹具、断言式探针、自动截图、回放、故障注入或压力任务，没有修改真实配置或启动服务。构建产物不入 Git。

## 来源处理与提交送达判读

阶段提交：`84c9d38`。以下保留本批实现与核对。

### 当前批次

- 分支 `feat/s0-product-contract`，本批起点 `dc919e8`，工作区当时干净；上批已提交事件诊断预览与下载。
- 持续推进授权包括阶段性本地提交，不包括推送、合并、部署、真实模型／平台调用或发送。当前阶段补 S1-03 的具体展示缺口，核对并保留 S1-05 已有实现。

### 交付与源码依据

| 边界 | 已核对的真实路径 | 本批处理 |
|---|---|---|
| 来源处理 | cognition/models.py 的 SourceOutcome；events/store.py 同事务保存 source_outcomes；scenes/reducer.py 按处理来源移除 pending_wakes | 保留五种来源状态，消息详情补原 unfinished；不因读过或整轮沉默改变本条结局 |
| 失败与拒绝 | agent_loop.py 保存终结参数失败、新输入冲突、预算异常等；agent_runtime.py 保存异常类型与阶段；GateDecision.record 保存 accepted/reason | 查询投影原 error_type、gate_accepted、gate_reason；页面按原类型显示，缺错误原文的 conversation_error 也保留问题入口 |
| 提交与发布 | scenes/actor.py 已有 checkpoint 返回 not_repeated；event_store 同事务提交；RuntimeGate.publish_committed 限原 pending 状态，失败保留持久提交 | 未改运行代码；补状态判读表和运营步骤，不重建事务或发布队列 |
| 发送与未知 | actions/delivery_store.py 尝试先落库；delivery_fact 无终态时返回 unknown；ActionQueue 先读事实且不自动重放；receipt_delivery_status 核对平台消息／文件 ID | 未改既有实现，不用事件名称冒充真实送达；保持模拟／Shadow 与线上分离 |

新增展示只说明本次候选或关联轨迹，不能覆盖先前已提交 checkpoint。工具错误统计从原 tool_outcomes 读取，属于整轨迹而非本条独占。解释和未完成项只展示已保存文本，不生成心理过程，不扫描错误正文猜分类。诊断下载继续不含错误原文，仅补原 Gate 接受状态。

### 核对结果与未确认

- uv compileall 编译本批 query_service.py，退出 0；前端 npm run build 退出 0，491 个模块、1.65 秒，日志 /private/tmp/lenbot-s1-outcomes-build.log。
- git diff --check 无格式错误。本批没有新增定位或编译失败，也没有运行业务故障观察。
- S1-03 本阶段展示源码完成；S1-05 现有实现已核对并保留，未为重复完成而改代码。两项记待复核，不记已验收。
- 没有可用同版面板，实际 Gate 拒绝、失败后继续、发布中断、发送未知及重复提交均待人工验收。未运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力任务；未读业务库、改真实配置或启动服务。阶段代码与文档一起本地提交，构建产物不入 Git。
