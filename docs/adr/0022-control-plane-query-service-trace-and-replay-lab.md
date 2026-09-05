# ADR-0022: Control Plane Query Service, Trace & Replay Lab

## 当前状态

部分取代（2026-09-06）。QueryService、真实 trace、事件与运行日志继续使用；第 4 节的日常回放 API 和第 5 节的 Replay 页面已删除。ReplayLab 仅供内部确定性测试，当前认知路径和面板职责见 [当前架构](../architecture.md)。下文保留当时决策，不是当前操作要求。

## Context

V2 计划 Phase 7(Control Plane)与 §三十七 工程原则。核实的现状:

- 路由直接扒 runtime 内部:`runtime.memory_store._db.execute`、`runtime.scene_manager._actors`、`runtime.plugin_host._plugins`、`runtime.pi_core._client`;
- `cockpit.py` 的 loop resolve 用裸 SQL `UPDATE open_loops`,绕过 runtime authority(Invariant A);
- 没有任何 Trace 能力:episode_id 从不落库,attention 结果是单槽覆写(`_last_attention_result`),无法回答"为什么插嘴/为什么沉默";
- 无 Behavior Metrics(§三十二)、无 Events 过滤查询、无 Replay Lab;
- 前端是 vanilla 单页,从未消费 cockpit API。

## Decision

### 1. RuntimeQueryService(§三十七)

`web/query_service.py` 是唯一只读门面:overview / scenes / scene detail / events 过滤查询 / tasks / open loops / memories(含 evidence)与信念演化链 / traces / metrics / plugins / providers / shadow log。所有 routes 一律经由它,对 runtime 内部结构的直接访问从 web 层消失。它只封装读;一切写操作仍走既有权威路径(scheduler / OpenLoopManager / MemoryStore / receive_event)。**不变量修复**:loop resolve 改走 `OpenLoopManager.resolve_loop`(与 gate/GC 同一 authority),只迁移 active loop 并保留溯源字段(ADR-0018)。

### 2. Behavior Trace(§二十四/二十五)

`traces(id, kind, scene_id, ref_id, payload_json, created_at)`:

- **attention 行**:每次 Attention 评估(含 OBSERVE/TRACK/WAKE/DROP)写一行——disposition、reason、刺激摘要;
- **episode 行**:episode 结束写一行,ref_id=episode_id,载荷覆盖完整因果链:Stimulus → Attention(reason) → Cognition(mode、每步 tier/model/provider、tool 调用与结果预览、interim/follow-up 注入次数、escalations) → Outcome → Gate disposition/reason → durable effects(committed task/loop/memory ids) → actions_enqueued。

`PiAgentCore.execute_episode` 返回 `(outcome, step_trace)` 并由 `EpisodeManager` 上抛——trace 是数据,不是新的执行机制。Debug 工具"为什么插嘴"由此闭合。

### 3. Metrics / Events / Logs(§二十二/三十)

- `GET /api/models/metrics`(ADR-0020)+ overview 内嵌 social 指标:human_messages、observe/track/wake、visible_messages、**Visible Speech Ratio**、wake_silence、would_send、cancellations_honored;
- `GET /api/cockpit/events`:scene/actor/type/since/until 过滤;
- Logs 与 Events 严格分开:内存环形 `LogRingBuffer`(1000 条)挂 `len_bot` logger,`GET /api/logs` 按级别过滤。

### 4. Replay Lab(§三十一)

`testing/replay.py: ReplayLab`:录制事件窗口 → 纯 `SceneReducer` 物化状态 → 真实 `AttentionEngine` 逐条评估 → 输出每条消息 OBSERVE/TRACK/WAKE + mock cognition 的 SILENCE/ACTION。确定性:无 sleep、无网络、注入时间戳。`POST /api/replay` 接受多组 overrides(如 monitored_keywords、speaking_budget_base_threshold),每组跑一遍返回对照(Policy A vs B)。

### 5. 前端(Vue 3 + Vite)

`web/frontend/` 构建产物输出 `web/static/dist/`,FastAPI 挂载 + SPA fallback;dev 走 vite proxy。十个视图对应 §二十一 信息架构:Overview(Social Metrics)/ Scenes(+Detail 时间线)/ Trace / Tasks & OpenLoops / Memory Explorer(演化链 + 驳斥)/ Models & Routing(provider 卡 + 指标)/ Plugins(真实注册表 + schema 表单)/ Events & Logs / Replay Lab / Settings & Security(含 Shadow Mode 开关)。**旧 vanilla UI(index.html/app.js/style.css)删除**——不保留双 UI。

### 6. 安全(§三十九)

移除 `allow_origins=["*"]` + credentials 的危险组合:默认同源,额外 origins 由 create_app 参数显式传入。

## Consequences

- 正向:完成定义"能从一条消息看到 Event→Attention→Cognition→Gate→durable effects→visible action"达成;Dashboard 与 Runtime 解耦;策略调参有了确定性实验台。
- 取舍(接受):traces 表随事件量增长(有索引,可按 scene/kind 清理);Replay 的 per-message stimulus 粒度与线上 debounce 有差异(回放是策略分析工具,不是比特级仿真);metrics/log ring 重启归零(运行期视图)。
