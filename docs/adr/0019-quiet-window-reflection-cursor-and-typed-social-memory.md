# ADR-0019: Quiet-Window Reflection, Reflection Cursor & Typed Social Memory

## Status

Accepted — implementation note 2026-09-05: the `ContextAssembler`【CURRENT ACTOR】person-card segment referenced below was removed with the V3 ContextAssembler; sender snapshots now reach cognition as `working_persons` projections in the Social Core situation package (ADR-0032/0033), and subject-scoped memories are retrieved on demand via the agentic retrieval tools (ADR-0035).

## Context

V2 计划 Phase 4(Reflection & Social Memory)要求闭合 Goal 9/11/12。核实发现的现状缺陷:

- **触发逻辑脆弱**:反射仅在 `bot_engagement in ("observing","idle")` 且 `intervening_messages_since_bot == 5`(精确等于)时触发——"第 N 条消息触发"正是计划 §10.3 明令删除的计数式逻辑;且每次重新拉取最近 20 条事件。
- **无 cursor**:同一批事件会被重复总结, episodes 表重复膨胀。
- **LLM reflector 从未接线**:`ReflectionEngine.llm_reflector` 接口存在但 `AgentRuntime` 构造时不传,生产永远走确定性 fallback,MemoryProposal 产出为零,Goal 11(社会位置形成)不可能发生。
- **kinds 自由字符串**:仅注释约定 preference/relationship/fact/pattern,无 group_norm/topic_interest/recurring_role/social_pattern。
- **Person Context 缺失**:OneBot 入站丢弃 sender 快照, prompting 中只有 `user:123456`。
- **decay 死代码**:`decay_memories` 从未接入 maintenance loop。

## Decision

### 1. Quiet Window 触发(替换计数触发)

每个 scene 维护一个防抖定时器(`loop.call_later`)。事件提交后重置定时器;场景安静满 `reflection_quiet_window_seconds`(默认 150s)后触发反射。若触发时 `bot_engagement == "active"`(Bot 仍在对话中)顺延一个窗口。测试以注入小窗口值保持确定性,不依赖 sleep 证明语义。

### 2. Reflection Cursor

`reflection_cursors(scene_id PK, last_event_rowid, updated_at)`。反射只处理 `rowid > cursor` 的事件区间(`EventStore.get_events_since`,按 rowid 即不可变写入序),成功后推进 cursor 到本批最大 rowid。同一批事件不可能被反思两次;部分失败(异常)不推进,下一窗口重试同一区间。

### 3. LLM Reflector 接线

新 `memory/reflector.py`。生产模式(`mock_pi_handler is None`)由 AgentRuntime 注入 reflector(与 cognition 同一 client、normal 模型);mock/测试模式显式走确定性 fallback——不是静默降级,而是测试路径。Reflector 输出 `EpisodeRecord + MemoryProposal[]`,每个提案 evidence 引用整个反射区间(可证在 scope 内,gate 强制校验),prompt 限定 typed kinds 并禁止人格标签。Reflector 只有提议权(Invariant E),写入仍经 MemoryGate / 原子事务。

### 4. MemoryKind 规范化

`MemoryKind` StrEnum:preference / habit / relationship / fact / group_norm / topic_interest / recurring_role / social_pattern。旧数据 `pattern` 启动时一次性 `UPDATE` 为 `social_pattern`(数据迁移,非双语义)。

### 5. Person Card

- `adapters/onebot.py`:入站 payload 保留 `sender: {nickname, card, role}` 快照(仅展示身份与当前群身份,非永久 profile 复制)。
- `ContextAssembler` 新增【CURRENT ACTOR】段:display name(card‖nickname‖actor_id)+ 群身份 + subject 过滤的 per-person memories(`query_memories(subject=...)`)。SQL 层 scope 守卫自然适用(Invariant G)。

### 6. Decay 接线

maintenance loop(60s)追加 `memory_store.decay_memories()`,时间衰减与遗忘正式成为运行时行为。

## Consequences

- 正向:一段聊天 + 安静窗口 → 恰好一次 Episode;L2 社会记忆开始真实产出,Goal 9/11 成为可能;prompt 中不再只有裸 actor ID。
- 取舍(接受):反射延迟最多一个 quiet window(实时性换稳定性,符合 §10.3 设计意图);反射失败不推进 cursor 会在下个窗口重试,无丢失。
- 中立:cursor 用 rowid 而非事件 ID/时间戳——免疫同秒事件乱序,SQLite rowid 单调稳定。
