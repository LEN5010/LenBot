# S2 持续上下文能力现状索引（只读源码核对）

- **核对 commit**：`ad41a5a71a4b5ec3693f4cc846342964c5b0b8c6`（分支 `feat/s0-product-contract`，基线业务代码 `9178c34`）
- **核对时间**：2026-09-21T14:21Z（容器 UTC）
- **基线差异**：`git diff --stat 9178c34 ad41a5a` 只新增两份规划文档（任务卡 1245 行、路线书 823 行），**没有业务源码变更**；本报告中的符号在 `9178c34` 已存在。
- **只读方式**：`git rev-parse/log/diff/status`、`grep`、`glob`、`read`。未运行测试／夹具／探针／回放，未启动服务，未调用模型或平台，未打开 `len_bot.db` 或媒体目录，未做任何写操作（本文件除外）。
- **一句话结论**：**当前普通群聊仍是逐轮重建**——每轮用新的 `episode_id` 新建 `ConversationContext` 并从数据库重读事件重拼请求，从不复用上一轮的原生消息轨迹；**“会话段”作为持久对象并不存在**。但 S2 的六项里已有多处**可复用的既有结构**（按场景一条 `SceneSession`、`history_batches` 压缩表、`open_loops` 等待／恢复表、窗口锚定与 `reconcile_original_reads`、缓存字段落库），因此 S2 更接近“在已有结构上补一段”，不是在空地上全新设计。

---

## 一、判定表

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口／未确认 |
|---|---|---|---|---|
| S2-01 持久“会话段”对象 | 未找到 | `src/len_bot/scenes/models.py:76`；`src/len_bot/events/store.py:102` | `class SceneSession(BaseModel):` ／ `CREATE TABLE IF NOT EXISTS scene_sessions (scene_id TEXT PRIMARY KEY, version INTEGER NOT NULL,` | 无 segment／episode 表；`scene_sessions` 每场景一行，字段里没有段身份、装配版本、已接入截点、摘要引用。 |
| S2-01 每群一个主会话（而非用户×群窗口） | 已实现 | `src/len_bot/events/store.py:102`；`src/len_bot/scenes/manager.py:20` | `scene_id TEXT PRIMARY KEY` ／ `actor = SceneActor(scene_id, self.bot_actor_id, self.event_store, ...)` | 房间粒度正确；但它保存的是注意力／pending，不是“段”。 |
| S2-01 段标识是否独立于全局 rowid 取模 | 未找到 | `src/len_bot/runtime/agent_runtime.py:1119` | `episode_id = resume.episode_id if resume else f"conversation:{uuid.uuid4().hex}"` | 现有 `episode_id` 是**每轮随机 UUID**（仅 `wait` 恢复时沿用），不是可取模的稳定段身份。 |
| S2-02 逐轮重建请求 | 已实现（现状如此，非目标态） | `src/len_bot/cognition/social_core.py:60`；`src/len_bot/cognition/context.py:1491` | `context=ConversationContext(runtime,session,through_rowid)` ／ `async def build(self, events, current_ids, *, ...)` | 每轮新建 context 对象；无任何“上一轮 trajectory”入参。 |
| S2-02 跨轮复用原生消息轨迹 | 未找到 | `src/len_bot/cognition/agent_loop.py:521`；`src/len_bot/runtime/job_runner.py:988` | `if exchange_checkpoint is not None:` ／ `exchange_checkpoint=persist_exchange, remaining_steps=...` | `exchange_checkpoint`（原生轨迹持久化）**只有工作运行器传参**，普通对话传 `None`。 |
| S2-02 段内追加 | 部分实现（仅单轮内） | `src/len_bot/cognition/agent_loop.py:214`；`src/len_bot/cognition/agent_loop.py:560` | `trajectory = copy.deepcopy(messages)` ／ `trajectory.extend(additions)`（`observe` 吸收新输入） | 段内追加只存在于**一次 run 之内**；run 结束 trajectory 就丢弃。 |
| S2-02 每轮请求的原料读取 | 已实现 | `src/len_bot/runtime/agent_runtime.py:1048`；`src/len_bot/events/store.py:492` | `recent = await self.event_store.get_recent_events(session.scene_id,limit=self.config.conversation_history_limit,through_rowid=cutoff,conversation_only=True)` | 逐轮按 `conversation_history_limit` 条重查，无跨轮复用。 |
| S2-02 请求装配顺序稳定（前缀友好） | 部分实现 | `src/len_bot/cognition/context.py:28`（doc）／`src/len_bot/cognition/context.py:1005`；`src/len_bot/cognition/context.py:22` | `stable（当前只有 persona）→ window（recent_history，按保存时序）→ brought_in → volatile` ／ `"""Order initial carriers only; never reorder native tool exchanges."""` ／ `STABLE_SECTIONS = {'persona'}` | 只有 persona 是稳定段；摘要／偏好因引用同一套 M 编号无法前置（源码注释明说）。 |
| S2-02 编号不因新消息重排 | 已实现 | `src/len_bot/cognition/context.py:1579-1596` | `Claiming them here, oldest first, means a new message takes the next free number and nothing already in the window moves.` | 仅覆盖事件／人物编号；未覆盖供应商前缀整体一致性。 |
| S2-03 历史压缩唯一入口 | 已实现 | `src/len_bot/runtime/agent_runtime.py:902`；`src/len_bot/memory/reflection.py:37` | `async def _maintain_history(self, scene_id: str, *, quiet=True, retry_batch=None, claimed=False)` ／ `async def maintain_batch(` | 入口唯一，由 `_maintaining_history_scenes` 集合互斥；未验证是否所有调用方都经此路径。 |
| S2-03 压缩产物落表 | 已实现 | `src/len_bot/memory/history.py:93`；`src/len_bot/memory/history.py:368` | `CREATE TABLE IF NOT EXISTS history_batches (` ／ `UPDATE history_batches SET status='completed',summary=?,` | 表名为 `history_batches`（**不是** segment 表）；摘要存于此，不新开 transcript。 |
| S2-03 压缩与未完成事项（JobStore／work_context）交接 | 部分实现 | `src/len_bot/cognition/context.py:1639-1681`；`src/len_bot/cognition/context.py:1268` | summary 装填后另装 `install_facts`（work／tasks／open_loops／outbound 目录） | **两者是并列装配，不是交接**：压缩本身不读 JobStore，未完成事项靠 `runtime_facts` 独立目录带入；没有“压缩附带未决清单”的确定性产物。 |
| S2-03 工作侧独立压缩 | 已实现（工作域） | `src/len_bot/runtime/work_context.py:198`；`src/len_bot/runtime/work_context.py:205` | `class WorkCompressor:` ／ `async def prepare(self, messages, tools, *, reserved=()):` | 有完整工具交换边界（`exchange_spans`）与大正文外置；**这是 S2-03 最可直接复用的既有结构**。 |
| S2-04 窗口头锚定 | 已实现 | `src/len_bot/cognition/context.py:1012`；`src/len_bot/cognition/context.py:1019` | `def anchor_window_start(self, messages):` ／ `boundary = ((min(message['_source_rowid'] for message in window) + step - 1) // step) * step` | 每轮重算再对齐，不是保存上轮窗口头；doc 明说“可能呈锯齿形”。 |
| S2-04 超出后丢弃什么 | 已实现 | `src/len_bot/cognition/context.py:1023`；`src/len_bot/cognition/context.py:1045` | `dropped = [message for message in before_boundary if message['_source_event_id'] not in self.required_originals]` ／ `messages.remove(message)` | 三重条件下才裁：step 非零、非空非全窗、**每条都有已完成摘要的 `_summary_complete_ids` 覆盖**。 |
| S2-04 被丢原话如何取回 | 已实现 | `src/len_bot/cognition/context.py:1042-1044`；`docs/context.md:147-153` | `message['_context_section'] = 'related_original'`（必需原话改归 brought_in，不删）／ `read_context`／`read_message_range`／`recall_chat` | 机制存在；“摘要只定位、精确引述回读”是文档级合同，未做运行验证。 |
| S2-04 已读范围按最终请求重建 | 已实现 | `src/len_bot/cognition/context.py:657`；`src/len_bot/cognition/context.py:682` | `def reconcile_original_reads(self, messages):` ／ `def confirm_original_reads(self):` | `confirm_*` 只在模型真实返回后调用（`social_core.py:277` 的 `after_model`）。 |
| S2-05 mailbox（段身份＋取消） | 部分实现 | `src/len_bot/cognition/mailbox.py:4`；`src/len_bot/cognition/mailbox.py:35` | `class EpisodeMailbox:` ／ `def cancel(self, reason: str = "Explicitly cancelled") -> None:` | 有身份与取消位，但 `episode_id` 每轮随机，取消位仅存活于单次 run；取消触发点见下表二。 |
| S2-05 gate（提交与取消核对） | 已实现 | `src/len_bot/runtime/gate.py:154`；`src/len_bot/runtime/gate.py:226` | `async def evaluate_and_commit(` ／ `if mailbox.is_cancelled():` | 取消后拒绝提交；`knowledge_revision` 变更同样拒绝。 |
| S2-05 resume（等待后恢复） | 已实现 | `src/len_bot/cognition/models.py:28`；`src/len_bot/cognition/social_core.py:324` | `class ConversationResume(BaseModel):` ／ `outcome.resume_state=ConversationResume(episode_id=episode_id,runtime_started_at=runtime._started_at,` | 只保存来源、预算、消息数、期限、`result_ids`；**不保存 trajectory**（docstring 明说“not a provider trajectory”）。 |
| S2-05 重启后未完成输入处理 | 已实现 | `src/len_bot/events/store.py:924`；`src/len_bot/runtime/agent_runtime.py:1156` | `async def mark_suspended_conversations_for_review(self):` ／ `raise SceneCommitConflict('Suspended conversation belongs to a previous process; review is required, no request is resent')` | 旧进程的等待置 `review_required`，不自动重放；新输入仍走 `pending_wakes`。 |
| S2-05 open_loops | 已实现 | `src/len_bot/events/store.py:108`；`src/len_bot/events/store.py:892` | `CREATE TABLE IF NOT EXISTS open_loops (` ／ `async def get_active_open_loops(self, scene_id: str) -> list[dict[str, Any]]:` | 等待关系持久；`resume_state` 实际从关联事件的 metadata 读取（`store.py:898,917`），不是表列。 |
| S2-05 并发接收新输入（已读≠已处理） | 已实现 | `src/len_bot/runtime/agent_runtime.py:1184`；`src/len_bot/runtime/agent_runtime.py:1212-1214` | `async def observe(*, provided_ranges=None):` ／ `handed = tuple(prior.ranges) if prior is not None else ()` ／ `if offered.get(wake.event_id) == handed: continue` | play 中追加输入只提供“未覆盖区间”；重复区间不重装。 |
| S2-06 纠正→记忆修订 | 已实现 | `src/len_bot/memory/writes.py:47`；`src/len_bot/memory/models.py:57` | `if mp.operation == "refute":` ／ `operation: Literal["create", "refute", "supersede"] = "create"` | 记忆侧 `supersede`／`refute` 完整，旧版保留可查。 |
| S2-06 纠正→上下文失效（连接） | 部分实现（仅弱连接） | `src/len_bot/cognition/context.py:1437`；`src/len_bot/scenes/actor.py:309` | `async def install_preferences(self, messages):`（每轮按当前有效项重读）／ `if state.knowledge_revision != item.knowledge_revision: raise SceneCommitConflict(...)` | 连接只有两条：偏好投影每轮重读 + `knowledge_revision` 变化使在飞的 turn 失败重建。**没有**“修订即失效已装入摘要／缓存段”的机制。 |
| S2-06 检索候选按有效版本过滤 | 已实现 | `src/len_bot/tools/retrieval.py:1265`；`src/len_bot/tools/retrieval.py:1314` | `include_superseded=args['include_history'], limit=candidate_limit,` ／ `memories = [item for item in latest if item.revision == candidates[item.id]]` | 候选截取前后都按 `include_superseded`／revision 复核；默认不含旧版本。 |
| S2-06 scope（可见范围去重／失败显式） | 已实现 | `src/len_bot/memory/store.py:108`；`src/len_bot/memory/store.py:179` | `if not include_superseded:` ／ `if not include_superseded:`（两处按 scope＋状态过滤） | 查询按 `allowed_scopes` 限定，未启用跨群共享时本群隔离。 |
| S2-06 cached_tokens 落库 | 已实现 | `src/len_bot/cognition/call_store.py:595`；`src/len_bot/web/query_service.py:430` | `("cached_tokens", "prompt_tokens_details", "cached_tokens")` ／ `("cached_tokens","prompt_tokens_details.cached_tokens")` | 从供应商 `usage` 原样读取并按正数／显式零／缺失区分（`docs/context.md:248`）。 |
| S2-06 cached_tokens 与上下文策略绑定 | 未找到 | `src/len_bot/config.py:52-58`；`docs/context.md:41,221` | `conversation_recent_tokens`／`conversation_window_step_rowids`／`conversation_window_seconds` | 无缓存 TTL／保温配置项；文档明确“这不是供应商缓存承诺”“不是跨轮缓存”。策略与缓存只有**设计意图**关联，无代码绑定。 |

---

## 二、逐轮重建的实际路径（S2-02 主函数）

普通群聊一轮请求的真实入口链：

1. `AgentRuntime._run_conversation`（`src/len_bot/runtime/agent_runtime.py:1113`）分配轮身份：
   `episode_id = resume.episode_id if resume else f"conversation:{uuid.uuid4().hex}"`（`:1119`）。
2. `_read_initial_window`（`:1036`）定 `cutoff`，从 `pending_wakes` 取来源，按 `conversation_read_batch_limit` 取 `required_ids`，再 `get_recent_events(limit=conversation_history_limit)` 取近期原料；`recent_ids` 作为独立集合传出（`:1048-1054`）。
3. `SocialCognitionCore.run`（`src/len_bot/cognition/social_core.py:27`）新建 `ConversationContext(runtime,session,through_rowid)`（`:60`），无上一轮对象入参。
4. `ConversationContext.build`（`src/len_bot/cognition/context.py:1491`）逐段拼装：persona（`:1548-1555`）→ 当前时间（`:1610`）→ 必需来源 `pack_events`（`:1613`）→ runtime_facts／偏好（`:1618-1621`）→ 其余近期历史 `pack_events`（`:1626`）→ 摘要候选（`:1658-1679`）→ 人物参考／媒体目录／表达样例（`:1683-1745`）。
5. `AgentLoop.run`（`src/len_bot/cognition/agent_loop.py:181`）在内存里 `trajectory = copy.deepcopy(messages)`（`:214`），段内用 `observe()` 追加新输入（`:560`）；run 返回后 trajectory 不再被任何普通对话路径写库。

**结论**：所谓“段内追加”当前只等于“一次 run 内的 trajectory 增长”；跨轮只能靠数据库重读事件＋摘要重建，因此任务卡把普通对话假设为“逐轮重建”**成立**。

---

## 三、未确认与读不到

- `src/len_bot/migrations/` **只有 `__pycache__`，没有任何 `.py`**（`glob` 确认，2 个条目均为目录）。所有建表都在各 `initialize_*` 方法里就地 `CREATE TABLE IF NOT EXISTS`，**没有独立迁移输入输出约定**，S2-01 要求的“迁移输入输出约定”无处可挂在现有结构上。
- 未打开 `len_bot.db`，因此**未确认**实际库中 `history_batches`、`open_loops`、`scene_sessions` 的行数与历史数据形态，也未确认旧数据是否缺轨迹。
- 未运行任何回放或装配，**未核实** `anchor_window_start` 在真实数据上是否触发裁剪、`window_anchor/summary_does_not_cover_original` 出现频率（`docs/context.md:105` 有口径，无现场数据）。
- 未核实 `conversation_resume` 事件在 `wait` 之外还有哪些生产触发点；仅确认 `actor.py:213` 一处写入。
- 未确认 `mailbox.cancel()` 的**全部**生产触发点：`grep` 只找到 `actor.py:79,123`、`agent_runtime.py:476,512`、`plugin_interactions.py:107` 五处（Runtime 停止、调用方取消、群被停用、插件运行取消）；是否有“修订／权限撤销”触发未确认。
- 未核实 `STABLE_SECTIONS` 注释所称“摘要与偏好因共用 M 编号无法前置”是否已有未合并的解决分支。
- 未核实供应商侧 `prompt_tokens_details` 的实际返回形状（`gateway.py:167-173` 只做 `dict` 校验），因此“字段缺失＝未报告”与“字段缺失＝未命中”无法在源码层区分。
- 未读 `docs/LenBot_群聊体验与可靠执行_完整改造计划_20260920.md`（任务未要求，且与 S2 段落假设可能重叠）。

---

## 四、与任务卡原假设的差异

1. **“逐轮重建”假设仍然成立，且源码有显式注释确认。** `ConversationContext` 每轮新建（`social_core.py:60`），`ConversationResume` 的 docstring 直接写 `"A sent wait retains its request and budget, not a provider trajectory."`（`models.py:29`），`exchange_checkpoint` 只有工作运行器传参（`job_runner.py:988`，唯一调用点）。任务卡与路线书第 4 节的前提**没有被证伪**。
2. **S2-01 的“段”确实是全新的。** 无 `segment`／`episode` 持久表；`episode_id` 是每轮 UUID（`agent_runtime.py:1119`），不能承担“段身份不依赖全局 rowid 取模”的要求。`scene_sessions` 只是每场景一行状态（`store.py:102`），字段里没有段身份、装配版本、已接入截点、摘要引用。
3. **S2-03 已有最完整的前置结构，不是全新设计。** `history_batches`（`history.py:93`）已是唯一压缩产物表，`_maintain_history`（`agent_runtime.py:902`）已是唯一入口且带互斥与重试；工作域的 `WorkCompressor`（`work_context.py:198`）已实现“完整工具交换边界 + 大正文外置 + 区间压缩 + 原资料回读”。S2-03 的实际增量主要是：把工作域的压缩纪律搬到对话域，并补“宿主确定性附带未决事项”这一半——目前 `install_facts`（`context.py:1426`，其 `facts_message` 在 `:1268`）只是**并列**装配，压缩本身不产出未决清单。
4. **S2-02 的“稳定渲染”已有相当多实现。** 编号 oldest-first 不重排（`context.py:1579-1596`）、`_initial_context_order` 不重排原生工具交换（`:1005`）、persona 独立稳定段（`:22,1555`）、窗口锚定（`:1012`）都已存在。真正的缺口是**跨轮 continuation 的原生交换本身**，而非渲染顺序。
5. **S2-04 的窗口锚定与丢弃已实现，且比任务卡描述更细。** 裁剪需三重条件（`:1017-1039`），被丢的原话若本轮必需会**改归 `related_original` 而不删**（`:1042-1044`），`reconcile_original_reads` 按实际保留载体重建已读（`:657`）。这三项与任务卡“已读不等于已处理”“恢复不复发已发送行为”方向一致。
6. **S2-05 的 mailbox／gate／resume／open_loops 四个符号全部已存在**，且重启边界明确（`mark_suspended_conversations_for_review`，`store.py:924`；新进程拒绝重发，`agent_runtime.py:1156`）。缺口在于它们只服务**单轮等待恢复**，不提供“段级”并发边界。
7. **S2-06 是本组最弱的一项。** cached_tokens 有完整落库与三种口径区分（`call_store.py:595`、`query_service.py:430`），但**没有任何配置或代码把缓存策略与上下文装配绑定**（`config.py` 无缓存字段，`docs/context.md:41,221` 反复声明“不是跨轮缓存”）。纠正与上下文的连接只有 `install_preferences` 重读（`context.py:1437`）＋ `knowledge_revision` 冲突拒绝（`actor.py:309`、`agent_runtime.py:1190`）两条弱连接，**没有**“记忆修订即失效已装入摘要／派生缓存段”的实际机制——正是任务卡 S2-05 要求补的那一段。

**总判断**：S2 六项中，S2-04 大体已有、S2-03 与 S2-02 有大量可复用结构（压缩表／入口／工作压缩器、稳定渲染与锚定）、S2-05 符号齐全但粒度是单轮、S2-01 是真正的新增、S2-06 的记忆—上下文失效连接基本空白。整体是**“在已有结构上补一段”**，但补的那一段（段身份 + 跨轮原生轨迹续接 + 压缩附带未决清单）确实不存在。

---

## 五、反向引用清单

**源码**
- `src/len_bot/cognition/context.py:22`（`STABLE_SECTIONS` 注释）、`:28`（装配顺序 doc）、`:232`（`ConversationContext`）、`:657`／`:682`（已读重建／确认）、`:1005`（`_initial_context_order`）、`:1012`（`anchor_window_start`）、`:1268`（`facts_message`）、`:1437`（`install_preferences`）、`:1491`（`build`）、`:1579`（编号不重排）
- `src/len_bot/cognition/social_core.py:27`（`run` 签名）、`:60`（新建 context）、`:275-277`（`checkpoint`／`after_model` 确认）、`:324`（`ConversationResume` 构造）、`:378`（`AgentLoop` 调用）
- `src/len_bot/cognition/agent_loop.py:181`（`checkpoint`／`exchange_checkpoint` 形参）、`:214`（`trajectory`）、`:521`／`:560`（轨迹检查点／追加）
- `src/len_bot/cognition/models.py:28`（`ConversationResume`）、`:229`（`EpisodeOutcome`）
- `src/len_bot/cognition/mailbox.py:4`（`EpisodeMailbox`）
- `src/len_bot/cognition/input_window.py:30`（`original_remainder`）
- `src/len_bot/cognition/projection.py:60`（`project_event`）、`:104`（`pack_recent_chat`）
- `src/len_bot/cognition/call_store.py:595`（cached_tokens）
- `src/len_bot/runtime/agent_runtime.py:902`（`_maintain_history`）、`:1036`（`_read_initial_window`）、`:1113`（`_run_conversation`）、`:1119`（`episode_id`）、`:1156`（新进程拒绝重发）、`:1184`（`observe`）、`:1234`（`commit`）、`:1401`（`_save_conversation_trace`）
- `src/len_bot/runtime/gate.py:154`（`evaluate_and_commit`）、`:226`（取消拒绝）
- `src/len_bot/runtime/work_context.py:133`（`archive_trajectory`）、`:198`（`WorkCompressor`）
- `src/len_bot/runtime/job_runner.py:372`（`restore_trajectory`）、`:988`（唯一 `exchange_checkpoint` 传参）
- `src/len_bot/runtime/job_store.py:328`（`job_exchanges`）、`:891`（`checkpoint_json`）、`:932`（写检查点）
- `src/len_bot/scenes/models.py:76`（`SceneSession`）、`src/len_bot/scenes/actor.py:213`（写 `conversation_resume`）、`:309`（知识修订冲突）
- `src/len_bot/events/store.py:102`（`scene_sessions`）、`:108`（`open_loops`）、`:492`（`get_recent_events`）、`:828`（resume 消费等待）、`:892`（`get_active_open_loops`）、`:924`（`mark_suspended_conversations_for_review`）、`:1800`（`recover_task_execution`）
- `src/len_bot/memory/history.py:93`（`history_batches`）、`:135`（`begin_history_batch`）、`:296`（`commit_history_batch`）、`:383`／`:389`（查询）
- `src/len_bot/memory/reflection.py:26`（`ReflectionEngine`）
- `src/len_bot/memory/store.py:108`／`:179`（superseded／scope 过滤）
- `src/len_bot/memory/writes.py:47`（refute 分支）
- `src/len_bot/memory/models.py:57`（`create/refute/supersede`）
- `src/len_bot/tools/retrieval.py:1265`／`:1314`（候选与复核）
- `src/len_bot/config.py:52-58`（对话容量与锚定参数）
- `src/len_bot/web/query_service.py:429`（cached_tokens 汇总）

**文档**
- `docs/LenBot_分阶段任务卡_20260921.md:342-474`（S2-01—S2-06）
- `docs/LenBot_成熟开源项目路线书_20260921.md:161-231`（第 4 节第 4.1—4.6 小节）
- `docs/context.md:7`（逐轮重建声明）、`:12-41`（来源与顺序）、`:83-121`（锚定与参数）、`:125-143`（已读以最终请求为准）、`:197-217`（长工作检查点与压缩）、`:219-248`（缓存与诊断边界）
- `docs/architecture.md:241`（已读账本按最终请求体核对）、`:335`（图片准备复用与 cached_tokens 无关）
- `docs/plan/README.md`（状态定义与读取顺序）
- `docs/iteration.md:179`（cached_tokens 实际观察口径）
