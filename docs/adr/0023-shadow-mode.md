# ADR-0023: Shadow Mode

## 当前状态

当前发送控制已简化（2026-09-06）：持久化实发群名单与全局 Shadow 决定是否实发，默认 Shadow 开启，初始名单为 group:126300994。无采样、打分或报告准入要求，源码、模型和人格变化不要求重新验收。Settings 保留发送配置，候选人工评测流程已删除；详细操作见 [运行手册](../operations.md)。下文是最初实现记录。

## Context

V2 计划 Phase 8(Shadow Testing,§三十四)。真实 QQ 群上线前需要一种模式:

> Runtime 正常 ingest / Attention / Cognition / 提案,但**不实际发送任何消息**,只记录"如果启用,它本来会发送什么"。

用于观察 false positive speaking、stale response、尴尬参与——这是 Social Agent 上线的关键阶段。代码库此前无任何支持(`shadow|would_send|dry_run` 零匹配)。

## Decision

1. **开关**:`AgentRuntime.shadow_mode`(内存布尔)+ `set_shadow_mode()` 持久化到 dynamic config `shadow_config`,重启恢复,控制面热切换。
2. **ActionQueue 注入点**:构造时接收 `shadow_probe`(每条 action 出队时询问)与 `shadow_recorder`。Shadow 开启时:
   - **安全拦截器照常执行**——内容安全流水线不受模式影响;
   - **跳过 `send_adapter`**——零物理发送;
   - **不发 MESSAGE_SENT / MESSAGE_SEND_FAILED**——即不产生任何社交事实:OpenLoop 不激活(两阶段提交的"发送确认"语义天然保守)、SceneState 不记录 Bot 发言、raw history 不被污染;
   - would-send 记录进内存 ring(`shadow_would_send_log`,500 条)+ `metrics.would_send` 计数。
3. **API**:`GET /api/cockpit/shadow`(开关状态 + would-send 列表)、`POST /api/cockpit/shadow/toggle`;Settings 页提供开关。

## Consequences

- 正向:Phase 8(真实群 Shadow 采集)只需开一个开关;与上线阶段(§三十五)的 Phase 2/3 渐进开放兼容——shadow 记录可直接对比"如果放开会怎样"。
- 取舍(接受):would-send 为内存记录,重启清零(Shadow 是观察期工具,不是审计账本;需要留存时 trace/metrics 可扩展);被 shadow 的 expect_reply 消息不会激活 OpenLoop,因此 shadow 期间的"对方可能已回复"语义由后续真实会话自然覆盖。
- 中立:Shadow 不改变 Gate/commit 语义——durable 内部状态(Task/Memory 提案)照常原子提交,只有"对外说话"被截断,这正符合 §三十四 "Runtime 正常运行,只是不实际 send"。
