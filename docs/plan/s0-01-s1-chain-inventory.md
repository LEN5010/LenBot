# S1 可追溯执行主链能力现状索引（只读源码核对）

- **核对 commit**：`ad41a5a71a4b5ec3693f4cc846342964c5b0b8c6`（分支 `feat/s0-product-contract`，基线业务代码 `9178c34`）。
- **核对时的实际 HEAD**：`43d093c`。`ad41a5a` 仍是 `43d093c` 的祖先；`git diff --name-only ad41a5a HEAD -- src` 输出为 0 个文件，即**自 `ad41a5a` 以来没有任何业务源码变更**，`HEAD` 上只多了 `docs/plan/` 与 `.github/` 的后续文档提交。本报告全部符号在 `ad41a5a` 上同样成立。
- **核对时间**：2026-09-21T14:43Z（容器 UTC；本机 `2026-09-21 23:43 JST`）。
- **核对方式**：只读源码。`git rev-parse/log/diff/status`、`grep`、`glob`、`read`。未运行测试／夹具／探针／回放／截图，未启动服务，未调用模型或任何外部平台，未打开 `len_bot.db` 或媒体目录内容。唯一写操作是本文件。
- **一句话结论**：S1 六项里，**身份链条（S1-01）与用量口径（S1-04）的底层记录大多已经存在但分散**——`model_calls`／`agent_jobs`／`events`／`traces` 四份账各自完整，`RuntimeQueryService.relations()` 甚至已能把它们串成一条链；而**请求材料清单（S1-02）与统一结局投影（S1-03）只有半个**：`request_manifest` 只记位置不记内容、`SourceOutcome.status` 只覆盖「已读来源」，**「业务沉默 vs 执行失败 vs 未完成」没有单一权威枚举**，需要分四个不同符号拼出来；提交／发布／送达（S1-05）状态枚举齐全且区分正确，是本链最扎实的一段。

---

## 一、判定表

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口／未确认 |
|---|---|---|---|---|
| S1-01 event 身份 | 已实现 | `src/len_bot/events/store.py:74` | `CREATE TABLE IF NOT EXISTS events (` ／ `id TEXT PRIMARY KEY,` | `id` 由生产者给（如 `turn:...`、`send-attempt:...`），无统一命名约束表。 |
| S1-01 episode 身份 | 部分实现 | `src/len_bot/runtime/agent_runtime.py:1119`；`src/len_bot/events/store.py:44` | `episode_id = resume.episode_id if resume else f"conversation:{uuid.uuid4().hex}"` ／ `... name IN ('group_agent_sessions','scene_states','episodes')` | **没有 episode 表**；`episode_id` 是每轮随机 UUID，只作字符串外键散落在 `model_calls.episode_id`、事件 `payload.episode_id`、`traces.ref_id`。旧 `episodes` 表被显式判为 retired 并拒绝启动。 |
| S1-01 call 身份 | 已实现 | `src/len_bot/cognition/call_store.py:94`；`:95` | `CREATE TABLE IF NOT EXISTS model_calls (` ／ `id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, episode_id TEXT, job_id TEXT, batch_id TEXT,` | `episode_id`／`job_id`／`batch_id` 可空且无 FK 约束；跨轮同一次工作的调用靠 `job_id` 关联。 |
| S1-01 job / revision 身份 | 已实现 | `src/len_bot/runtime/job_store.py:312`；`:313` | `CREATE TABLE IF NOT EXISTS agent_jobs (` ／ `id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, revision INTEGER NOT NULL,` | `revision` 是工作内单调版本，用于防「按旧版本交付」；`job_exchanges.goal_revision`（`:329`）绑定该修订。 |
| S1-01 action 身份 | 部分实现 | `src/len_bot/actions/models.py:79`；`src/len_bot/runtime/gate.py:338`；`:346` | `id: str = Field(default_factory=lambda: str(uuid.uuid4()))` ／ `action_ids = [str(uuid.uuid4()) for _ in outcome.message_proposals]` ／ `scene_commit['event'].payload['action_ids'] = action_ids` | **没有 actions 表**：action 身份只在提交事件 JSON（`action_ids`）与回执事件 `payload.action_id` 里。队列 `ActionQueue` 是纯内存（`actions/queue.py:23,29-32`），进程重启后未发送的排队行动无持久行。 |
| S1-01 receipt 身份 | 已实现 | `src/len_bot/actions/models.py:34`；`src/len_bot/actions/queue.py:80` | `def receipt_delivery_status(event_type, payload, metadata) -> str \| None:` ／ `return {"action_id": action.id, "raw_text": action.content, ...}` | 回执是 `events` 行（`MESSAGE_SENT`／`MESSAGE_SEND_FAILED`／`FILE_UPLOADED`／`FILE_UPLOAD_FAILED`／`ACTION_SHADOWED`），以 `payload.action_id` 反查行动；`EVENT_TYPE.DELIVERY_ATTEMPTED` 是「尝试」不是回执。 |
| S1-01 batch／turn 关联 | 已实现 | `src/len_bot/runtime/gate.py:555`；`src/len_bot/scenes/actor.py:294` | `batch_id=decision.commit_event_id.removeprefix('turn:')` ／ `event_id = f'turn:{item.mailbox.episode_id}:checkpoint:{item.outcome.checkpoint_index}'` | `batch_id` 是提交事件 id 去掉 `turn:`；同一次提交的多条行动共享 `batch_id` 并各有 `batch_index`。 |
| S1-01 一条链的可查询关联 | 已实现 | `src/len_bot/web/query_service.py:1268` | `async def relations(self, scene_id, *, event_id=None, job_id=None, episode_id=None, action_id=None, batch_id=None):` | 这是本项最强证据：它沿**存储身份**（不是时间邻近）从任一点扩散到 events／traces／calls／jobs／actions／tool_results／turns／operation_receipts。单类上限 50，`truncated` 标记截断。 |
| S1-01 `revision` 术语统一 | 未找到 | `src/len_bot/runtime/job_store.py:313`；`src/len_bot/memory/store.py:64`；`src/len_bot/events/store.py:103` | `revision INTEGER NOT NULL,` ／ `revision INTEGER NOT NULL,` ／ `scene_id TEXT PRIMARY KEY, version INTEGER NOT NULL,` | `revision` 至少有三种语义：工作修订、记忆修订、`scene_sessions.version`。**没有一份术语表**说明它们互不等价。任务卡 S1-01「给每个阶段定义真实含义」尚无可挂载的文档位置。 |
| S1-02 `answer_basis` 定义与校验 | 已实现 | `src/len_bot/cognition/models.py:104`；`:82`；`src/len_bot/events/store.py:1266` | `class AnswerBasis(BaseModel):` ／ `AnswerBasisKind = Literal['social','general','observed','work_result','mixed','unverified']` ／ `async def validate_answer_basis(self, basis, scene_id, *, through_rowid, read_event_ids, result_reads, bot_actor_id):` | 校验很硬：`event_ids` 必须是本场景截点内**真实人类**原话、`result_spans` 必须落在**实际提供给模型**的范围内（`:1300-1302`）、工作成果必须匹配当前修订（`:1306`）。 |
| S1-02 `answer_basis` 写点 | 已实现 | `src/len_bot/cognition/proposals.py:662`；`src/len_bot/scenes/actor.py:335`；`src/len_bot/actions/queue.py:90` | `basis=AnswerBasis(kind=declared.kind,` ／ `if message.answer_basis:` ／ `"answer_basis": action.answer_basis.model_dump(mode='json') if action.answer_basis else None,` | 三处落库：提交事件 `outcome.message_proposals[].answer_basis`、行动／回执 payload、`traces` 内。冗余但一致。 |
| S1-02 `request_manifest`（位置清单） | 部分实现 | `src/len_bot/cognition/context.py:1241` | `def request_manifest(self, messages):` ／ `"""Only locations and categories; original text stays in the event store."""` ／ `'event_id':message.get('_source_event_id'),'ref':message.get('_source_ref'),` | **只记 index／role／section／event_id／ref／text_range／omitted／tool_call_id**。能还原「哪条原文、哪段范围被提供」，**不能**还原系统提示正文、人物参考正文、媒体像素、工具定义 schema。 |
| S1-02 `context_plan` 装配记录 | 已实现 | `src/len_bot/cognition/context.py:266`；`src/len_bot/cognition/social_core.py:264` | `self.context_plan = {'omitted': []}` ／ `context.context_plan['request']={'input_tokens':tokens,'input_budget_tokens':context.input_budget,` | 每轮记 `section_tokens`、`tool_definition_tokens`、`current_pixel_assets`、`messages`（即 manifest）、`omitted` 列表。**这是当前最接近 request manifest 的权威结构。** |
| S1-02 `event_packing_detail` | 已实现 | `src/len_bot/cognition/context.py:865` | `profile=self.context_plan.setdefault('event_packing_detail', {'calls':0,'phases':{},'media':{},` ／ `'required_candidates':0,'optional_candidates':0,'evaluated_candidates':0,'fitted_candidates':0})` | 记录打包候选数／命中数／分阶段耗时／媒体准备统计。是**计数**，不是逐条内容。 |
| S1-02 媒体清单 | 已实现 | `src/len_bot/cognition/context.py:240`；`:1161`；`:1225` | `self.media_manifest = []` ／ `self.media_manifest.extend(prepared['manifest'])` ／ `self.media_manifest.append({'asset_id':asset,'status':'evicted','reason':reason})` | 记录资产 id 与被拒／驱逐原因。**不含像素内容**（`exclude={'content'}` 见 `web/query_service.py:124`）。 |
| S1-02 清单持久化位置 | 已实现 | `src/len_bot/runtime/agent_runtime.py:1403`；`src/len_bot/cognition/social_core.py:294` | `await self.event_store.save_trace(kind="conversation", scene_id=scene_id, ref_id=episode_id,` ／ `step['context_plan']=copy.deepcopy(context.context_plan)` | 落在 `traces` 表 `kind='conversation'`／`'conversation_error'` 的 payload；工作侧落 `kind='agent_job'`（`job_runner.py:533`）的 `runs[].context_plan`。 |
| S1-02 提示词／工具定义版本 | 未找到 | `src/len_bot/cognition/agent_loop.py:356` | `"forced_final": forced_final, "available_tools": [item["function"]["name"] for item in definitions],` | **只记工具名列表，不记 schema、不记提示词版本**。全仓 `grep` 无 `prompt_version`／`definitions_hash`／`schema_hash`／`template_version`。任务卡 S1-02「提示与工具定义版本」是真实缺口。 |
| S1-02 工作侧请求清单 | 部分实现 | `src/len_bot/runtime/job_runner.py:972` | `run_trace['context_plan']={'input_budget_tokens':config.job_context_tokens-config.work_output_tokens,` | 工作侧只记 `input_budget_tokens`／`estimated_input_tokens`／`omitted`／`current_pixel_assets`，**没有逐条 `messages` manifest**（比对话侧弱）。 |
| S1-03 顶层结局枚举 | 已实现 | `src/len_bot/cognition/models.py:11` | `class FinalDisposition(StrEnum):` ／ `SILENCE = "SILENCE"` ／ `ACTION = "ACTION"` | 只有两态。**它区分不了「主动沉默」与「Gate 拒绝」「失败」**——Gate 拒绝也返回 `SILENCE`（见下一行）。 |
| S1-03 Gate 拒绝原因 | 已实现 | `src/len_bot/runtime/gate.py:67`；`:175`；`:231` | `def __init__(self, disposition, reason, actions_enqueued=0, ...)` ／ `return GateDecision(FinalDisposition.SILENCE, 'Operator controls cannot submit messages', accepted=False)` ／ `return GateDecision(FinalDisposition.SILENCE, f"Gate rejected stale response: {reason}", accepted=False)` | 拒绝带 `accepted=False` + `reason` 字符串（中英混用）。**原因是自由文本，不是枚举**，无法稳定聚合。 |
| S1-03 来源级处理结局 | 已实现 | `src/len_bot/cognition/models.py:19`；`src/len_bot/cognition/proposals.py:712` | `status: Literal['replied','delegated','waiting','incomplete','silent']` ／ `status=item.status` ／ `elif result.next=='wait' and any(...): status='waiting'` | 这是 S1-03 最接近「统一结局」的枚举。但它**只覆盖本轮被 `handled` 的读入来源**，覆盖不到「未入场」「工具失败」「预算限制」「发送未知」。 |
| S1-03 未入场／无机会 | 已实现 | `src/len_bot/runtime/attention.py:162`；`src/len_bot/web/query_service.py:1598` | `event.metadata['attention_reasons'] = reasons` ／ `elif not reasons: stage, title = 'no_opportunity', '没有观察机会'` | 未产生读取机会的来源以空 `attention_reasons` 表示，`_participation()` 投影为 `no_opportunity`。 |
| S1-03 输入窗口 / 读取事实 | 已实现 | `src/len_bot/cognition/context.py:657`；`:682` | `def reconcile_original_reads(self, messages):` ／ `def confirm_original_reads(self):` | 只有模型真实返回后才确认已读；`social_core.py:277` 在 `after_model` 调用。**「读过 ≠ 已回复」在提交侧也被强制**（`proposals.py:708` 要求未完成／旁听必须给原因）。 |
| S1-03 执行失败 | 已实现 | `src/len_bot/runtime/agent_runtime.py:1320`；`:1328` | `except Exception as error:` ／ `self.metrics.inc_social("cognition_failed")` ／ `await self.event_store.save_trace(kind="conversation_error", ...)` | 失败落 `traces.kind='conversation_error'`，带 `error_type`／`error_phase`（`pre_commit` / `after_checkpoint` / `post_commit`）。与沉默在不同表里。 |
| S1-03 预算限制结局 | 部分实现 | `src/len_bot/cognition/budget.py:274`；`src/len_bot/runtime/job_runner.py:737` | `def _refusal(self, state: dict) -> tuple[str, str] \| None:` ／ `result.reason='model_budget_exhausted_at_finish'` | 预算耗尽有结构化 kind（`elapsed_time`／`model_steps`／`tokens`），但只在**工作**侧记入 `result.reason`；对话侧耗尽表现为 Gate 拒绝文本或 `next_action` 约束。 |
| S1-03 提交冲突／发送未知 | 已实现 | `src/len_bot/actions/models.py:21`；`src/len_bot/actions/delivery_store.py:83` | `UNKNOWN = "unknown"` ／ `return ('unknown', None, '发送尝试已登记，但没有可靠终态回执') if attempted else None` | 「发送未知」是 `DeliveryStatus.UNKNOWN`，与「选择潜水」完全不同层。 |
| S1-03 **统一结局投影** | 未找到 | `src/len_bot/web/frontend/src/domain/messageProgress.js:121` | `return { ...facts, steps: [entry, read, handled, materials, commit, delivery] }` | **唯一的「统一投影」在前端**（`messageProgress.js`），由 6 个 step 拼装；后端没有对应函数。判定依据是 `attention`／`source_outcomes`／`publication_status`／`delivery_status` 四份不同来源的字段。 |
| S1-04 `model_calls` 字段 | 已实现 | `src/len_bot/cognition/call_store.py:94`；`:98` | `id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, episode_id TEXT, job_id TEXT, batch_id TEXT,` ／ `role TEXT NOT NULL, purpose TEXT NOT NULL, provider_id TEXT NOT NULL, model TEXT NOT NULL,` ／ `status TEXT NOT NULL, usage_json TEXT, estimate_json TEXT NOT NULL,` | 另加 `output_ceiling_tokens`／`held_tokens`（`:106-108` 就地 ALTER）。`usage_json` 是供应商原样，`estimate_json` 是本地估算，**两者分开**。 |
| S1-04 缓存／推理 token | 已实现 | `src/len_bot/cognition/call_store.py:595`；`src/len_bot/web/query_service.py:430` | `for field, details, key in (("cached_tokens", "prompt_tokens_details", "cached_tokens"),` ／ `("cached_tokens","prompt_tokens_details.cached_tokens"), ("reasoning_tokens","completion_tokens_details.reasoning_tokens")):` | 从 `usage.prompt_tokens_details.cached_tokens` 与 `completion_tokens_details.reasoning_tokens` 读取；缺失按 `unknown_usage` 计数（`query_service.py:428`），**不冒充 0**。 |
| S1-04 分阶段 purpose | 已实现 | `src/len_bot/cognition/gateway.py:66` | `if self.purpose not in {"conversation", "plugin_agent", "announcement", "interest_share", "work", "heartbeat", "action_review", "history_maintenance", "work_compression", "skill_maintenance", "capability_probe"}:` | 11 种 purpose 是硬校验枚举。用量页按 `purpose,disposition` 分组（`query_service.py:434`）。**"互动 vs 工作" 可由此聚合，但不是现成字段。** |
| S1-04 预留账 | 已实现 | `src/len_bot/cognition/call_store.py:118`；`:121` | `CREATE TABLE IF NOT EXISTS usage_reservations (` ／ `status TEXT NOT NULL, usage_tokens INTEGER, estimated_tokens INTEGER,` | 每工作日账户维度；`held`／`settling`／`settled`／`released`（`query_service.py:405-409` 聚合 SQL 明确列出）。 |
| S1-04 延迟口径 | 部分实现 | `src/len_bot/runtime/metrics.py:48`；`src/len_bot/runtime/agent_runtime.py:1347` | `def record_latency(self, phase: str, seconds: float) -> None:` ／ `self.metrics.record_latency("cognition_total", time.monotonic() - started)` | **`record_latency` 全仓只有 1 个调用点**，phase 名注释里列的 7 个（event→burst、burst→request、model_total、parse、gate、queue→send、end_to_end）**只有 `cognition_total` 真正在写**。`RuntimeMetrics` 是进程内 `deque(maxlen=200)`，**重启即失**。 |
| S1-04 单轮阶段耗时 | 已实现 | `src/len_bot/cognition/social_core.py:176`；`:349`；`:367` | `audit['initial_context_ms'] = round((time.monotonic() - context_started) * 1000, 2)` ／ `timings['commit'] = round(...)` ／ `timings['publication'] = round(...)` | `context_plan['timings_ms']` 内另有 `event_packing`／`image_related_source_reads`／`associated_source_reads`（`context.py:871,425,1572`）。这些**按轮持久化**，比 metrics 可靠。 |
| S1-04 单次调用耗时 | 已实现 | `src/len_bot/cognition/agent_loop.py:369`；`src/len_bot/actions/queue.py:217` | `step.update({"latency_ms": response.latency_ms, "usage": response.usage,` ／ `"queue_ms": queue_ms, "send_ms": send_ms,` | 模型延迟落 step；队列／发送延迟与 `event_to_delivery_ms` 落回执 payload（`queue.py:220`）。**但都不进 metrics 聚合**。 |
| S1-04 工作侧分阶段 | 部分实现 | `src/len_bot/runtime/job_runner.py:808`；`src/len_bot/cognition/budget.py:187` | `run_trace = {"job_revision": revision, 'budget_snapshot': {` ／ `class WorkBudgetSnapshot(BaseModel):` | 记录创建时**冻结**的预算快照（这是防「按今日策略重算旧工作」的关键设计），但无工具／模型分阶段耗时。 |
| S1-05 提交状态机 | 已实现 | `src/len_bot/runtime/gate.py:57`；`:60` | `status: Literal["pending", "completed", "failed", "interrupted", "not_repeated"] = "pending"` ／ `phase: Literal["not_started", "awaiting_publication", "action_preparation", "scheduler_schedule",` | `PublicationRecord` 记录持久提交**之后**的发布过程。`not_repeated` 专门表示「同一 checkpoint 已提交，发布不重放」（`:298-303`）。 |
| S1-05 单行动发布状态 | 已实现 | `src/len_bot/runtime/gate.py:52`；`:469` | `status: Literal["not_enqueued", "enqueued", "enqueue_unknown"] = "not_enqueued"` ／ `record.status = "enqueue_unknown"` | 注释明说：入队若在「已接受」之后抛错，该行动必须与「从未触碰」区分（`:467-471`）。 |
| S1-05 送达状态 | 已实现 | `src/len_bot/actions/models.py:17`；`:34` | `class DeliveryStatus(StrEnum):` ／ `SENT = "sent"` ／ `NOT_SENT = "not_sent"` ／ `REJECTED = "rejected"` ／ `UNKNOWN = "unknown"` ／ `def receipt_delivery_status(event_type, payload, metadata) -> str \| None:` | 关键正确性：`MESSAGE_SENT` 若**没有**平台 `message_id` 会被归为 `unknown` 而非 `sent`（`:48-50`）。`sent`／`shadow`／`simulated`／`unknown`／`not_sent`／`rejected` 六态。 |
| S1-05 送达回执写回点 | 已实现 | `src/len_bot/actions/queue.py:210`；`:227`；`src/len_bot/actions/delivery_store.py:73` | `event = Event(event_type=(EventType.FILE_UPLOADED if success else EventType.FILE_UPLOAD_FAILED) if action.file_asset_id` ／ `await self._emit(event, event.metadata.get('associated_open_loop') if success else None)` ／ `async def delivery_fact(self, action_id, scene_id):` | 真实回执**由 ActionQueue 发送后写 `events`**，不是由适配器写。之前先 `begin_delivery_attempt`（`delivery_store.py:152`）写 `send-attempt:` 事件，保证「丢响应不重放」。 |
| S1-05 延期交付阶段 | 已实现 | `src/len_bot/actions/delivery_store.py:21`；`:187` | `delivery_phase: Literal['waiting', 'queued', 'attempted', 'terminal'] = 'waiting'` ／ `async def finish_deferred_in_transaction(self, event):` | 延期发送沿 `tasks.payload` 存阶段，终态回填 `delivery_event_id`。 |
| S1-05 任务／工作状态枚举 | 已实现 | `src/len_bot/scheduler/models.py:6`；`src/len_bot/runtime/job_store.py:56` | `class TaskStatus(StrEnum):` ／ `PENDING/CLAIMED/PROCESSING/RESULT_READY/AWAITING_DELIVERY/COMPLETED/FAILED/DELIVERY_UNKNOWN/SHADOW_OBSERVED/REVIEW_REQUIRED/CANCELLED` ／ `data["execution_status"] = (data["result"] or {}).get("status") or {` | **`status`（响应／交付）与 `execution_status`（执行是否成功）明确分开**（`:55` 注释原文）。这是 S1-05「已提交但未发布／已入队／平台未确认」分辨的基础。 |
| S1-05 平台侧动作状态 | 已实现 | `src/len_bot/runtime/platform_actions.py:8`；`:11` | `CREATE TABLE IF NOT EXISTS platform_actions (` ／ `job_revision INTEGER NOT NULL, status TEXT NOT NULL, attempted_at REAL,` | 对外平台动作（非 QQ 送达）是**另一张表**，注册为 `not_sent`（`:33`），结果写 `PLATFORM_ACTION_RESULT` 事件（`:54`）。与 QQ 回执不混。 |
| S1-05 执行容器状态 | 已实现 | `src/len_bot/execution/protocol.py:37`；`:58` | `class ExecutionState(StrEnum):` ／ `ACCEPTED/STARTING/RUNNING/EXITED/FAILED/CANCEL_REQUESTED/TERMINATION_CONFIRMED/TERMINATION_UNCONFIRMED` ／ `TERMINAL_STATES = frozenset({` | 明确区分「请求了停止」(`cancel_requested`) 与「确认已停止」(`termination_confirmed`)／「未知」(`termination_unconfirmed`)。注释称「`cancel_requested` is not a stop」。 |
| S1-06 查询服务入口 | 已实现 | `src/len_bot/web/query_service.py:26`；`src/len_bot/web/query_service.py:1268`；`:1217` | `class RuntimeQueryService:` ／ `async def relations(self, scene_id, *, event_id=None, job_id=None, ...)` ／ `async def trace(self, trace_id, scene_id=None):` | 单一只读门面（docstring 声明路由不得绕过它访问 `event_store._db`）。`trace()` 会把 `tool_results` 与 `stored_observation` 一并解析（`:1230-1244`）。 |
| S1-06 一条回复的时间线查询 | 部分实现 | `src/len_bot/web/routes/cockpit.py:464`；`src/len_bot/web/query_service.py:1586` | `async def relations(scene_id: str, request: Request, event_id: str \| None = None, ...)` ／ `return {"events":event_views,"traces":[...],"calls":[...], "jobs":jobs,"actions":list(actions.values())[:limit],"tool_results":tools,"batches":[], "turns":turns,"source_handling":source_handling,'operation_receipts':operations[:limit],` | `GET /api/cockpit/relations` **要求五选一锚点**（event／job／episode／action／batch），返回按类别分组的关联集合，**不是按时间排序的单一时间线**。它给的是「同一身份链的全部相关对象」。 |
| S1-06 `SceneInspector` | 部分实现 | `src/len_bot/web/frontend/src/components/SceneInspector.vue:17`；`:78-85` | `const props = defineProps({ event: Object, relations: Object, loading: Boolean, error: String, readAt: Number, active: Boolean, modal: Boolean })` ／ `<h3>表达行动与回执 <span>{{ relations.actions.length }}</span></h3>` | **SceneInspector 是纯前端组件**（无 Python 符号），消费上面的 `relations`。分工段渲染：表达行动与回执／轮次与执行轨迹／模型请求／工具资料／原始事件。 |
| S1-06 结局解释（前端） | 已实现 | `src/len_bot/web/frontend/src/domain/messageProgress.js:61`；`:114` | `export function messageProgress(event, relations) {` ／ `const deliveryLabels = { sent: '真实送达', failed: '明确未送达', unknown: '送达未知', shadow: 'Shadow', simulated: '模拟', pending: '待回执', unrecorded: '未记录结局' }` | 这是当前唯一「一句回复 6 步」的成体系投影：触发与入口→实际阅读→最近已记录处理→资料与工作→提交与发布→发送与回执。 |
| S1-06 脱敏 | 已实现 | `src/len_bot/web/query_service.py:102` | `private = {"api_key", "password", "password_hash", "authorization", "access_token", "refresh_token",` ／ `"token", "path", "locator", "base64", "checkpoint_data", "checkpoint_json", "messages_json",` ／ `"continuation", "extra_content", "provider_private", "reasoning_content", "read_result_ranges"}` | `_public()` 递归剔除私密键、含 `signature` 的键，并把 `data:...;base64,...` 替换为占位串。 |
| S1-06 诊断导出 | 未找到 | （无符号） | `grep -rn "diagnostic\|export" src/len_bot/web/routes/*.py` 只命中 `jobs/{job_id}/files/{asset_id}/download` 与 `workspace-artifact/download`（`cockpit.py:156,202`） | **没有「一次授权的临时诊断导出」入口**，只有单个文件下载。任务卡 S1-06 交付物里的「诊断导出说明」无可挂载实现。 |

---

## 二、四类身份的完整实例（S1-01 主链实测路径）

沿源码可确认一条普通回复经过以下身份，**每一跳都是存储外键，不靠时间或文本相似**：

| 跳 | 产生的身份 | 产生点 |
|---|---|---|
| 群消息 | `events.id` | 适配器 → `EventStore`（`events/store.py:74`） |
| 读取机会 | `events.metadata['attention_reasons']` + `SceneSession.pending_wakes` | `runtime/attention.py:162,178` |
| 本轮 | `episode_id = f"conversation:{uuid}"` | `runtime/agent_runtime.py:1119` |
| 模型调用 | `model_calls.id`（带 `episode_id`） | `cognition/gateway.py:93` |
| 持久提交 | 事件 `turn:{episode}:checkpoint:{n}` | `scenes/actor.py:294` |
| 行动 | `payload.action_ids[i]`（UUID） | `runtime/gate.py:338,346` |
| 发布 | `traces` 内 `gate.publication.actions[].status` | `runtime/gate.py:417-426` |
| 真实回执 | 新 `events` 行，`payload.action_id` 回指 | `actions/queue.py:210,227` |

**未确认**：`episode_id` 与实际持久对象之间**没有行**——`episodes` 表被 `events/store.py:44-45` 判为 retired 并在启动时拒绝；`episode_id` 只是若干 JSON 列里的一段字符串。

---

## 三、未确认与读不到

- **未打开 `len_bot.db`**：以上全部结论来自源码，**未核实**实际库中 `events`／`model_calls`／`traces` 的行数、历史数据是否缺 `answer_basis`／`context_plan`、旧 `episode_id` 是否真为 UUID 形态。
- **未运行任何代码**：`request_manifest` 与 `context_plan` 的**实际键集**未在运行时验证；本报告的字段列表来自 `read` 到的构造表达式。
- **未确认** `record_latency` 是否还有非 `grep` 可见的动态调用点（如通过 `getattr`）；`grep -rn "record_latency"` 全仓只有定义与 `agent_runtime.py:1347` 一处。
- **未确认** `ActionQueue` 内存队列在进程重启时未入队行动的最终处置——`actions/queue.py:44-63` 的 `stop()` 会 `_reject(... unknown=False)`，但**崩溃**（非优雅停止）路径未读到兜底逻辑。
- **未确认** `messageProgress.js` 是否与后端任何契约测试绑定；它是纯前端推导，后端不产出同名结构。
- **未确认** 供应商 `usage.prompt_tokens_details` 的实际形状；`gateway.py:114` 只做 `isinstance(body, dict)`，因此「字段缺失＝未报告」与「缺失＝未命中」在源码层不可区分。
- **未核实** `docs/plan/s0-01-s2-context-inventory.md` 已记录的部分（会话段、窗口锚定）与 S1 的边界重叠是否已被维护者划分；本报告只覆盖 S1。
- **读到但未采信**：`runtime/query_service.py` 中 `_participation()` 的 `stage` 取值（`no_opportunity`／`attempted_unknown`／`delivered`／`waiting_resource`／`slow_opportunity`／`excluded`）**只服务于 UI 展示**，不落库、不被任何业务判定消费。

---

## 四、与任务卡原假设的差异

1. **S1-01「复用 event/episode/call/tool/job/revision/action/receipt 身份建立查询关系」——任务卡把它当缺口，但关联查询已经存在。**
   `RuntimeQueryService.relations()`（`web/query_service.py:1268`，路由 `cockpit.py:464`）已能沿存储身份从任一锚点扩散到七类对象，且注释明写 "Follow stored identifiers only; timestamps never establish a relation."（`:1269`）。**S1-01 更接近「补术语表 + 补 episode 持久行」，不是从零建关联。**

2. **S1-01 把 `episode` 与 `event/call/job` 并列，暗示它是同级的既有身份；实际没有 episode 表。**
   `events/store.py:44-45` 检测到旧 `episodes` 表会**拒绝启动**，`episode_id` 是每轮随机 UUID（`agent_runtime.py:1119`）。任务卡「明确不做」里写「不新增另一套事件总线」，但**没有说明 episode 是否需要补一张最小持久表**——这是 S1-01 的真实决策点。

3. **S1-03「找 attention/输入窗口/respond 相关符号，说明当前是否存在统一结局投影」——任务卡假设可能有；实际是「前端有、后端没有」。**
   后端只有分散的四套：`FinalDisposition`（2 态，`models.py:11`）、`SourceOutcome.status`（5 态，`models.py:19`）、`DeliveryStatus`（6 态，`actions/models.py:17`）、`PublicationRecord`（5+9 态，`gate.py:57-60`）。统一投影在 `messageProgress.js:121`。**若 S1-03 要在后端建投影，需先决定这四套是否合并——任务卡「明确不做」并未排除。**

4. **S1-02「请求材料清单与版本依据」——`answer_basis` 与 `context_plan` 已存在，任务卡若把它们当缺口会重复建设。**
   `answer_basis` 有完整定义 + 5 处校验（`models.py:104`、`events/store.py:1266`），`context_plan['request']` 已含 `messages` manifest（`social_core.py:264-266`）。**真正的缺口只有两项**：工具定义 schema 版本（`agent_loop.py:356` 只存名字）、工作侧逐条 manifest（`job_runner.py:972` 无 `messages`）。

5. **S1-04「完善用量和分阶段延迟口径」——用量侧基本齐备，延迟侧比任务卡假设的更空。**
   用量：`model_calls` + `usage_reservations` + `purpose` 11 枚举 + `cached_tokens`／`reasoning_tokens` 分项，均已实现。延迟：`RuntimeMetrics.record_latency` 注释声称 7 个 phase（`metrics.py:49-50`），**实际只有 `cognition_total` 一个调用点**，且是进程内 `deque`，重启即失。持久化的分阶段耗时只在 `context_plan['timings_ms']` 里（`social_core.py:349,367`；`context.py:871,425,1572`），**任务卡把它们列为「待完善」，实际应表述为「已有两条互不相通的延迟通道，需选一条并补齐」。**

6. **S1-05「收口提交、发布与送达的真实状态」——本项状态枚举最完整，任务卡若按「缺口」处理会低估现有实现。**
   `sent` 缺平台 id 降级为 `unknown`（`actions/models.py:48-50`）、`enqueue_unknown`（`gate.py:52`）、`not_repeated`（`gate.py:57`）、`termination_unconfirmed`（`execution/protocol.py:52`）、`DELIVERY_UNKNOWN`（`scheduler/models.py:14`）——这些正是任务卡验收条件「结果未知不自动重放」的既有实现。**建议 S1-05 定位为「补状态转换表文档 + 补缺口」，而非重写。**

7. **S1-06「完成一条可读的审计时间线」——任务卡列 `SceneInspector` 为「涉及现有模块」，但它不在 Python 侧。**
   `SceneInspector.vue`（`:17`）是前端组件，后端对应物是 `relations()` 返回的**分组集合**而非时间线。且 `/api/cockpit/relations` **强制单锚点**（`cockpit.py:467-468` 要求五选一，多于一个报 400）。**「一条普通回复的完整时间线」当前需要前端把 `relations` 的六个分组按 6 步拼装**（`messageProgress.js:121`）。诊断导出（S1-06 交付物）**未找到任何实现**。

---

## 五、反向引用清单

**身份与关联（S1-01）**
- `src/len_bot/events/store.py:74` `events` 建表；`:44` 旧 `episodes` 表被拒
- `src/len_bot/runtime/agent_runtime.py:1119` `episode_id` 随机 UUID
- `src/len_bot/cognition/call_store.py:94-95` `model_calls` 主键与外键列
- `src/len_bot/runtime/job_store.py:312-313` `agent_jobs` 主键与 `revision`；`:329` `job_exchanges.goal_revision`
- `src/len_bot/memory/store.py:64` 记忆 `revision`
- `src/len_bot/actions/models.py:79` action UUID；`src/len_bot/runtime/gate.py:338,346` action ids 生成与写盘
- `src/len_bot/runtime/gate.py:555` `batch_id` 派生；`src/len_bot/scenes/actor.py:294` turn 事件 id
- `src/len_bot/web/query_service.py:1268-1269` `relations()` 与「不靠时间」注释
- `src/len_bot/events/store.py:103` `scene_sessions.version`

**请求清单（S1-02）**
- `src/len_bot/cognition/context.py:1241-1249` `request_manifest`
- `src/len_bot/cognition/context.py:266` `context_plan` 初始化；`:865` `event_packing_detail`；`:240,1161,1225` `media_manifest`
- `src/len_bot/cognition/social_core.py:264-266` `context_plan['request']`；`:294,297` 写入 step／payload
- `src/len_bot/cognition/models.py:82,104` `AnswerBasisKind`／`AnswerBasis`
- `src/len_bot/cognition/proposals.py:662-671` basis 构造与校验；`src/len_bot/events/store.py:1266-1307` `validate_answer_basis`
- `src/len_bot/scenes/actor.py:335-336`；`src/len_bot/actions/queue.py:90`；`src/len_bot/web/query_service.py:1545` answer_basis 三处落库
- `src/len_bot/cognition/agent_loop.py:356` 只记工具名
- `src/len_bot/runtime/job_runner.py:972-975` 工作侧 `context_plan`
- `src/len_bot/runtime/agent_runtime.py:1403-1414` conversation trace 写盘

**结局区分（S1-03）**
- `src/len_bot/cognition/models.py:11-13` `FinalDisposition`
- `src/len_bot/cognition/models.py:16-22` `SourceOutcome.status`
- `src/len_bot/cognition/proposals.py:697-724` status 推导；`:708-711` 未完成／旁听须给原因；`:695` note 必填
- `src/len_bot/runtime/gate.py:175,231,239,241,256,277` 拒绝路径返回 SILENCE
- `src/len_bot/runtime/attention.py:162-167` `attention_reasons`／`attention_lane`；`:178-181` `pending_wakes`
- `src/len_bot/web/query_service.py:1593-1608` `_participation()`
- `src/len_bot/runtime/agent_runtime.py:1313,1320,1328,1332` 失败与 `conversation_error`
- `src/len_bot/cognition/budget.py:274-284` `_refusal()`；`src/len_bot/runtime/job_runner.py:737-748` 预算耗尽原因
- `src/len_bot/cognition/context.py:657,682` `reconcile_original_reads`／`confirm_original_reads`
- `src/len_bot/web/frontend/src/domain/messageProgress.js:121` 六步投影

**用量与延迟（S1-04）**
- `src/len_bot/cognition/call_store.py:94-108` `model_calls` 全字段；`:118-122` `usage_reservations`；`:152-153` 重启收口；`:572-601` `get_model_call_totals`（无调用方）
- `src/len_bot/cognition/gateway.py:66` purpose 枚举；`:93-99` `begin_model_call`；`:124-128` `end_model_call`
- `src/len_bot/web/query_service.py:412-437` `model_usage` 与按 `purpose,disposition` 聚合；`:436` 费用 `unverified`
- `src/len_bot/runtime/metrics.py:18-46` 计数器；`:48-55` `record_latency`；`:60-77` `snapshot`
- `src/len_bot/runtime/agent_runtime.py:1347` 唯一 `record_latency` 调用
- `src/len_bot/cognition/social_core.py:176,349,367` `initial_context_ms`／`commit`／`publication` 耗时
- `src/len_bot/cognition/context.py:425,871,1572` `timings_ms` 各键
- `src/len_bot/cognition/agent_loop.py:369-374` step 内 latency/usage；`src/len_bot/actions/queue.py:217-220` queue/send/event_to_delivery
- `src/len_bot/cognition/budget.py:187-205` `WorkBudgetSnapshot`

**提交／发布／送达（S1-05）**
- `src/len_bot/actions/models.py:17-21` `DeliveryStatus`；`:34-54` `receipt_delivery_status`（含缺平台 id 降 unknown）
- `src/len_bot/runtime/gate.py:52` `ActionPublication.status`；`:57-61` `PublicationRecord.status/phase/scheduler_status`；`:429-486` `publish_committed`；`:467-471` `enqueue_unknown` 语义
- `src/len_bot/scenes/actor.py:295-305` `not_repeated` 幂等分支
- `src/len_bot/actions/queue.py:148-231` `_process` 发送与回执；`:107-116` `_reject`；`:185-196` 尝试先行
- `src/len_bot/actions/delivery_store.py:21` `delivery_phase`；`:33-71` 依赖校验；`:73-87` `delivery_fact`；`:152-185` `begin_delivery_attempt`；`:187-199` `finish_deferred_in_transaction`
- `src/len_bot/scheduler/models.py:6-17` `TaskStatus`；`src/len_bot/runtime/job_store.py:55-63` `status` vs `execution_status`
- `src/len_bot/runtime/platform_actions.py:8-13,27-35,42-57` 平台动作独立账
- `src/len_bot/execution/protocol.py:37-67` `ExecutionState`／`TERMINAL_STATES`／`OCCUPYING_STATES`

**审计查询（S1-06）**
- `src/len_bot/web/query_service.py:26` 门面声明；`:1268-1590` `relations()` 全实现；`:1217-1245` `trace()`；`:1610-1611` `metrics()`
- `src/len_bot/web/routes/cockpit.py:464-470` `/relations` 路由与单锚点约束；`:458-461` `/traces/{trace_id}`
- `src/len_bot/web/query_service.py:102-114` `_public()` 脱敏
- `src/len_bot/web/frontend/src/components/SceneInspector.vue:17,39-43,78-85` 组件与分组渲染
- `src/len_bot/web/frontend/src/domain/messageProgress.js:6-55,61-121` 关联收窄与六步投影
- `src/len_bot/web/frontend/src/components/TraceDetails.vue:92` `context_plan` 展示
- `src/len_bot/web/frontend/src/components/AnswerBasisDetails.vue:17,21` basis 与 unresolved 展示
