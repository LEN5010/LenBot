# 当前任务

更新于 2026-09-24。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 `feat/s0-product-contract`，基线 `4fefe9d`；上批通用群报告字体样例已归[历史记录](history/iteration-20260924-s0.md)，未使用真实字体或装载插件。
- S0-03：兴趣分享候选由插件以注册的 `Candidate` 发出，Actor 接入时又按描述符解析；出站准备却在内核再次导入同一业务插件模型。只移除这处反向类型依赖，直接使用已保存候选的两个身份字段，原当前兴趣回读、来源核对、Gate 与发送事务不变。

## 本批交付与核对

- `AgentRuntime.prepare_outbound_action` 不再 import `plugins.builtin.interest_share.config.Candidate` 或再次 `model_validate`；从原 `source_event_id` 读取已存事件的 `data.interest_id`／`revision`，调用原 `publication_for` 复核当前兴趣、修订和匿名来源。事件发出端 `PluginContext.emit_event` 要求注册模型实例，Actor 的 `PluginHost.match_event` 再按描述符严格解析后才提交；出站不从未经入口解析的外部协议猜字段。来源缺失仍在原边界拒绝，`validate_outbound_action` 与发送尝试事务不变。
- 同步[架构中的逐群表达](architecture.md#公共兴趣的逐群表达)及路线 S0-03 证据。只收窄内核对插件模型的直接依赖，不增公共接口、候选类型、配置、表或重试；其他 `interest_share` 业务 ID 分支仍在，不能称为已完成全量分层。
- `uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/runtime/agent_runtime.py` 退出 0；`git diff --check` 退出 0。仅证明语法与差异格式，未运行插件事件、出站或真实送达。
- 未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；未读取真实根配置／业务库、启动服务、调用模型／平台或实发。没有本批实际业务失败原文。

## 待决定与接续

1. S0-03 其余兴趣分享和站点业务硬编码仍按当前源码存在；是否做更大的插件归属迁移需逐项业务依据，不能从这一处类型 import 的删除推断通用发行已完整拆分。兴趣分享到发送的同版正常观察仍待获准现场。
2. S2 仍为 `source_window_only`，固定材料版本、跨轮原生交换和压缩交接未完成；回复片段持久边界待维护者答复。首个 Linux／SnowLuma 报告与文件、S1／S3／S4 同版人工核对仍待现场；数据期限、许可证／素材授权、公开承诺、S6 候选与 S7 外部闭环亦未完成。
3. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
