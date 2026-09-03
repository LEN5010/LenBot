# Persistent Social Agent Bot Harness (`len_bot`)

> **长期运行、持续观察环境、拥有时间感和未完成事务、能够自主决定注意什么、何时思考、何时行动、何时保持沉默，并在长期互动中形成连续认识的社会化 Agent。**

---

## 📖 项目简介

本项目不是一个简单的“接入 LLM 的 QQ 机器人框架”，而是一个**以持续存在的持久化运行时（Persistent Runtime）为主体**的社会化 Agent 系统。

* **LLM 不是 Agent**：大语言模型仅是按需启动的短暂认知执行器（Pi Agent Core）。
* **LLM 永远只能产出提案（Proposal）**：模型不直接拥有真实世界的修改权与不可逆副作用，全部操作均须经由运行时门禁（`RuntimeGate`）审核提交。
* **单进程模块化单体**：基于 Python 3.13、`asyncio` 与 SQLite WAL 模式构建，零外部消息队列中间件依赖，保证极致的因果确定性与微秒级状态一致性。

```text
       QQ / OneBot v11                Scheduler / Plugins
              │                                │
              ▼                                ▼
       Event Adapters ──────────────────► Raw Events
                                               │
                                               ▼
                                         Event Store (SQLite WAL)
                                               │
                                               ▼
                                         Scene Reducer (Single-Writer)
                                               │
                                               ▼
                                       Stimulus Builder (Debounce & Burst)
                                               │
                                               ▼
                                        Attention Engine
                                    ┌──────────┼──────────┐
                                    ▼          ▼          ▼
                                  DROP      OBSERVE     TRACK
                                                          │
                                                Soft Scene Annotation
                                                          │
                                                         WAKE
                                                          │
                                                          ▼
                                                   Episode Manager
                                                          │
                                                          ▼
                                                  Context Assembler
                                                          │
                                                          ▼
                                                  Cognition Router (Normal <-> Deliberate)
                                                          │
                                                          ▼
                                                    Pi Agent Core (ReAct Loop + Steering)
                                                    ↙           ↓          ↘
                                              search_messages  read_context query_memory
                                                    \           │          /
                                                     ▼          ▼         ▼
                                                      Episode Outcome
                                                            │
                                                            ▼
                                                       Runtime Gate (Staleness / Two-Phase Commit)
                                                   ┌────────┼────────┐
                                                   ▼        ▼        ▼
                                                SILENCE   State    Action
                                                          Commit  Proposal
                                                                     │
                                                                     ▼
                                                                Action Queue (OneBot Adapter)
```

---

## 🚀 核心架构特性与第一阶段落地里程（V1-A ~ V1-E）

系统严格遵循《系统架构设计 v0.2》规范，现已全部完成第一阶段 5 个里程碑的交付，自动化测试全量通过（18/18 Passed）：

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
| [`0012`](docs/adr/0012-agency-initiative-and-model-routing.md) | **Agency, Initiative & Model Routing** | 发言预算动态抑制抗话痨，显式兴趣打分，ReAct 工具结果驱动动态升阶 |

更多设计理念与领域名词见 [`CONTEXT.md`](CONTEXT.md) 与面向开发代理的指导原则 [`AGENTS.md`](AGENTS.md)。

---

## 🛠️ 快速开始

### 1. 运行环境要求
* **Python**: 3.11+ (推荐 3.13+)
* **包管理器**: [uv](https://github.com/astral-sh/uv) (推荐) 或 pip

### 2. 安装与环境同步
```bash
# 激活/安装依赖环境
uv sync
```

### 3. 运行自动化测试套件
```bash
# 运行全部 19 项端到端架构与控制台 API 集成测试
uv run pytest -v
```

### 4. 启动轻量玻璃拟态 SaaS 管理控制台 (Web Dashboard)
运行时默认集成了轻量玻璃拟态 + Bento Grid 风格的 Web 管理后台：
* **访问入口**：`http://127.0.0.1:11307`
* **默认初始账密**：`admin` / `lenbot123`
* **支持功能**：
  * **Bento 全局概览**：实时 Uptime、事件总数、活跃场景、未决 Open Loops、记忆信念数。
  * **WebSocket 管理**：OneBot 反向连接状态监控、远端客户端 IP、延迟检测与强制断连。
  * **模型提供商配置**：Base URL、API Key（脱敏）、Normal 与 Deliberate 阶层模型切换、1-token 延迟测速 (Ping)。
  * **人格与社交参数**：Bot QQ 与称呼修改、系统人格 Prompt 实时保存、敏感关键词、发言冷却、基础发言预算与兴趣主题权重滑块。
  * **插件中心（预留框架）**：支持扩展感官插件启停切换与参数抽屉预览。
  * **安全设置**：PBKDF2-HMAC-SHA256 加盐密码安全修改。

### 5. 接入真实 QQ 机器人（OneBot v11）
本系统内置反向 WebSocket 服务端，兼容 Lagrange.Core、NapCat、LLOneBot 等主流 OneBot 实现：

1. **配置环境变量**（或在 `RuntimeConfig` 中指定）：
   ```bash
   export BOT_QQ=12345678
   export OPENAI_API_KEY="sk-..."
   export OPENAI_BASE_URL="https://api.deepseek.com/v1" # 或 OpenAI / Claude
   export ONEBOT_WS_HOST="0.0.0.0"
   export ONEBOT_WS_PORT="8080"
   ```
2. **OneBot 客户端配置**：
   在 Lagrange / NapCat 中配置反向 WebSocket 连接地址为：
   `ws://127.0.0.1:8080`
3. **启动持久运行时**：
   ```python
   import asyncio
   from len_bot.config import RuntimeConfig
   from len_bot.runtime.agent_runtime import AgentRuntime
   from len_bot.adapters.onebot import OneBotAdapter

   async def main():
       config = RuntimeConfig()
       runtime = AgentRuntime(config)
       adapter = OneBotAdapter(config, on_event=runtime.receive_event)
       runtime.action_queue.send_adapter = adapter.send_action

       await runtime.start()
       await adapter.start()
       print("Len Bot Persistent Runtime is actively observing the world...")
       await asyncio.Event().wait()

   if __name__ == "__main__":
       asyncio.run(main())
   ```

---

## 📜 核心架构金律（§110）

> **Persistent Runtime 是持续存在的 Agent；Event 是它经历到的世界变化；Scene State、Task 和 Open Loop 构成它当前的执行现实；Memory 是它形成的认识；Attention 决定什么值得思考；Pi 是按需启动的认知皮层；所有认知结果都只是 Proposal，只有 Runtime 才拥有最终状态和行动权。**
