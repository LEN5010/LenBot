# ADR-0020: Provider Registry & Routing Metrics

## Context

V2 计划 Phase 5(Cognition & Provider Routing)要求闭合 Goal 10。现状:

- 单 BaseURL + API Key + 两个 model name 硬编码在 `RuntimeConfig`,无法表达多 provider;
- 升级/切换模型需要改全局 config,无热更新与持久化的 provider 概念;
- 每次认知调用的 tier/provider/model/latency/tokens/success/error 全部被丢弃(`resp.usage` 从未读取),无法回答"模型健康吗""升级率多少";
- `[COMPLEXITY: HIGH]` 升级标记是字符串约定但代码库中没有任何生产者;
- `CognitionRouter.get_model_for_tier` 直接读 config,与"Provider + Model"路由模型不符。

## Decision

### 1. Provider Registry(`cognition/providers.py`)

- `ProviderConfig(id, api_style="openai", base_url, api_key, enabled, timeout_seconds)`;第一版只支持 OpenAI-compatible(计划 §十七)。
- `RoutingConfig(normal/deliberate → RouteTarget(provider_id, model))`。
- `ProviderRegistry`:唯一权威;`apply_update()` 热替换(校验重复 id 与未知路由目标;连接指纹变化时丢弃缓存的 AsyncOpenAI client);`resolve(tier)` 按 tier 返回 `RouteResolution(provider_id, model, client)`;client 懒创建并缓存。
- 持久化:dynamic config `provider_config`(含 api_key——SQLite 是 secret store);API 层只写不回读,GET 返回掩码提示。
- **一次性迁移**:启动时若无 `provider_config` 且存在旧 `model_config`,迁移为默认 provider 后持久化;旧键永久停读(数据转换,非兼容层)。

### 2. Routing Metrics(`runtime/metrics.py`)

`RuntimeMetrics`(内存,重启归零——持久化状态仍由 events 拥有):

- per `(tier, provider_id, model)`:calls / errors / prompt_tokens / completion_tokens / latency deque(maxlen=200),快照计算 p50/p95;
- per provider 错误计数;escalation 计数与原因(最近 N 条);
- Social counters(§三十二):human_messages、observe/track/wake、wake_silence、gate_action、visible_messages、would_send(Phase 8)、cancellations_honored;`visible_speech_ratio = visible / human`。

### 3. PiAgentCore 重构

- 构造注入 registry + metrics(直接构造时从 RuntimeConfig 种子化,供测试);
- 每步 `resolve(tier)`——tier 切换与热更新在下一步即生效;
- 每次调用记录延迟与 `resp.usage`;失败记入 provider 错误并上抛(既有异常边界转为 SILENCE);
- `should_escalate` 返回 `Optional[str]` 升级原因,记入 metrics。

### 4. Escalation 信号真实化

保留启发式(>1200 字符 / "争议" / step≥3);`RetrievalToolkit` 在证据集较大(≥8 条)时追加 `[COMPLEXITY: HIGH]` 行,标记从约定变为真实生产信号。

### 5. API(`web/routes/models.py` 重写)

`GET/POST /api/models/providers`(upsert,omit api_key 保留旧值)、`GET/POST /api/models/routing`、`POST /api/models/test`(按 provider 测)、`GET /api/models/metrics`。旧 `/api/models/config` 语义删除。Persona continuity(§十八)不新增机制:升级只换 model 名,同一 working_messages 轨迹与 system prompt 天然跨层共享(Scenario K 断言)。

## Consequences

- 正向:多 provider 可配置、热更新、可观测;Goal 10 的"模型 tier 是认知容量而非人格"有了数据与结构保证。
- 取舍(接受):metrics 重启归零(Overview 指标是运行期健康视图,不是审计账本);api_key 明文存 SQLite(单实例本地部署,计划 §三十九 只要求"API Key 永不通过 API 返回明文")。
- 中立:`get_model_for_tier` 删除,模型选择权单一化到 registry。
