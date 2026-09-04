# Persistent Social Agent Bot Harness (`len_bot`)

> **长期运行、持续观察环境、拥有时间感和未完成事务、能够自主决定注意什么、何时思考、何时行动、何时保持沉默，并在长期互动中形成连续认识的社会化 Agent。**

---

## 📖 项目简介

本项目不是一个简单的“接入 LLM 的 QQ 机器人框架”，而是一个**以持续存在的持久化运行时（Persistent Runtime）为主体**的社会化 Agent 系统。

* **LLM 不是 Agent**：大语言模型仅是按需启动的短暂认知执行器（Social Cognition Core）。
* **LLM 永远只能产出提案（Proposal）**：模型不直接拥有真实世界的修改权与不可逆副作用，全部操作均须经由运行时门禁（`RuntimeGate`）审核提交。
* **单进程模块化单体**：基于 Python 3.13、`asyncio` 与 SQLite WAL 模式构建，零外部消息队列中间件依赖，保证极致的因果确定性与微秒级状态一致性。

```text
OneBot / Plugin / Scheduler Event
                │
                ▼
         Event Store (SQLite WAL)
                │
                ▼
       SceneActor (single writer)
                │
        ┌───────┴────────┐
        ▼                ▼
 BurstAssembler   GroupAgentSession
        └───────┬────────┘
                ▼
      Social Cognition Core
         ┌──────┼──────┐
         ▼      ▼      ▼
      SILENCE  SPEAK  TOOL → Worker → Social Core
                │
                ▼
           RuntimeGate
                │
                ▼
           ActionQueue
```

---

## V4 迁移状态

- Stage 1 已完成：`StimulusBuilder` 已由按 scene 保序、无语义判断的 `BurstAssembler` 替代；`GroupAgentSession` 与 Event、`SceneState` 在同一事务提交并支持重启恢复。
- Stage 2 已完成：严格结构化的 `SocialCognitionResult` 已接入 Shadow；它可更新 session 与记录 intentional silence / would-speak trace，但没有发送、工具、调度或记忆提交 authority。
- Stage 3 已完成：生产路径已切换为 `Hard Event Gate → Social Cognition Core → RuntimeGate`；V3 Attention、Interest、SpeakingBudget 与 ParticipationThread 业务路径已删除。
- Stage 6 已完成：RetainedAttention 在合法 Session 提交时清理；NextWakeIntent 经 RuntimeGate 转换为可恢复的 durable task，并以 `TASK_DUE` 重新进入 Social Core。
- Agentic Retrieval 已完成（ADR-0035）：SocialCognitionCore 升级为有界 ReAct 工具循环，模型可按需调用历史/记忆检索工具（带强制收敛、工具错误回灌与确定性工具预算）；ProviderRegistry 支持主→备回退路由、模型目录管理与调用指标，控制台可在线勾选模型并指定普通/思考/回退三类用途。
- OneBot 连接已支持主动连接与等待接入两种模式（ADR-0036），消息发送可明确选择 WebSocket 或 HTTP；配置由控制台持久化，主动连接断开后自动重连。

---

## 🚀 Runtime Foundation 与历史里程（V1-A ~ V3）

以下能力构成已验证的 Runtime Foundation。Attention、Interest 与 ParticipationThread 条目仅描述 V1–V3 历史实现，相关生产路径已在 V4 Stage 3 删除。

### 1. V1-A：反应式核心（Reactive Core）
* **事件与刺激分离（`Event != Stimulus`）**：支持滑动空闲窗口（`Sliding Idle Window`）防抖聚合，识别 `@Bot` 与急迫词立即抢占 Flush。
* **三层注意力机制（Attention Engine）**：硬规则（Hard Attention）-> 状态依赖启发式（Heuristic）-> 能动性（Initiative），支持 `DROP / OBSERVE / TRACK / WAKE`。
* **单写者场景协程（Scene Actor）**：各群聊/私聊拥有独立单写者管道与版本号（`version++`），LLM 推理不占用 Scene 锁。
* **沉默是第一等公民（Silence as First-Class Citizen）**：Bot 绝不在闲聊中充当复读机，支持自主保持静默。

### 2. V1-B：持久化执行与时间感（Persistent Execution）
* **确定性任务调度器（Task Scheduler）**：基于优先级最小堆与 SQLite 同步，精准到期投递 `TASK_DUE` 物理事件。
* **未决事务管理（Open Loops）**：追踪跨消息社交期待，支持双轨 GC（绝对 TTL 到期 + 场景活跃度自然衰减）。
* **两阶段提案提交（Two-Phase Commit, ADR-0003）**：待回复的 Open Loop 仅在消息物理发送成功（`MESSAGE_SENT`）后激活入库。
* **步边界转向（Step-Boundary Steering）与时效门禁（Response Staleness Gate）**：用户途中改口（“算了不用查了”）时在 ReAct 步边界直接熔断。

### 3. V1-C：主动历史检索（Agentic History）
* **零向量数据库（No Vector DB）**：拒绝每条消息自动检索历史灌 Prompt，由 Agent 主动通过工具找回记忆。
* **SQLite 原生中文全文索引（Trigram FTS5）**：无需额外分词器，原生支持 CJK 中文任意子串毫秒级匹配。
* **SQL 层物理权限隔离（Ambient ExecutionScope, ADR-0006）**：查询强制绑定 `WHERE scene_id IN ({allowed_scopes})`，绝不依赖 Prompt 规则防越权。
* **完整检索工具箱**：提供 `search_messages`, `read_context`, `query_timeline`, `query_person_history`。

### 4. V1-D：四层记忆体系与认识沉淀（Epistemic Memory）
* **四层分级结构**：`L0 Raw Events`（不可篡改物理事实）-> `L1 Episodes`（经历切片）-> `L2 Semantic Beliefs`（认知信念）-> `L3 Reflection`（长周期反思）。
* **事实证据链强校验门禁（Memory Gate）**：记忆提案必须绑定真实存在的历史事件 ID（`evidence`），杜绝凭空捏造。
* **语义槽位冲突更迭（Conflict Resolution）**：主体偏好变更时，旧记录置为 `SUPERSEDED` 归档留痕，新记录激活，历史因果永不丢失。

### 5. V1-E：主动能动性与多阶认知（Agency & Model Routing）
* **发言预算抗话痨机制（Speaking Budget）**：根据近期发言冷却时间与群聊密集度动态抬高发言门槛；连续发言 2 次直接封顶 1.0 坚决不抢话。
* **显式领域兴趣评分（Interest Model）**：维护领域兴趣权重（电竞、直播、技术等），打分通过后方触发主动介入（`WAKE_FOR_INITIATIVE`）。
* **无损认知升阶（Dynamic Cognition Router）**：遇工具长文本（>1200 字符）或争议标签时，动态从 `Normal` 升阶至 `Deliberate` 深度推理模型，消息轨迹完整保留。

---

## 📑 架构决策记录（ADR）索引

所有重大设计决策均沉淀在 [`docs/adr/`](docs/adr/) 中：

| ADR 编号 | 核心决策主题 | 核心摘要 |
| :---: | :--- | :--- |
| [`0001`](docs/adr/0001-materialized-state-in-sqlite.md) | **Materialized State in SQLite** | 采用 SQLite WAL 模式，双写事件事实与物化状态表 |
| [`0002`](docs/adr/0002-step-boundary-episode-steering.md) | **Step-Boundary Episode Steering** | 在 ReAct 工具迭代边界检测转向信号，响应发送前做时效门禁核验 |
| [`0003`](docs/adr/0003-two-phase-proposal-commit.md) | **Two-Phase Proposal Commit** | 依赖回复的 Open Loop 需等待消息发送确认事件后方才落库激活 |
| [`0004`](docs/adr/0004-scene-actor-mailbox-topology.md) | **Scene Actor Mailbox Topology** | 每个场景一个轻量级异步工作协程串行递增版本号，推理过程不阻塞场景 |
| [`0005`](docs/adr/0005-sqlite-trigram-fts5-for-cjk.md) | **SQLite Trigram FTS5 for CJK** | 零外部依赖，使用 SQLite 3 自带的 trigram tokenizer 支持中文子串检索 |
| [`0006`](docs/adr/0006-sql-level-execution-scope-enforcement.md) | **SQL-level Execution Scope** | 权限作用域在 SQL 拼接处强制限制，杜绝模型跨场景越权泄密 |
| [`0007`](docs/adr/0007-reactive-core-v1a-scope-and-stubs.md) | **Reactive Core Scope & Stubs** | 确立 V1-A 反应式闭环边界，为后续模块保留纯净接口 |
| [`0008`](docs/adr/0008-scenario-runner-for-offline-verification.md) | **Scenario Runner for Offline Tests** | 离线确定性测试框架，无需网络即可完整复现多角色时间演进剧情 |
| [`0009`](docs/adr/0009-deterministic-task-scheduler.md) | **Deterministic Task Scheduler** | 基于最小堆与数据库巡检的无模型定时引擎，杜绝意图虚构 |
| [`0010`](docs/adr/0010-agentic-history-retrieval-tools.md) | **Agentic History Retrieval Tools** | 拒绝全量注入与向量 RAG，由 Agent 自主按需调起检索工具 |
| [`0011`](docs/adr/0011-four-tier-memory-and-evidence-gate.md) | **Four-Tier Memory & Evidence Gate** | 建立四层记忆体系与强证据链检验，冲突采用 superseded 软淘汰 |
| [`0012`](docs/adr/0012-agency-initiative-and-model-routing.md) | **Agency, Initiative & Model Routing** | 发言预算动态抑制抗话痨，显式兴趣打分，ReAct 工具结果驱动动态升阶（预算/兴趣模型已随 V4 Stage 3 删除，升阶路由保留） |
| [`0013`](docs/adr/0013-atomic-proposal-commit-transaction.md) | **Atomic Proposal Commit Transaction** | 全部持久化变更在单一 SQLite 事务内提交或整体回滚，外部副作用仅在提交成功后执行 |
| [`0014`](docs/adr/0014-social-behavior-participation-threads-and-interim-context.md) | **Social Behavior & Participation Threads** | 已废弃 — ParticipationThread 机制由 GroupAgentSession + Social Core 取代（ADR-0032/0033），EpisodeMailbox 时效机制保留 |
| [`0015`](docs/adr/0015-semantic-memory-hierarchy-slot-superseding-and-scope-guard.md) | **Semantic Memory & Scope Guard** | 槽位 superseding 软淘汰与 SQL 层隐私边界（visibility 列后被 ADR-0024 移除，scope 为唯一边界） |
| [`0016`](docs/adr/0016-plugin-runtime-isolation-and-sensory-decoupling.md) | **Plugin Runtime Isolation** | 插件沙箱、工具超时保护、异常拦截与出站动作拦截器 |
| [`0017`](docs/adr/0017-control-plane-operational-cockpit-and-dynamic-configuration.md) | **Operational Cockpit & Dynamic Config** | /api/cockpit 安全干预、动态配置热更新与 SQLite 持久化 |
| [`0018`](docs/adr/0018-condition-bound-obligations-and-ambient-items.md) | **Condition-Bound Obligations & Ambient Items** | wake_event_type 条件绑定义务、LIVE_* 插件事实直达 Social Core（AmbientStore 后由 retained_attention 取代） |
| [`0019`](docs/adr/0019-quiet-window-reflection-cursor-and-typed-social-memory.md) | **Quiet-Window Reflection & Typed Memory** | 反思游标、MemoryKind 规范枚举、证据范围校验与维护心跳衰减 |
| [`0020`](docs/adr/0020-provider-registry-and-routing-metrics.md) | **Provider Registry & Routing Metrics** | 多提供商 tier 路由、热更新、一次性迁移与全量调用指标 |
| [`0021`](docs/adr/0021-plugin-discovery-lifecycle-health-and-real-plugins.md) | **Plugin Discovery, Lifecycle & Real Plugins** | 内建插件注册表、健康追踪与插件状态持久化 |
| [`0022`](docs/adr/0022-control-plane-query-service-trace-and-replay-lab.md) | **Query Service, Trace & Replay Lab** | RuntimeQueryService 唯一读门面、traces 决策链路与确定性回放 |
| [`0023`](docs/adr/0023-shadow-mode.md) | **Shadow Mode** | 零社交事实的“只记录不发送”灰度验证与 would-send 记录 |
| [`0024`](docs/adr/0024-strict-memory-scope-and-safe-promotion.md) | **Strict Memory Scope & Safe Promotion** | visibility 移除、scope 唯一边界、global-safe 安全晋升与 decision_reason |
| [`0025`](docs/adr/0025-proposal-contract-and-social-state-authority.md) | **Proposal Contract & Social State Authority** | GateDecision.accepted、记忆写入统一与开环解析的原子范围保证（类型化社交状态契约后被移除） |
| [`0026`](docs/adr/0026-interim-staleness-gate-and-event-filtering.md) | **Interim Staleness Gate & Event Filtering** | EpisodeMailbox 事件过滤、RuntimeGate 时效拒绝与人类消息计数 |
| [`0027`](docs/adr/0027-participation-lifecycle-and-attention-continuation.md) | **Participation Lifecycle & Attention Continuation** | 已废弃 — 由 Social Core 在 GroupAgentSession 状态内判断（ADR-0032/0033） |
| [`0028`](docs/adr/0028-reflection-wiring-batch-cursor-and-atomic-commit.md) | **Reflection Wiring, Batch Cursor & Atomic Commit** | LLMReflector 生产接线、无跳过批量游标与 commit_reflection_batch 原子提交 |
| [`0029`](docs/adr/0029-condition-bound-wake-match-and-durable-task-claim.md) | **Condition-Bound Wake Match & Durable Task Claim** | wake_match 精确匹配、exactly-once 任务认领与 promote_task 晋升 API |
| [`0030`](docs/adr/0030-plugin-lifecycle-tool-reservation-and-ssrf-guard.md) | **Plugin Lifecycle, Tool Reservation & SSRF Guard** | RESERVED_CORE_TOOLS 防工具遮蔽、生命周期钩子与集中式 SSRF 网络策略 |
| [`0031`](docs/adr/0031-control-plane-social-hot-reload-shadow-annotations-and-adapter-robustness.md) | **Shadow Annotations & OneBot Hardening** | 影子标注持久化与准确率统计、OneBot 自环丢弃、引用回复与有界重连退避 |
| [`0032`](docs/adr/0032-group-agent-session-and-scene-bursts.md) | **Group Agent Session & Scene Bursts** | 持久化每个 scene 的工作社会状态，以纯时间 burst 保留多人对话顺序 |
| [`0033`](docs/adr/0033-social-cognition-core-shadow-contract.md) | **Social Cognition Shadow Contract** | 严格结构化认知结果、intentional silence 与无副作用 Shadow 提交路径 |
| [`0034`](docs/adr/0034-social-core-production-and-durable-ambient-wake.md) | **Social Core Production & Ambient Wake** | 唯一 Social Core 生产路径与 Runtime 所有的 durable NextWake |
| [`0035`](docs/adr/0035-agentic-memory-retrieval-and-provider-fallback.md) | **Agentic Memory Retrieval & Provider Fallback** | Social Core 有界 ReAct 工具循环、强制收敛、工具错误回灌与单跳回退路由 |
| [`0036`](docs/adr/0036-onebot-link-modes-and-action-transport.md) | **OneBot Link Modes & Action Transport** | 单事件链路双向模式、显式选择发送通道、禁止跨通道重试 |
| [`0037`](docs/adr/0037-context-budget-and-direct-cognition-preemption.md) | **Token-Budgeted Context & Direct Cognition Preemption** | 200K 令牌预算的模型侧投影滚动与在途推理的确定性抢占 |

更多设计理念与领域名词见 [`CONTEXT.md`](CONTEXT.md) 与面向开发代理的指导原则 [`AGENTS.md`](AGENTS.md)。

---

## 🛠️ 快速开始

### 1. 运行环境要求
* **Python**: 3.13（`.python-version` 固定；CI 同版本运行）
* **包管理器**: [uv](https://github.com/astral-sh/uv) (推荐) 或 pip

### 2. 安装与环境同步
```bash
# 激活/安装依赖环境
uv sync
```

### 3. 运行自动化测试套件
```bash
# 运行完整架构、边界与场景测试
uv run pytest -v
```

### 4. 启动轻量玻璃拟态 SaaS 管理控制台 (Web Dashboard)
运行时默认集成了轻量玻璃拟态 + Bento Grid 风格的 Web 管理后台：
* **访问入口**：`http://127.0.0.1:11307`
* **默认初始账密**：`admin` / `lenbot123`
* **支持功能**：
  * **Bento 全局概览**：实时 Uptime、事件总数、活跃场景、未决 Open Loops、记忆信念数。
  * **场景 / Trace / 事件日志**：场景列表、社交决策链路追踪（burst → Social Core → Gate → 持久效果 → 动作，含检索工具调用计数）、事件查询与运行日志。
  * **WebSocket / OneBot 管理**：反向接入与主动连接两种模式、发送通道（WebSocket / HTTP）选择、连接状态监控、远端客户端 IP、延迟检测与强制断连。
  * **模型提供商配置**：多提供商 Base URL、API Key（脱敏）、`/v1/models` 模型目录在线拉取、Normal / Deliberate / 回退三类用途路由、连接测试。
  * **人格设置**：Bot QQ 与称呼修改、系统人格 Prompt 与对话风格实时保存。
  * **插件中心**：内建插件（B 站直播传感器、B 站内容工具、网页搜索工具）启停切换与参数抽屉预览。
  * **Shadow 模式与人工标注**：只记录不发送的影子运行，TP/FP/TN/FN 人工评价与准确率统计。
  * **安全设置**：PBKDF2-HMAC-SHA256 加盐密码安全修改。

### 5. 接入真实 QQ 机器人（OneBot v11）
本系统内置 OneBot v11 链路，兼容 Lagrange.Core、NapCat、LLOneBot 等主流实现；支持反向接入（本地开 WebSocket 服务端等客户端连入）与主动连接（运行时连向客户端）两种模式（ADR-0036）。

1. **配置**：代码只读取以下三个环境变量（也可改用 `RuntimeConfig` 同名字段），其余配置项均由控制台持久化管理：
   ```bash
   export OPENAI_API_KEY="sk-..."
   export OPENAI_BASE_URL="https://api.deepseek.com/v1" # 或 OpenAI / Claude
   export ONEBOT_ACCESS_TOKEN="..."                     # 可选；控制台填写后只写不回显
   ```
   注意：`OPENAI_*` 仅作为首次启动迁移到 `provider_config` 的种子，之后请在控制台“模型”页管理提供商。OneBot 连接模式（`onebot_connection_mode`）、监听地址与端口（`ws_host` / `ws_port`）、发送通道（`onebot_action_transport`）等均可通过 `RuntimeConfig` 字段或控制台设置；Bot QQ 会由 OneBot `self_id` 自动校正。
2. **OneBot 客户端配置**（默认反向接入模式）：
   在 Lagrange / NapCat 中配置反向 WebSocket 连接地址为：
   `ws://127.0.0.1:8080`
3. **启动持久运行时**（推荐 `uv run len-bot`，等价于以下装配，见 `src/len_bot/__init__.py`）：
   ```python
   import asyncio
   from len_bot.config import RuntimeConfig
   from len_bot.runtime.agent_runtime import AgentRuntime
   from len_bot.adapters.onebot import OneBotAdapter

   async def main():
       config = RuntimeConfig()
       runtime = AgentRuntime(config)
       adapter = OneBotAdapter(
           config,
           on_event=runtime.receive_event,
           on_self_id=runtime.update_bot_identity,  # 由 OneBot self_id 校正 Bot QQ
       )
       runtime.action_queue.send_adapter = adapter.send_action
       runtime._onebot_adapter = adapter  # 控制台 WebSocket 管理接口依赖该引用

       await runtime.start()
       await adapter.start()
       print("Len Bot Persistent Runtime is actively observing the world...")
       await asyncio.Event().wait()

   if __name__ == "__main__":
       asyncio.run(main())
   ```

---

## 📜 核心架构金律（§110）

> **Persistent Runtime 是持续存在的 Agent；Event 是它经历到的世界变化；Group Agent Session 承载每个群的社会连续性；Social Core 理解场景并提出 SILENCE / SPEAK / TOOL；所有认知结果都只是 Proposal，只有 Runtime 才拥有最终状态和行动权。**
