# 当前任务

## 2026-09-18：让前缀可复用，然后用它换掉抽签

上一轮把窗口装满了，这一轮发现装满的东西**一次都没被复用过**。中转面板显示约一半调用没有缓存行，命中的那些是 87–94%。

### 三个各自足以致命的原因

**编号每轮整体平移。** `TurnReferences._register` 按注册顺序发号，本轮新进 3 条消息就把后面每条的编号顶高 3 位（实测 M69→M72、M170→M173，280 个跨轮共有事件 **280 个全变**）。编号写在消息正文里（`{"ref":"M170",...}`），于是窗口里每条历史的文本都变了，前缀在第 1 条就断。上一轮把这条记成"摘要定位符的问题"，**判轻了**——它破坏的是 24–31k 的原话窗口本身。

**窗口有洞，洞会移动。** `pack_events` 里可选消息装不下就跳过去试更旧的，大的塞不进、小的塞得进，窗口成了带洞的集合，洞随每轮剩余预算变化（实测两轮之间同一段落缺失的消息完全不同）。

**窗口起点逐轮滑动。** 按预算从最新回填，每来一条消息起点前移一格。

### 改法

编号改为 `M<rowid>`，由事件自身决定，与集合构成脱钩；`_register` 接受首选编号并在冲突时探测空位。人物编号改用 `SceneSession.participants`（持久、只增不删、保序），回来的人保留原号。可选消息第一条装不下即收尾，窗口成为连续区间。锚点从候选层（被预算架空）移到装填后的**实际窗口**起点，按 `conversation_window_step_rowids` 网格向上取整。窗口单独成区，排在引用/回捞带进来的旧消息之前，避免它们插进窗口头部。被锚点丢掉的消息撤销"已读原话"标记但保留定位符。

| | 跨轮编号稳定 | 相邻请求公共前缀 |
|---|---|---|
| 改前 | 0 / 280 | 1 条（1%） |
| 连续窗口 + `M<rowid>` | 248 / 253 | 76% / 1% |
| 放宽窗口 + 步长 800 | **300 / 300** | **74% / 82%** |

配置：`conversation_context_tokens` 120k→200k、`conversation_recent_tokens` 32k→56k、`conversation_window_step_rowids` 200→800、`conversation_history_limit` 200→400。实测窗口 313 条 / 42k（每条均 134 token），请求合计 68k。锚点跨步约每 **75 条消息**一次（10.3–11 全局 rowid 一条消息）；历史摘要批次也是每 69–91 条一批，两条线节奏基本同步。

### 为什么"发几条消息"仍要付几千 token

前缀之后的尾巴每轮重建。按真实 token 数逐段测变化率（678 个相邻对）：

| 段落 | 变化率 | 平均多少轮变 | 均值 token |
|---|---|---|---|
| persona | 1% | 96.9 轮 | 4,702 |
| reference | 31% | 3.2 轮 | 3,180 |
| history_summary | 28% | 3.6 轮 | 2,401 |
| runtime_facts | 28% | 3.5 轮 | 1,677 |

把这三块前移到窗口之前的想法**被这次测量否决**：盈亏平衡点约 7 轮（前置块一变，后面整个窗口跟着失效），而它们 3 轮出头就变一次。期望新增约 **9,000 token/轮**（图片 2,030 + 上一步工具往返 2,470 + 本轮原话与状态约 1,800 + 上述三块摊销约 2,100 + 零碎），跨步摊销另加约 4,100。

一个方法上的教训：`traces` 的 `request.messages` 只记 role/section/event_id/ref，**不记内容**，单条无 event_id 的块比出来永远"相同"。我先按它得出"这三块从不变"的结论并准备据此改动，用 `section_tokens` 复测才发现是假象。

### 关掉抽签，回到一直判断

抽签存在的唯一理由是"每轮 40k 全价看不起"，前缀可复用之后理由没了，而它决定得很差。两个实例：

- 16:13:45 "如果现在配台 78x3d+5060ti 的电脑怎么样"——`attention_reasons` 为空。bot 在 16:13:02–05 连答三个 @ 它的人，每条扣 0.3，参与度 1.0→0.1，40 秒后这条只剩 0.8×0.1 = 8% 的概率被看一眼。**额度是被"履行义务"花掉的。**
- 16:35:20 "不是变矮吗"——直接接着 bot 自己那句话玩梗，同样零理由。发话人是第三方，`continuing_interaction` 只认 bot 刚回应过的人；现有理由里没有"bot 刚说完、有人紧接着接话"这个信号，抽样是唯一兜底，没中。

参与度整套删除（`engagement()`、`_set_engagement`、发一条扣一档、三处概率乘子、两个配置旋钮）。`SceneSession.engagement_level`/`engagement_at` 保留但退役——`extra='forbid'`，删字段会让已存会话读不进来。任何理由成立即唤醒；无理由的普通消息由**主动观察**兜底：每群每 `attention_sample_window_seconds`（现 60 秒）最多读一次，一次读取携带自上次以来的全部消息。`attention_sample_probability` 从概率降级为开关（0 = 该群不观察），以此保住每群覆盖与面板，不整体拆掉那套配置。面板文案与密度公式（`3600 ÷ 间隔`）同步改，dist 已重建。

### 历史摘要停了 3–14 小时

`begin_history_batch` 见到任何非 completed 批次即返回 None。五个活跃群**每个都有一条失败批次正好压在完成边界的下一段**：

| 群 | 完成到 | 失败区间 | 错误 | 卡住于 |
|---|---|---|---|---|
| 992584358 | 21,593 | 21,597–21,900 | RateLimitError | 02:30 |
| 1078114081 | 21,836 | 21,837–21,980 | APIConnectionError | 11:59 |
| 1102823315 | 27,960 | 27,964–28,430 | APIConnectionError | 12:08 |
| 1042218062 | 28,529 | 28,530–28,785 | APITimeoutError | 12:22 |
| 126300994 | 30,211 | 30,214–30,482 | APIConnectionError | 13:16 |

累计 6,890 条既不在摘要也不在窗口——对 Bot 完全不存在。"不自动重试"是有意设计（重跑会再买一次调用，也会让同一段对话产生两份说法），但阻塞是完全的且**全程静默**。已清除这五条让前沿恢复推进，并给阻塞加 WARNING（按每个新阻塞点一次，写明群、批次、错误与 rowid 区间）；设计本身不动，运行手册补了处置步骤。

### 实测（16:43:58 重启，窗口仅 1.9 分钟）

51 条消息、车道 none 24 / slow 25 / fast 2；理由 `in_flight_follow_up` 22、`sample_opportunity` 4、`mention` 2、`address_name` 2。`engagement_level` 已不再写入。对话 2 轮 / 4 次调用 / 0 条发言。新摘要批次 2 条，摘要恢复推进。WARNING 与 ERROR 均为 0。

### 未确认

- **观察间隔没有得到检验**：窗口只有 1.9 分钟，四个群各只触发 1 次，量不出重复间隔是否守在 60 秒。
- `certain = bool(reasons)` 回来了，而那次 129 条连发正是它造成的。现在压住它的是小时限额、重写过的人设与模型判断，**尚无运行数据**。本窗口 `in_flight_follow_up` 已触发 22 次。
- 缓存改善只能看中转面板，`usage_json` 不回传 `cached_tokens`（只有 prompt/completion/reasoning）。74–82% 是本地前缀测量，不是命中率。
- 6,890 条摘要积压约 88 批会集中补；若再撞上上游 503，对应群会重新被卡，区别只是这次有日志。
- 图片每轮按 1024/张重付约 2,030、上一步工具往返约 2,470，两块都还没压。
- 引用带进旧消息、以及长引用按剩余预算截断（`quote_tokens=cap`），仍可能让同一事件的文本逐轮不同；未处理。
- 上游 503 `No capacity available for model gemini-3.8-flash-high`、429 配额与 502 换模型，均属中转侧，未处理。

## 2026-09-18：12 分钟窗口的审计结论、机会积压与执行缺口

14:08 重启、14:20:42 干净关停，实测窗口 12.5 分钟：8 个 episode、34 次 conversation、187 条群消息、**0 条发言**、1 次插件图片投递 `delivery_unknown`。样本小，但足以判上一轮的判据。

**已确认修好。** 失败率按图片数：6 图 74.9% → **3.0%**（29 次 1 次失败，是 APIConnectionError 不是体积）。同群相邻工具块 69% 不同 → **18%**（23/28 相同），剩余差异是 `1409`（插件精简轮）与 `9993/10581`（`revise_work` 一类条件披露），本轮数据不再进 schema。日志出现 `openai._base_client: Retrying request`（有限重试生效）与 `Media read failed: asset=… http=400`（此前零日志）。3 条 `address_name` 未触发任何限流提示。沉默理由改为「无具体接话点」「无合适想法或梗切入」，不再是「没人在跟我说话」——仅 3 条样本。

**上一轮的记忆结论是错的。** 原话窗口的闸门是 `conversation_recent_tokens`，不是 `conversation_context_tokens`。后者 50k→120k 之后 `estimate.parts.history` 均值只从 **20,032 → 21,028**（+5%）；多出的预算被图片吃掉（每轮 2.9 → 5.9 张），单轮真实输入 33.5k → 38.0k。「吐槽完矢口否认」没有解决。根因是图片按 1024/张计入同一条原话额度：六张图占掉 20k 中的 6k。本轮把图片移出该额度（仍受张数、字节与请求预算三处限制），并把 `conversation_recent_tokens` 20000 → 32000，使 200 条候选（实测均 ~116 token/条）能整体装入。

**机会积压是第二个、更大的成因。** `pending_wakes` 02:28 为 7 条、13:43 为 293 条、关停时 321 条（另有 241/127/126 三个群）。被限额或抽样拦下的普通机会不消费待处理集合。这些群一旦拿到轮次，200 条候选里 115 条当场作为 `original_input/no_capacity` 丢弃，`remaining_raw` 归零，`recent_history` 一条装不进——原话窗口从约 178 条塌到约 21 条。按小时的省略中位数是干净的：13 点前 `original_input` 为 0，13/14 点为 114/115。

### 本轮改动

| | 改了什么 |
|---|---|
| 机会过期 | `PendingWake.created_at`；`attention_opportunity_ttl_seconds`（默认 600）之后关闭非义务类唤醒。@／回复／私聊／等待中的答案／工作参与者／`runtime:*` 不受限 |
| 原话额度 | 图片不再计入 `pack_events` 的 raw 额度，`original_media` 也不再缩减 `remaining_raw`；`conversation_recent_tokens` 32000 |
| 名字降级 | `address_name` 移出 `ADDRESSED_REASONS`，改为每 `attention_keyword_cooldown_seconds` 一次的机会，不重置参与度、不冲洗合并窗口；`keyword_opportunity` 也乘参与度 |
| 限额一致 | 新增 `_chat_ceiling_applies`，入口闸门与 `_eligible_conversation_events` 共用。此前 `AGENT_JOB_CHECKPOINT`(349)／`TASK_DUE`(105)／`AGENT_JOB_FINISHED`(40) 都带 `interaction='chat'`，会被第二道网取消 |
| 租约 | 快照移入 `try`；`finally` 中先释放租约再记账 |
| 空来源 | 资格过滤后无有效来源即结束本轮，不拿旧历史开模型调用 |
| 图片字节 | 单张也受 `media_context_max_bytes` 约束；`synchronize_image_window` 增加必填 `max_bytes`，工作侧 7 处调用全部覆盖 |
| 工具顺序 | 注册新增与 `kind` 正交的 `ordered`；工作区五个工具声明后在 AgentLoop 中串行 |
| 配置化 | `attention_engagement_step` / `attention_engagement_recovery_seconds` / `attention_opportunity_ttl_seconds` 进根配置，不再是代码常量 |

其中「机会过期」与「限额一致」「租约」「空来源」「图片字节」「工具顺序」仍然有效；**「名字降级」的冷却保留但不再乘参与度，「配置化」的前两个旋钮与整套参与度已在下一轮删除**，改法与理由见上一节。

### 实际核对

只核对了**静态**与**只读**两类，没有重新启动：全部模块导入无错；真实库 6 个群的 `scene_sessions` 用新模型载入正常，新字段取默认值；按新的关闭规则对真实待处理集合做只读推演，818 条收敛为 13 条，保留项理由全是 `mention`／`reply_to_bot`／`runtime:reflection_recorded`。

### 未确认

- **以上全部改动尚无运行数据。** 编译与只读推演不是运行通过，需要一次获授权的真实启动。
- 零发言是否由积压造成仍未证实：窗口内 186 个事件的 `engagement_level` 全是 1.0（一条没发出去，衰减从未启动），所以参与度机制本轮**没有得到检验**。
- 空来源轮次在 831 条 trace 里出现 **0 次**；该防护是按代码路径加的，没有实例。
- 工作区工具在全部数据里一次都没被调用过，`ordered` 同样没有实例。
- 图片字节闸门在窗口内一次未触发（JPEG 后 6 张远低于 3 MB），目前是休眠保险。
- group:1078114081 一次 `FreshInputConflict` 报废整条 episode，12 次已完成调用零提交；未处理。
- 每轮调用数 1.29 → 4.25（失败修好后必然上涨），12.5 分钟 1.18M 输入 token 换 0 条发言。缓存是否改善仍只能看中转面板，`usage_json` 拿不到 `cached_tokens`。
- 原话窗口起点仍逐轮滑动，那段仍不命中缓存；锚定需要持久化的窗口起点与脱离 M 编号池的定位符，均未做。
- 上游 429（`Individual quota reached`）与 502 里中转把 gemini 换成 `deepseek-v4.1-flash`，未处理。

## 2026-09-18：图片编码、有限重试、参与度节奏与缓存前缀

12 小时实测（重启窗口 02:28–13:10）暴露三件上一轮限额没碰到的事；限额自始至终没触发（每群每小时峰值 16／上限 50，单人 7／上限 10）。

**图片。** conversation 失败 44.7%，同 provider 同模型的 `history_maintenance` 只有 4.8%。按图片数拆开单调上升（0 图 7.2%、6 图 **74.9%**），且在同一小时内成立（11–13 点 0 图 0%、6 图 81–87%），排除中转在抖。根因是 `media/service.py` 缩放后存**无损 PNG**：实测 40 张真实素材，6 张图的 base64 体积 p90 **13.6 MB**、最坏 37 MB，改 JPEG q85 后为 2.0 / 5.0 MB。`limit_image_window` 只按张数裁，token 估算按每张固定 1024 计——字节是预算系统看不见的维度，故增 `media_context_max_bytes`（默认 3 MB）。

**记忆。** 251 轮里 `recent_history` 因 `no_capacity` 省略 **6,803 次**，每轮中位丢 22 条。总量 ~42k 长期顶着 input_budget 45,904。用户报告「吐槽完马上矢口否认」，成因是它自己刚发的那条被挤出上下文。`conversation_context_tokens` 50,000 → 120,000。**该结论后被实测推翻**，闸门是 `conversation_recent_tokens`，见上一节。

**缓存。** 同群相邻调用工具块 69% 大小不同，根因是 `respond` 的 schema 每轮被写进当轮数据。三处已移除，兜底校验本就在 ProposalLedger 与 Gate，参数被拒返回可纠正回执而非废掉一轮。`build()` 末尾重排为 `[稳定] [原话按时序] [易变]`。

**节奏与人设。** `certain` 拆三档，新增 `SceneSession.engagement_level`（初值 1.0，发一条 −0.3，每 5 分钟 +0.3，被搭话重置 1.0）。根配置删「不确定就别回」与「别人发问号先别急」，沉默判据改为「没有具体想法才旁听」，自称由「喜欢」降为「偶尔」。新增 `own_recent_expression` 块携带自己近期开头与收尾。限流提示由 `attention_lane=='fast'` 收紧为只认 @／回复／私聊——群里聊真人嘉然会不断命中 `address_name`，而这条提示不经模型。

`max_retries` 0 → 2；`MediaService._media_error` 补 WARNING（此前 982 个资产 342 个没有本地文件而运维侧零日志）。

### 未确认

- 以上均为 14:08 重启后**尚未取得运行数据**的改动，审计窗口进行中。（已完成，结论见上一节。）
- 缓存是否真的改善**只能看中转面板的缓存列**，`usage_json` 拿不到 `cached_tokens`。若仍只在同轮第二步命中，说明该 provider 的缓存键不含 tools。
- 原话窗口仍按预算从最新回填，起点逐轮滑动，那 19k **仍不会命中缓存**。锚定需要两件事：窗口起点要有持久化位置（`build()` 不拥有 session），以及摘要／偏好的定位符要脱离与原话共用的 M 编号池。均未做。
- 上游 429（`Individual quota reached`）重试救不了；502 里出现请求 gemini 却被中转转给 `deepseek-v4.1-flash`。未处理。


## 2026-09-18：发言限额与跟读群

用户要求：群 50 条/小时、单人 10 条/小时；达到后不闲聊、不主动发言，但继续观察记录，今日直播与直播推送不受影响，@ 给一条说明。另加一个「不闲聊但一直旁听」的群模式。

### 先核对的两件事

限额初稿数值 150/30 高于实测峰值（群 86、人 16），不会触发；用户改为 50/10 后两项都在峰值之下。真正的开销是 24 小时 773 次 `conversation`、23.0M 输入 token（均次 29.8k），加 129 次 `history_maintenance`／2.38M。因此限额触发时**停的是模型轮次而不是发送**——轮次一开始 token 就已付出，只掐发送等于白花。

`chat=false` 原本已做到不闲聊、只记录、命令与推送照常，但 `scene_policy.maintenance_allowed()` 要求 `scene.chat`，于是历史总结与记忆也一并关掉。那是存档不是跟读，缺的第三档就是这个。

### 改动

限额计 Bot 自己真实发出的消息（`MESSAGE_SENT` 且 `delivery_status='sent'`、`origin_mode='live'`），滚动 60 分钟，直接查 `events`（复用 `idx_events_scene`），不加计数器表，重启自动正确。新 `runtime/rate_limit.py` + `EventStore.sent_message_counts()`。主闸门在 `agent_runtime.py` 的 `should_ingest_social` 旁边（事件是否进模型的唯一入口），第二道网在 `_eligible_conversation_events`，`interest_share` 槽超限直接取消。限额是独立谓词，**没有**并进 `ScenePolicy.chat_allowed`——后者还被 `plugins/host.py:666`、`job_runner.py:470` 和工作交付用着，混进去会连带掐掉成果交付。插件命令走 `plugin_consumed` 分支、推送 mailbox 是 `output_kind='plugin'`，两者本就不经过这条链，豁免是结构性的。

@ 提示不进模型，直接 enqueue 一条 `output_kind='chat'`、无 plugin_origin 的 ActionItem，每群/每人 10 分钟一条；睡眠窗口内不发（睡眠自己有措辞）。私聊整体豁免。

跟读群：`SceneSettings.listen`（默认 false，旧配置原样可读），`maintenance_allowed` 改为 `scene.enabled and (scene.chat or scene.listen)`。面板三档「聊天群／跟读群／仅播报群」，批量也是三档。面板里「旁听」已指抽样参数，故新模式称「跟读」，不复用该词。

### 实测（重启 02:28:40 后 ~1 分钟）

| 群 | 窗口内已发 | 重启后收到 | 重启后 conversation 轮次 |
|---|---|---|---|
| group:1078114081 | 136（>50） | 21 | **0** |
| group:992584358 | 38（<50） | 11 | 1 |
| group:1102823315 | 0 | 4 | 3 |

超限的群收到 21 条消息、一次模型都没跑；未超限的两群照常。省的是轮次不是发送，这一行就是证据。

日志里的 429 与连接错误来自上游模型供应商（`gemini-3.8-flash-high` 个人配额耗尽，约 2h19m 后恢复），与本批无关。

### 未做

跟读群尚未真实启用过（当前 6 个群都是 `chat=true`，`listen` 为空转）。`chat=false` 时 `attention.py:134` 仍在追加 `pending_wakes` 而消费端全被过滤，长期跟读群该列表可能持续增长，真正投用前要复查。`attention_sample_probability` 未动——那是降成本的另一个独立旋钮，等限额数据再说。

