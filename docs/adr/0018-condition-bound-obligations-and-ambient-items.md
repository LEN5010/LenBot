# ADR-0018: Condition-Bound Obligations & Ambient Retained Items

## Status

Partially superseded by ADR-0032/ADR-0033 — 2026-09-05. The separate `AmbientStore` was removed: retained soft state lives in `GroupAgentSession.retained_attention`, proposed by the Social Core and pruned on lawful commits. `PLUGIN_FACT` events enter the Social Core directly with no attention prefilter. The condition-bound obligation half (`wake_event_type`, `TASK_DUE` wake matching) remains active, refined by ADR-0029/ADR-0034.

## Context

V2 计划 Phase 3(Persistent Social Continuity)要求闭合 Goal 4/5/6/7:

- **Goal 5(记得自己答应过什么)**:当前 `TaskItem` 只有 `due_at` 相对时间语义。"开播叫我"这类承诺无法表达为"当 LIVE_STARTED 事件发生时唤醒";插件事实事件(直播开播)到达时 Runtime 没有任何义务匹配机制。
- **Goal 7(主动性必须有社会理由 / Retained Interest)**:Agent 浏览到一件可能以后有用的东西(切片、热帖、群内有趣内容)后,没有任何短期 soft state 可以保存它,并在未来相关话题出现时带入 Situation Package。现有 `initiative_interest` soft annotation 从未被再次读取。
- **Goal 4 溯源缺陷**:`OpenLoopManager.resolve_loop` 以空字段构造假行,resolve 不存在/已完结的 loop 时会写入脏数据。

## Decision

### 1. 条件绑定 Obligation(TaskItem + TaskProposal)

`TaskItem` 增加 `wake_event_type: Optional[str]`。条件任务由**先到者**触发:

- 匹配事件到达:`TaskScheduler.on_event(committed_event)` 在事件被 SceneActor 原子提交后调用,匹配 `wake_event_type == event.event_type and scene_id 相同` 的 pending 任务,走与定时任务完全相同的 `TASK_DUE` 事件路径(触发本身仍是 Event,Invariant A 保持)。
- `due_at` 截止:条件任务的 `due_at` 是最长等待截止期(缺省 now + 604800s),截到同样发 `TASK_DUE`,由 cognition 结合最新上下文自行判断是否已过时。

`TaskProposal` 增加 `wake_event_type`,`delay_seconds` 变为 Optional(条件任务可省略)。所有写入仍在 `commit_proposal_transaction` 原子事务内。

### 2. 插件事实事件不构成发言理由

新增 `EventType.LIVE_STARTED / LIVE_ENDED` 与 `StimulusType.PLUGIN_FACT`。插件事实事件绕过 debounce 立即 flush,但 `AttentionEngine` 对 PLUGIN_FACT **显式返回 OBSERVE**(不进入 initiative 评分)——裸事实事件永远不唤醒 cognition,只有 obligation 匹配(经 `TASK_DUE → PROACTIVE_TASK` 既有硬唤醒路径)才 WAKE。这保证 Invariant F/Goal 7:插件不能制造主动发言。

### 3. Ambient Item(短期 soft state,Goal 7)

新模块 `state/ambient.py`:

- `AmbientItem`:id / scope / source / topic / summary / salience / retained_at / expires_at / evidence_event_id。
- `AmbientStore`:**纯内存**(非 durable——完成定义的持久化清单只含 OpenLoop/Task/Memory;小时级寿命的内容重启丢失符合定义)。`retain` / `match(text, scope)`(词面重叠 × salience 评分,scope 过滤含 global-safe)/ `sweep(now)`(maintenance loop 清扫,默认 TTL 6h)。
- 提案路径:`EpisodeOutcome.retained_item_proposals`(Invariant C 的 optional retained-item proposal)。`RuntimeGate` 在 DB 事务提交后写入 AmbientStore——与 Scheduler 注册同层的外部副作用,因非 durable 不入事务。
- 注入路径:wake episode 组装 Situation Package 时 `ambient_store.match(stimulus 文本)` 产出【RELEVANT AMBIENT ITEMS】段,由 Pi 自主决定使用或 SILENCE;system 规则明确"无相关话题绝不主动传播"。

### 4. OpenLoop resolve 修正

`OpenLoopManager.resolve_loop` 改为:读取该 scene 的 active loop → 不存在或非 active 返回 False;存在则仅状态迁移为 resolved(字段原样保留,溯源不丢)。

## Consequences

- 正向:Goal 5 的承诺-履行闭环成立("开播叫我" → LIVE_STARTED → WAKE → "开了");Goal 7 的 Ambient Recall 成立且不需要向量库;OpenLoop 历史可溯源。
- 负向(接受):AmbientStore 重启即空——retained item 是小时级 soft state,持久化会诱导它向 Memory 漂移(违反 §九"不要永久沉淀");条件任务截止期触发可能产生一次"已过时"的 WAKE,由 cognition SILENCE 吸收。
- 中立:任务触发的唯一权威路径仍是 `TASK_DUE` 事件,Attention/episode/gate 逻辑零改动。
