# ADR 索引

当前架构由 [ADR-0044：原生对话 Agent 与单一证据账本](0044-native-conversation-and-evidence-ledger.md) 及 [架构](../architecture.md) 定义。实现与上线状态以 [实施记录](../implementation.md) 为准，操作见 [运行手册](../operations.md)。

## 现行决策

- [ADR-0044：原生对话 Agent 与单一证据账本](0044-native-conversation-and-evidence-ledger.md)：自然原话与原图、窄原生提案、后台工作、事实 Session、单一认识账本和显式 conversation/work 路由。

## 已由 ADR-0044 替代的设计

下列旧文档保留当时的理由与约束，不能据其类型、字段、预算或路由说明实现当前功能。事件、权限、事务和真实送达等仍有效原则已迁入现行架构。

| 历史 ADR | 被替代的部分 |
|---|---|
| 0011、0015、0019、0024、0025、0028 | 多层记忆、语义槽、衰减/晋升、episode 摘要、工作世界补丁和旧反思返回结构 |
| 0012、0020、0035、0037 | 兴趣评分、能力档位、normal/deliberate/fallback 路由及旧上下文/抢占策略 |
| 0018、0032、0033、0034、0040、0042 | 持久社会世界、自我意愿、Retained Attention、人物/关系容器及大 JSON 输出契约 |
| 0041 | 最终草稿后的额外续接；当前终结后不再吸收输入，发送结果边界继续保留 |
| 0043 | 前台与后台的查询分工及旧工作模型循环；当前外部查询、解题和整理统一后台，复用原生 AgentLoop |

0022、0023、0031 中的日常回放、人工评分、报告导入和实发准入门槛早已退出生产。0007、0014、0027、0038 的有效原则已迁入架构，0039 草案先由 0043 替代；这些已移除文档的原文保留在 Git 历史。

## 历史文档

以下按编号保留，不表示其中整份实现仍有效；与 ADR-0044 冲突时采用 ADR-0044。

- [Materialized State Tables in SQLite with Transactional Dual-Write](0001-materialized-state-in-sqlite.md)
- [Step-Boundary Episode Steering with Runtime Gate Staleness Check](0002-step-boundary-episode-steering.md)
- [Two-Phase Proposal Commit with Dependency Cascading](0003-two-phase-proposal-commit.md)
- [Dedicated Worker Coroutine and Mailbox Router per Scene](0004-scene-actor-mailbox-topology.md)
- [Native SQLite FTS5 with Trigram Tokenizer for CJK Search](0005-sqlite-trigram-fts5-for-cjk.md)
- [Ambient Execution Scope Enforcement at the SQL Layer](0006-sql-level-execution-scope-enforcement.md)
- [Scenario Runner and Mock OneBot for Deterministic Offline Testing](0008-scenario-runner-for-offline-verification.md)
- [In-Process Hybrid Min-Heap Task Scheduler with SQLite Sync](0009-deterministic-task-scheduler.md)
- [Agentic History Retrieval via On-Demand SQLite Tools](0010-agentic-history-retrieval-tools.md)
- [Four-Tier Memory Hierarchy with Epistemic Evidence Gating](0011-four-tier-memory-and-evidence-gate.md)
- [Proactive Agency via Speaking Budget, Interest Scoring, and Model Escalation](0012-agency-initiative-and-model-routing.md)
- [Atomic All-or-Nothing Durable Proposal Commit](0013-atomic-proposal-commit-transaction.md)
- [Semantic Memory Hierarchy, Slot Superseding, and ExecutionScope Privacy Guard](0015-semantic-memory-hierarchy-slot-superseding-and-scope-guard.md)
- [Plugin Runtime Sandbox, Sensory Decoupling, and Action Interceptor Pipeline](0016-plugin-runtime-isolation-and-sensory-decoupling.md)
- [Control Plane Operational Cockpit, Safe Interventions, and Zero-Downtime Dynamic Configuration](0017-control-plane-operational-cockpit-and-dynamic-configuration.md)
- [ADR-0018: Condition-Bound Obligations & Ambient Retained Items](0018-condition-bound-obligations-and-ambient-items.md)
- [ADR-0019: Quiet-Window Reflection, Reflection Cursor & Typed Social Memory](0019-quiet-window-reflection-cursor-and-typed-social-memory.md)
- [ADR-0020: Provider Registry & Routing Metrics](0020-provider-registry-and-routing-metrics.md)
- [ADR-0021: Plugin Manifest, Lifecycle Health & First Real Plugins](0021-plugin-discovery-lifecycle-health-and-real-plugins.md)
- [ADR-0022: Control Plane Query Service, Trace & Replay Lab](0022-control-plane-query-service-trace-and-replay-lab.md)
- [ADR-0023: Shadow Mode](0023-shadow-mode.md)
- [ADR-0024: Strict Memory Scope and Safe Promotion](0024-strict-memory-scope-and-safe-promotion.md)
- [ADR-0025: Proposal Contract, Typed Social State, and Scoped OpenLoop Authority](0025-proposal-contract-and-social-state-authority.md)
- [ADR-0026: Interim Event Filtering, Staleness Gating, and Human Message Accounting](0026-interim-staleness-gate-and-event-filtering.md)
- [ADR-0028: Reflection Provider Wiring, Batch Cursor Ingestion, and Atomic Batch Commit](0028-reflection-wiring-batch-cursor-and-atomic-commit.md)
- [ADR-0029: Condition-Bound wake_match Dict Matching, Durable Task Claim, and Provenance Lineage](0029-condition-bound-wake-match-and-durable-task-claim.md)
- [ADR-0030: Plugin Lifecycle Hooks, Core Tool Namespace Reservation, and SSRF Network Policy](0030-plugin-lifecycle-tool-reservation-and-ssrf-guard.md)
- [ADR-0031: Monitored Keywords Hot Reload, Shadow Annotations Persistence, and OneBot Adapter Robustness](0031-control-plane-social-hot-reload-shadow-annotations-and-adapter-robustness.md)
- [ADR-0032: Persistent Group Agent Session and Scene-Level Bursts](0032-group-agent-session-and-scene-bursts.md)
- [ADR-0033: Social Cognition Core Shadow Contract](0033-social-cognition-core-shadow-contract.md)
- [ADR-0034: Social Core Production Path and Durable Ambient Wake](0034-social-core-production-and-durable-ambient-wake.md)
- [ADR-0035: Agentic Memory Retrieval in Social Core and Provider Fallback Routing](0035-agentic-memory-retrieval-and-provider-fallback.md)
- [ADR-0036: Runtime-Owned OneBot Link Modes and Action Transport](0036-onebot-link-modes-and-action-transport.md)
- [ADR-0037: Token-Budgeted Social Context and Direct Cognition Preemption](0037-context-budget-and-direct-cognition-preemption.md)
- [ADR-0040: Unified Social Cognition and Continuous Fulfilment](0040-unified-social-cognition.md)
- [ADR-0041: 有界群聊续接与明确发送结果](0041-bounded-chat-and-delivery.md)
- [ADR-0042: 可纠正的认识与有界工具续接](0042-revisable-understanding-and-natural-voice.md)
- [ADR-0043: Runtime 管理的信息型工作](0043-runtime-owned-information-work.md)
