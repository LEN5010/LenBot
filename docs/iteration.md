# 当前任务

## 2026-09-19：两小时真实流量的账，两个卡死的修复，和一条被推翻的旧结论

20:43:14—22:42:27 是配置改动（`reasoning_effort=low`、`conversation_max_concurrent=6`、`timeout=120`）之后第一段长流量，7 个群。收 4073 条人类消息，起 923 个对话轮次、1005 次对话调用，**实际发出 163 条**。输入 4260 万 token，缓存命中 35.9%。22:42:27 收到 SIGTERM，`Len Bot stopped cleanly.`，无残留进程。

### 插件的自主轮次全部提交失败，因为一个 set 进了 JSON

`interest_share` 这一段 23 次考虑**全失败**：容量不足 16 次、提交回滚 6 次、上游 502 一次。回滚那六次的原文是

```
Atomic proposal commit transaction failed and rolled back on Scene group:1014123451:
Object of type set is not JSON serializable
```

`plugin_interactions.py` 的 commit 回调把 `read_event_ids` 原样递给 `commit_turn`，而它是 `context.refs.read_events`——一个 `set`。`CommitCommand.source_event_ids` 标注是 `list[str]` 但只是普通 dataclass，不校验；这个 set 一路进到 `actor.py` 的 `payload['source_event_ids']`，在 `store.py` 的 `json.dumps` 上抛出，整个原子事务回滚。普通对话路径没炸，是因为 `agent_runtime.py` 的同名回调写的是 `sorted(read_event_ids)`。差别只有一个 `sorted()`。

影响范围不止 `interest_share`：**任何走 `run_agent` 的插件自主轮次，只要走到提交就必然失败**。这一段里凡是越过容量检查的七次，六次死在这里，一次死于上游 502——通过率 0。

容量那 16 次是插件自己的问题：它把完整 interest JSON 加最近 8 条发送各 1000 字塞进 `context_tokens`，在 5 万 token 的群语境上装不下。未修。

### 三个群的历史维护卡了整整两小时，而它同时是缓存和召回的前提

停机时 6 个群都压着未完成批次：3 条 `pending` 建于 20:15—20:17（上一个进程被 kill 留下），3 条 `failed/RateLimitError` 建于 22:03—22:15。前三条**跨越了本次运行的全程**——本次启动不接手未完成区间，而没有人去面板点重试。

后果可以直接量出来，锚定成败和缓存命中是同一条曲线：

| 群 | 锚定成功 | 锚定被挡 | 缓存 | 平均输入 | 摘要落后 |
|---|---:|---:|---:|---:|---:|
| 1042218062 | 93 | 3 | 66.9% | 56.4k | 279 |
| 1102823315 | 74 | 10 | 61.2% | 50.2k | 463 |
| 1090284567 | 46 | 11 | 56.4% | 31.0k | 127 |
| 992584358 | 67 | 19 | 51.1% | 52.3k | 284 |
| 126300994 | **2** | **189** | **17.0%** | 62.1k | 1595 |
| 1078114081 | **0** | **120** | **16.1%** | 65.5k | 3903 |

被挡住的两个群积压不但没追平，反而各涨了约 1200 和 1400 条（上一轮记的是 331 和 2458），而它们正是调用最多、缓存最低、输入最贵的两个。

命中比例是双峰的，没有中间态：完全没命中 361 次（54%）、只命中开头 12 次、断在中间 5 次、大部分复用 231 次、几乎整块复用 60 次。**那 361 次每次全价重付 5.76 万 token，合计 2079 万，占本阶段全部输入的 49%。**

修法在 `memory/history.py`：`begin_history_batch` 开头新增 `_resume_blocked_history_batch()`，遇到阻塞批次时按它自己的状态判断能不能就地转回 `pending` 继续。依据是 `commit_history_batch` 把摘要和 `status='completed'` 写在同一个事务里（异常整体回滚），所以**非 completed 的批次可证明没有写过摘要**，重做它产生不出第二份记述；而被拒的请求根本没花调用。可恢复集合是 `RateLimitError` / `APIConnectionError` / `APITimeoutError` / `InternalServerError` / `process_restart` 加上 `pending`。有界性复用这个函数里已有的模式：进程内按批次 id 去重，每个批次每进程只自动恢复一次，再失败回到原来的阻塞加告警，重启可再试一次。没有新增表、字段或状态。

原注释给的理由是「重试可能产生同一段对话的第二份摘要」——那个风险在这份代码里结构上不存在，注释已按实际约束重写。

在真实库副本上核过：6 条阻塞批次全部判定为自动恢复并转回 `pending`，同一进程内第二次调用 6/6 不再恢复。**这是副本上的判定核对，不是运行证据。**

### 撤回：不存在「约 3 分钟的隐式缓存 TTL」

上一轮由 14 次请求推出的那条结论，用这一段 772 次调用重测**没有复现**。全量按距上次调用的间隔分档，命中率是 <1 分钟 43.5%、1—2 分钟 59.0%、2—3 分钟 64.3%、3—4 分钟 55.6%——如果 TTL 主导，间隔最短的一档命中率应该最高，实际它最低。

按锚定是否成立做对照，差别立刻出来：

| 间隔 | 锚定能成立的 4 个群 | 锚定为 0 的 2 个群 |
|---|---:|---:|
| <1 分钟 | 78.1% | 17.8% |
| 1—2 分钟 | 72.2% | 14.8% |
| 2—3 分钟 | 75.0% | 0% |
| 3—5 分钟 | 60.0% | 0% |

同一个间隔差 4.4 倍，而锚定成立的群到 3—5 分钟还有 60%，三分钟处什么都没发生。**决定命中的是前缀稳不稳，不是隔了多久。** 上一轮那条结论及其在「中转链路实测」里的引用都已就地标注作废。

### 额度：22:00 打满上游，22:24 换的模型有 43% 的轮次作废

22:00—22:23 每一次对话都是 429（`Individual quota reached. ... Resets in 3h50m49s`），224 次对话全挂，同期 3 个群的维护批次也被打成 failed。CPA 只挂 1 个账号，当天该模型走了 2441 次请求、9435 万输入 token。

22:24:05 面板热切模型。切换前后是干净的对照：

| | gemini-3.8-flash-high | cline-pass/deepseek-v4.1-flash |
|---|---:|---:|
| 轮次 | 606 | 86 |
| AgentProtocolError | **0** | **37** |
| 平均输出 | 197 token | 1485 token |
| 平均推理 token | 3 | 1222 |
| 平均耗时 | 8.9s | 17.8s |

37 次协议错误（31 次 `Arguments for respond must be a valid JSON object`、6 次「本轮必须调用 respond」）**全部**发生在切换之后，切换之前一次没有。这些是调用已经付过、结果被运行时丢弃的轮次。后一个模型也没有遵守 `reasoning_effort=low`。

顺带核了中转的计费口径：`gemini-3.8-flash-high` 不在 new-api 的 `ModelRatio`、`CacheRatio`、`CompletionRatio` 任何一张表里，分别落到默认的 37.5、1、4。同族 gemini flash 的 `ModelRatio` 是 0.05—0.15，`CacheRatio` 是 0.1。**账面倍率高了两到三个数量级，且缓存命中按原价计费。** 这是中转侧的配置缺口，不影响上游真实消耗，运营者明确表示暂不处理，此处仅记录事实。

### 观察类调用占 85%，换来 69 次发言

按唤醒类型拆 658 个对话轮次：

| 唤醒类型 | 轮次 | 发言 | 沉默率 |
|---|---:|---:|---:|
| 被搭话 @／回复／点名 | 91（13.8%） | 87 | **4.4%** |
| 观察期内接话 | 439（66.7%） | 59 | 86.6% |
| 纯周期观察 | 120（18.2%） | 10 | 91.7% |

每轮输入 63.5k 的构成：`history` 43.1k、`system_reference` 9.4k、`tool_definitions` 9.3k、图片 1.7k。后两段是每轮固定重发的 18.7k。

最大头那 439 轮的来源是 debounce 太短。`observing_debounce_idle_ms=800`，而活跃群平均 5.1 秒才来一条消息——每条消息等 0.8 秒没有下一条就单独发车。`group:1078114081` 在 21:20:00—21:35:40 的 15 分 40 秒里收 183 条消息、开 52 轮、付 332 万 token、发出 30 条，平均每轮只合并 2.9 条来源。

同一个群 21:35 用满 50 条／小时的上限，之后 28 分钟内 548 次机会以 `reason='hourly_limit'` 拒绝。这是上一轮删掉固定提示后的预期行为，不是故障。

### 其余观察

- 媒体读取失败 22 次：超下载尺寸上限 18 次、超像素上限 2 次、400 两次。这些图模型直接看不到。
- 停机时 `pending_wakes` 共 55 条，没有重演 321 条压塌窗口。`observation`（`OriginalCoverage`）确实随 wake 进了请求，但**非空 `ranges` 依然是 0**——分批续读与证据重建连续两轮没有运行证据。
- 观察期在真实触发：`active_observation` 629 次、`in_flight_observation` 2141 次、`sample_opportunity` 1111 次。

### 语义检索与自主召回：索引不全，召回口子太窄，而且走的不是语义

两个开关都开着（embedding 绑定已配、7 个群 `semantic_retrieval` 全 true），增量索引也在跑，但**显式重建从来没执行过**：

| 范围 | 已索引 / 应索引 |
|---|---:|
| 历史摘要（全部群） | 193 / 278 |
| 认识（全部群） | 24 / 54 |
| `group:126300994` 摘要 | 39 / 117 |
| `group:126300994` 认识 | **5 / 35** |

最早的摘要建于 09-12、最早的认识建于 09-07，而最早的索引条目是 09-16。**积累最久的那个群，86% 的认识和 67% 的摘要搜不到**，缺的恰好是最久远的部分。

自动召回这一侧有两个独立问题。`seed_history_recall` 的触发是一条硬编码白名单，只有 `上午|下午|昨天|刚才|上次|那天|记得|那件事|三点|四点` 十个词；本段 4070 条人类原话里它命中 34 条，而同类但不在表里的词（以前／之前／上周／上个月／前天／当时／那次／说过／提过／聊过／去年／原来／当初／最早／第一次）命中 74 条，是现有的 2.2 倍，且恰好是指向更久远过去的那一组。更根本的是它的 `mode` 是 `local_literal`，调的是 `search_messages` 字面 FTS，**根本不经过 `memory_index`**。

语义索引只有两个入口，都要模型自己决定调用。这 2 小时 923 轮里模型调了 `query_memory` 1 次、`recall_chat` 1 次、`search_history_summaries` 0 次。`seed_history_recall` 自动触发 14 次，全部是字面搜索。

三层合起来的结论：**当前状态下不具备"隔一个月自主想起一个月前的事"的能力**，索引缺口、召回口子和检索方式各断一层。均未修。

### 回忆改成模型自己去取，不再靠关键词强推

`seed_history_recall` 的自动注入保留，但它不再是唯一的路，也不再假装自己完整。三处一起改：

- `search_history_summaries` 从对话角色的 `deferred_local` 里移出。它是**语义检索旧对话的唯一入口**，此前不在工具列表里，模型要先调 `tool_search` 才发现得了——于是 923 轮 0 次调用，而 `tool_search` 本身只被调过 2 次。
- 三条工具描述从"什么时候别用"改成"什么时候用"。`recall_chat` 原本写着「没有历史指向时不要查一整天」，这句直接劝退了自发回忆，换成「需要确认更早说过什么就用它，不必等对方先给出明确日期」。
- 系统提示加一句软提示：`recent_history` 只是最近一段原话，不是全部记忆，想不起来就去查，不要凭印象断言记得或不记得。它在人格段之后、属于缓存前缀，每轮不额外花钱。
- 自动注入的那批结果现在自述局限：按字面关键词命中，不是语义检索，也不代表本群没有别的相关历史。

对照数据：本阶段 923 轮里模型调 `query_memory` 1 次、`recall_chat` 1 次、`search_history_summaries` 0 次；`seed_history_recall` 自动触发 14 次，全部是字面 FTS。现有 10 词白名单在 4070 条原话里命中 34 条，而同类但不在表里的词（以前／之前／上周／上个月／当时／说过／提过／去年／当初／最早等）命中 74 条，是现有的 2.2 倍，且恰好是指向更久远过去的那一组。**不扩词表**，因为扩的是字面路径；改的是让模型够得着语义路径。

### 原话窗口改成锯齿：长到峰值，一次丢掉大半

此前窗口是固定大小的尾随窗口：`conversation_recent_tokens=56000` 一开机就被积压填满，从第一轮起就永远处在"满了只能滑"的状态，头每轮前移，缓存从窗口第一条断。`anchor_window_start` 把头钉在 `conversation_window_step_rowids` 的整数倍上，让它在两次跳跃之间不动——但 800 rowid 只有约 24k token 宽，增长期太短。

改成锯齿：窗口从谷底约 30k 一路长到峰值 130k，**这期间头完全不动**，然后一次性丢掉约 100k 回到谷底。被丢掉的那段只能靠召回工具取回，这是有意的。

三个数必须一起动，因为锚定能丢多少受第三个约束卡死：裁剪要在**本次请求里**找到覆盖该区间的摘要（`_summary_ranges`），而摘要块只装 `conversation_summary_limit` 条。实测单条摘要覆盖 7,494 token 原话、自身 823 token，所以原来的 4 条只能证明约 30,000 token 的覆盖——`step=800`（约 24k）正是贴着这个天花板调的。光放大窗口和步长会让锚定直接拒绝。

按你要的"分两段、提前摘"，把单批摘要放大到 60k：两条就覆盖一次丢弃量，维护调用数随之从约 16 次降到 2 次。维护本来就是后台连续跑的，不是等到要裁时才摘；此前失效纯粹是批次阻塞那个 bug。

预算核对：`input_budget` = 200000 − 4096 = 195,904；峰值请求 = 130,000（原话）+ 24,576（摘要块上限）+ 20,428（实测其余固定）= **175,004**，余量 20,900。维护单批输入预算 81,808，装得下 60,000 原话加开销。

周期约 3,300 rowid ≈ 620 条消息，按活跃群 11.7 条/分钟算，前缀能稳定约 50 分钟。

### 本次配置改动（停机状态下改，旧值备份在 `.backups/`）

| 项 | 旧 | 新 | 依据 |
|---|---|---|---|
| `observing_debounce_idle_ms` | 800 | **8000** | 活跃群 5.1 秒一条消息，0.8 秒空窗等于每条单独发车 |
| `observing_debounce_max_ms` | 2000 | **30000** | `deadline = min(max_deadline, now+idle)`，只改 idle 会被 2 秒的上限按回去 |
| `runtime.heartbeat_enabled` | true | **false** | 暂停系统自发研究；`jobs_enabled` 保持 true，用户发起的工作不受影响 |
| `plugins.interest_share.enabled` | true | **false** | 同一条链的分享臂，且本段 23/23 失败 |
| `public_research` / `interest_share` 授权 | enabled | **false** | 停用不删除，翻回 true 即恢复 |
| `plugins.link_parser.enabled` | false | **true** | 唯一未启用的插件 |
| `conversation_recent_tokens` | 56000 | **130000** | 锯齿的峰 |
| `conversation_window_step_rowids` | 800 | **3300** | 一次丢约 100k，谷底回到约 30k |
| `conversation_summary_limit` | 4 | **3** | 两条覆盖一次丢弃量，留一条余量 |
| `history_target_tokens` | 8000 | **60000** | 一次丢弃分两段摘完 |
| `history_min_tokens` | 2000 | **20000** | 与上一行配套 |
| `maintenance_context_tokens` | 50000 | **90000** | 单批要装得下 60k 原话 |
| `maintenance_output_tokens` | 4096 | **8192** | 60k 的批次值得更长的摘要 |

`addressed_debounce_*`（400／1000）未动，真实 @／回复／私聊的响应速度不变。`conversation_recent_tokens` 保持 56000 未动：窗口变小会改变锚定步长的相对大小，等积压清完再看。

同时补齐了每群插件条目：`link_parser` ×5 群、`workspace` ×4 群；`group:1090284567` 此前只配了 3/12 个插件且**零授权**，已按既有群的模板补齐九个插件并补 `send_file` 授权。结果是 7 个群各 11/12 启用，唯一停用的是 `interest_share`。

### 本次代码改动

- `runtime/plugin_interactions.py`：`commit` 回调改为 `sorted(read_event_ids)`。
- `memory/history.py`：新增 `RESUMABLE_HISTORY_ERRORS` 与 `_resume_blocked_history_batch()`，并重写阻塞分支的注释。
- `tools/retrieval.py`：`search_history_summaries` 不再对对话角色延迟发现；`recall_chat` / `query_memory` / `search_history_summaries` 三条描述改写。
- `cognition/context.py`：系统提示加入长期记忆软提示；`seed_recall` 记录附带自述局限的 `hint`。
- `plugins/builtin/interest_share/plugin.py`：最近送达资料由 8 条 × 1000 字降到 4 条 × 300 字，针对本段 16 次「初始插件资料与当前请求超过输入容量」。

### 结构收敛

`.backups` 从 6.0 GB 收敛到 1.6 GB，只保留一个完整还原点 `pre-start-20260918-190705`（19:07 的库与根配置，加从 `lenbot-20260918-151918` 移入的 15:19 媒体集合），附 `RESTORE.md` 说明代码版本 `1ad6d09` 是按提交时间线推断而非当时记录、以及媒体比库早 3 小时 48 分的含义。媒体只增不改经抽样核对（三份备份共 1200 个文件，当前 `media/` 无一缺失）。本文件同时删去四段已完成轮次的记录，它们保留在 Git 历史里。

### 未确认

- **锯齿窗口的全部收益都是推算，没有一次运行数据。** 峰值 175k 的请求在未命中时全价付；上一段 54% 的调用是完全未命中。命中率若不随锚定修复回升，这组参数会比原来更贵，必须立刻调回。
- 摘要粒度从 7.5k 放大到 60k 会降低语义索引的定位精度，对"想起很久以前某件具体事"的影响没有测。
- 回忆改动之后模型到底会不会主动调检索工具，没有数据；本段的基线是 923 轮里 2 次。
- **两个修复都只有静态核对与副本判定，没有运行证据。** set 序列化那处要等插件自主轮次真正提交一次；批次自动恢复要等一次真实的上游失败或重启之后观察是否自行追平。
- debounce 改动的实际效果没有测。预估只是按 15 分 40 秒那个样本的算术外推，不是实测。
- 摘要索引与认识索引的重建尚未执行，`group:126300994` 的缺口仍在。
- 分批续读（非空 `ranges`）与证据重建连续两轮没有运行证据。
- `workspace` 与 `browser_agent` 配置上已全群启用，但 `gateway.config.json` 不存在、网关进程未运行、8790 端口无监听，**第一次执行必然连接失败**。配置启用不等于能力可用。
- `interest_share` 的容量失败、`seed_history_recall` 的词表与检索方式、以及面板缺少阻塞批次的全局视图，均未修。
- 中转计费倍率缺口按运营者决定暂不处理。

## 2026-09-18：让它按时读到，并且只把真读过的算成读过

两份外部审查（群聊观察与参与专项、`4921efa` 源码审查）对同一批代码给出 FIX_RECOMMENDED / BLOCKER_FOUND。核对后主要结论成立，本轮按"先修证据与调度完整性，再打通到期观察与分批覆盖"的顺序做完。

### 上一轮的"按间隔观察"其实没有触发者

`attention_sample_at` 只在**收到人类消息时**被比较。间隔内、没有其他理由的消息拿到空 `attention_reasons`，Runtime 就不把它交给 BurstAssembler；而 Burst 的定时器只能冲洗已经在缓冲区里的事件。于是"t=0 观察一次、t=5 来一条值得聊的普通消息、然后群里安静"这条路径上，t=60 什么都不会发生——不是模型选择沉默，是根本没有发起判断。把 60 改成 5 只缩小盲区，不改变末尾消息没有触发者这件事。

改法是让普通消息也真正进缓冲：attention 给它 `sample_opportunity` 并写下 `attention_due_at`，BurstAssembler 按这个绝对时刻排定时器。截止时间属于**本批第一条**待观察消息，后来的消息只能把它提前。缓冲区现在有四条车道，共用同一个 buffer 和同一个"最早截止"：

| 车道 | idle / max | 谁走这里 |
|---|---|---|
| `fast` | `addressed_debounce_*`（400 / 1000 ms） | 真实 @、回复 Bot、私聊 |
| `observing` | `observing_debounce_*`（800 / 2000 ms） | 短时观察期内、或轮次进行中的新原话 |
| `slow` | 原 `debounce_*` | 名称、关键词等弱线索 |
| `interval` | `attention_due_at` | 纯周期观察 |

真实 @ 不再零等待冲洗缓冲。先 @ 再补一句要求或一张图，此前首轮容易只看到"@你"，靠后续增量和 FreshInputConflict 纠正，等于多买一次调用去问"你要问什么"。这些毫秒数是首轮调参起点，不是实测最优值，也不是回复延迟承诺。

### 关注期依赖 Bot 先说过话

`focused_participants` 在确认 MESSAGE_SENT 之后才建立。被 @ 之后模型判断无需回复、合法 silent，就没有回执，也就没有关注期；对方随后的补充、第三人接着 Bot 上一句的接话，都掉回普通间隔。新增 `SceneSession.observing_until`：收到真实 @／回复／私聊即开启，与是否发言无关，期内任何成员的新原话获得 `active_observation`。轮次进行中另有 `in_flight_observation`。模型可用 `observation={source,action}` 就本轮实际处理的人类原话申请续期或结束；定时器、Bot 自己的发言和旧来源都不能续期。没有复用管睡眠的 `awake_until`。

### 扫描位置不是覆盖位置

`observe()` 原本把本轮 `observed` 推到本批截点，`append_update` 再只装 `attention_reasons` 非空的事件——被扫过、通过资格检查、却没有理由的第三方接话就这样被跳过了，而扫描位置已经越过它。初始窗口同样只是"最近 N 条"，不保证覆盖上次以来的全部输入。

现在待观察积压记在 `PendingWake.observation`（`OriginalCoverage`：原话总长 + 已提供字符区间）。每轮按 `conversation_read_batch_limit` 取一批未覆盖原话，装不下的保留位置等下一批；长消息按 `original_remainder` 续读剩余区间。唤醒理由只解释为什么开始读，不再决定哪些原话能进请求。覆盖只有在模型确实返回之后才确认，随 `CONVERSATION_COMMITTED.provided_original_ranges` 由 reducer 并入 `pending_wakes`；完整覆盖的纯观察机会就此收口，被搭话与工作来源仍须由 `source_outcomes` 处理。

`observe()` 只在有真正新增或覆盖推进时返回。这一条是补的：按覆盖重写之后，装不下的同一段积压会在每个步骤被重新提供一次，而 `append_update` 每次都要重装 facts、preferences 和一份新的 input footer——既白花 token 又把刚锚定好的前缀顶掉。现在记下本 episode 已经交出过的区间，覆盖没有推进就等下一轮。

### 被裁掉的载体仍留着"已读"

`event_message()` 给消息 A 登记原文范围时，也给 A 的 `reply_to` 引用原文 X 登记；随后 `anchor_window_start()` 从请求里删掉 A，却只撤销 A 自己的记录。最终请求里既没有 A 也没有独立的 X，X 仍在已读集合里。反方向也错：X 自己的条目被删、但另一条保留消息仍完整引用 X 时，简单清掉 X 的记录会错撤真实读取，所以不能靠递归删除引用 ID 解决。

改成按最终请求体重建：每次原文投影记进 `range_contributions` 并随所属消息存为 `_original_ranges`，裁剪完成后 `reconcile_original_reads()` 清空并只从**实际保留且非省略**的消息重建，多个载体对同一原话取并集。工具正文按 `tool_call_id` 记录渲染时的投影与内容，插件替换过正文的工具消息不继承那批范围。`confirm_original_reads()` 在模型返回后把本次范围转为已确认，后续步骤在此之上累积。

同一处另有两个边界错误：锚点公式 `(oldest // step + 1) * step` 求的是严格大于，`step=200`、最早 rowid 正好 200 时得到 400，白跨一整格；改为真正的上取整。以及锚定裁掉的区间并没有核对摘要是否真的覆盖——注释里的假设而已，摘要前沿被失败批次挡住时这会进一步减少可见原话。现在只在本次请求里确实有已完成摘要覆盖该区间时才额外裁掉，否则保留原话并记 `summary_does_not_cover_original`。

### 分享被限额挡一次可能就不再有下一次

Runtime 入口在额度耗尽时直接把 `interest_share` 槽设为 CANCELLED，而续排在插件 `run_slot()` 的 `finally` 里——入口取消绕过了它。单群部署、或所有群本轮都被挡下时，额度恢复也不会恢复调度，要等重新启用、改配置或重启。限额判断移进 `run_slot()`（记 `skipped_hourly_limit`），续排留在同一个 `finally`；`ensure_next()` 自己核对 `manifest.enabled`，停用才停止续排。不补发错过的分享。

候选顺序也改了：`list_public(considered_in_scene=...)` 按本群最近一次 `interest_share_consideration` 时间升序，未考虑过的优先。此前永远取 `observed_at` 最新的第一个合格候选，它一直不适合这个群时，每槽都在同一条上沉默，后面的没机会。

### 额度到顶后的 @ 不再自动回一条

超额时真实 @／回复会触发一条不经模型的固定提示。这与本轮"被 @ 只提高阅读优先级、反应式表达可以自然结束"直接冲突——"@你 哈哈哈"也会收到"这一小时我已经回你多少条"。删掉 `_send_limit_notice` 与 `limit_notice`，入口拒绝改写一条 `observation` trace（`reason='hourly_limit'`），剩余额度显示在群配置页。这是明确的产品选择，不是把它判成程序错误。

### 其余

- `certain` 从 `bool(reasons)` 收回到只认 ADDRESSED_REASONS。系统提示不再说"certain=true 需要有处理结果"，改为"wake 只解释阅读机会与优先级"；`input_status` 同时给出 `directly_addressed`。Actor 的新鲜输入检查不再按 `wake.certain` 选来源，而是要求"同一请求者 + 这条确实是搭话"。
- 名称／关键词冷却不再吞掉触发资格：冷却内的消息落回周期观察，不会因为第一次第三人称提到角色名就让紧接着的"然然你怎么看"失去机会。
- 机会过期保留，但已经**部分提供过**的原话例外——它保留已记录区间等剩余覆盖，不在中途被丢掉。上一轮 321 条积压压塌窗口的教训还在，所以没有整体取消 TTL。
- 图片定位符改用 `I:<asset_id>`。此前按本轮注册顺序发号，当前消息新增一张图会把历史消息里的图片重编号，历史文本跟着变，前缀在那一条断。
- 配置项改名：`attention_sample_probability` / `attention_sample_window_seconds` → `attention_observation_enabled` / `attention_observation_interval_seconds`（`scenes[group].attention` 同名字段一并改）。`extra='forbid'`，**带旧键的根配置会被启动校验拒绝**，升级前必须跑 `scripts/migrate_observation_config.py`。面板「旁听 +2」改为「开启并将观察间隔减半」，密度文案标明是间隔折算频率而不是调用上限。

### 静态核对

- 全部模块导入通过（0 失败），`compileall` 通过。
- 真实库 13 个 `scene_sessions` 用新模型载入正常，`observing_until` 与 `PendingWake.observation` 取默认值；现存 35 条 pending wake 全部是旧记录（`observation=None`），理由分布 `in_flight_follow_up` 23、`sample_opportunity` 7、`mention` 4、`address_name` 3、`runtime:reflection_recorded` 1。
- **真实根配置仍然是旧键**（`attention_sample_probability=0.8`、`attention_sample_window_seconds=60.0`，无群级覆盖）。迁移脚本 dry-run 报告正好这两项改名并按新 schema 校验通过；未写入。
- 候选轮换的新 SQL 在真实库副本上执行通过，9 条公共兴趣，带群与不带群参数结果一致（该库尚无 `interest_share_consideration` 记录）。
- 前端 `npm run build` 通过，`web/static/dist` 已重建（不进 Git）。

### 首次真实启动：一个 f-string 花括号让 6 个群哑了 2.5 分钟

19:07:15 取得授权后启动（Shadow 关闭，6 个 chat 群）。**启动即失败**：

```
[ERROR] len_bot.runtime.agent_runtime: Conversation failed in group:1102823315: NameError: name 'source' is not defined
```

2.5 分钟内 35 次 conversation 全挂在同一处。根因在本轮新加的系统提示词里——它是 f-string，而新写的一句

```
可用observation={source:M引用,action:continue}申请同一短期观察
```

花括号没有转义，`{source:M引用,action:continue}` 被解释成一个替换字段（字段名 `source`、格式规格 `M引用,action:continue`），于是每次 `build()` 都抛 NameError。改成 `{{...}}` 之后渲染回预期的字面量。

**为什么之前没抓到。** 编译和导入都进不到 f-string 内部：它只在方法真正执行时求值，所以 `compileall` 与 `import` 全绿是必然的——这正是把静态检查当成运行通过的代价。`pyflakes` 一次就点了出来（`context.py:1224:188: undefined name 'source'`），本轮之前没有跑过它。AST 复查确认该提示词其余五个替换字段（`config.identity_name` 等）都是有意的，只有这一处是意外。

代价有限：35 次都在装配阶段失败，**模型调用 0 次、发送 0 条**，没有花费也没有错误发言，期间群里收到的消息仍完整存进事件库。副作用是被 kill 时留下一条 pending 历史批次（`de8ba131`，group:126300994，rows 30214-30482），需要从面板重试；另外三条 pending 批次（16:43—16:45，群 992584358 / 1078114081 / 1042218062）在本次会话之前就已存在。

19:08:56 修复后重启：OneBot 连上，Dashboard 起来，**重启之后 conversation_error 0 次**。截至 19:10 群里没有新消息，因此模型调用 0 次——这正是「无新输入不调用」应有的样子，但也意味着到期观察本身仍未取得运行证据。

### 第一段真实流量：10.5 分钟，六个群

19:08:56—19:19:28 这一段是本轮改动第一次被真实流量检验。14 次 conversation、19 次 history_maintenance。重启后收到的人类消息里，`attention_reasons` 分布为 `sample_opportunity` 182、`in_flight_observation` 143、`active_observation` 6、`address_name` 3、`mention` 1——到期观察和观察期都在真实触发，不再只有被 @ 才动。

`PendingWake.observation` 确实带着 `OriginalCoverage` 进了请求（trace 的 `wake_sources` 里能看到 `total` 与 `ranges`）。但**没有任何一条 wake 留下非空 `ranges`**：完整覆盖的纯观察机会直接收口出列，只有装不下的长原话才会留半截等续读，这十分钟里没有出现。分批续读与证据重建因此仍然没有运行证据。

group:1078114081 的两次对话都以上游 `APIConnectionError: Server disconnected` 失败，十分钟里积压到 156 条 pending wake（最早一条正好 10 分钟前），600 秒 TTL 在边界上起作用。两次 conversation_error 都来自上游中转，与本批无关。

### 面板重试历史批次：不只是报错，那次重试本身被丢掉了

用户在面板点重试，收到 `History maintenance is already running for this scene`。

`OPERATOR_ACTION` 记录显示 19:15:23/:24/:25/:28 连点四次同一批次（`c2b545aa`，group:1078114081），而该群 19:14:32—19:17:55 正好有一次 conversation 在跑。第一次点击撞上 `actor.has_active_episode()`，被 `retry_history` 用「Conversation is running」挡下；后三次与第一次的 scene 认领重叠，拿到那句「already running」。批次至今仍是 pending，没有产生任何 model call，也没有留下 trace。

真正的缺口不在提示词。`retry_history` 把批次改回 pending 之后交给 `_maintain_history(retry_batch=..., claimed=True)`；后者在循环开头发现轮次在跑，就 `_schedule_history_maintenance` 然后 return——而定时器那条路走的是 `begin_history_batch`，它看到未完成批次就拒绝（未完成的区间只能由人显式重试）。于是**操作员的重试被静默丢掉，这个群继续卡着**，下一次警告还会被同批次去重挡掉。忙群里两次对话之间的空隙可能永远等不到，重试这个动作实际上在碰运气。

改法：`retry_history` 不再因为轮次在跑而拒绝，把等待交给后台；`_maintain_history` 手里握着重试批次时改为 `await actor.wait_episode_idle()` 再继续（HistoryCommitDeferred 已经在用这条路径），认领全程不放。并发提示改成中文，且现在是真话——它确实正在跑。

### 缓存为什么仍然是 0：锚定一次都没成立

重启后 15 次 conversation 调用里只有 4 次命中缓存，其余 `usage_json` 连 `prompt_tokens_details` 都不带（上一轮记的「`usage_json` 拿不到 `cached_tokens`」是错的，命中时它会回传）。命中的 4 次是同一轮次的第二步、或重启后接续上一进程轨迹的 resume，都属于「同一份请求再发一次」；**跨轮次零命中**，每轮实付 64k—70k 输入。

原因在 trace 里是直接写着的：14 次请求里 13 次记了 `window_anchor / summary_does_not_cover_original`。请求本身的布局是对的——

| 位置 | 段 | tokens |
|---|---|---|
| 0 | persona | 4919 |
| 1–289 | recent_history | 43203 |
| 290–315 | related_original / original_input / original_media | 7640 |
| 316 | own_recent_expression | 267 |
| 317 | current_time | 97 |
| 318–324 | input_status … execution_budget | 11101 |

易变的东西全在尾部，`execution_budget` 每步搬到最后，能复用的前缀有 56k 正文加 9.3k 工具定义。但前缀要成立，`recent_history` 的**起点**必须不动，而起点只有 `anchor_window_start()` 能钉住；它又要求被裁掉的原话在本次请求里确实有已完成摘要覆盖。摘要前沿被 pending 批次挡住，锚定就永远不成立，窗口起点随每条新消息滑动：group:1042218062 相邻两次请求的首条从 `M29997` 变成 `M30103`，整块 44k 平移，缓存自然从第 1 条消息就断。

唯一一次锚定成功（19:15:00，group:1102823315，裁掉 5 条）正好发生在该群维护连续追赶、摘要前沿贴近当前行的时候。所以这不是两个问题：**卡住的历史维护就是缓存不命中的原因**，上面那个重试缺口是它的上游。

积压规模（按各群自己的消息条数算）：

| 群 | 摘要前沿 | 落后条数 |
|---|---|---|
| group:1078114081 | 21836 | 2458 |
| group:992584358 | 21593 | 2155 |
| group:1042218062 | 29248 | 563 |
| group:126300994 | 30211 | 331 |
| group:1102823315 | 32279 | 28 |

追平要一批批跑，每批约 8k 输入、20k 左右一次调用。前两个群从 16:43 就卡着，各需二十批上下。

### 19:26—20:05 第二段：重试全部生效，缓存跨轮次命中，但慢

四次面板重试（19:29:05—19:29:19，每群一次）全部跑起来了，包括 group:1078114081——点击时该群正好有对话在跑，这正是上一节那个修复要解决的情形。随后 29 批维护跑完，126300994 / 1042218062 / 1102823315 / 1014123451 追平到 10—90 条以内；992584358 与 1078114081 各跑了 8 批后撞上上游 `APIConnectionError`，批次转 failed，又把这两个群挡住了。

锚定与追赶严格对应：`anchor_fired` 只出现在追平的群（126300994 ×5、1102823315 ×5、1042218062 ×2、1014123451 ×1），`anchor_blocked` 只出现在仍被挡住的两个群（1078114081 ×7、992584358 ×6）。窗口起点因此第一次真正钉住——1102823315 的起点 M32060 连续 11 轮没动，126300994 的 M34404 连续 6 轮没动；而被挡住的两个群 14 轮动了 14 次、8 轮动了 8 次。

缓存随之出现跨轮次命中：42 次成功调用命中 21 次，输入 2,702,493 token 里 1,201,053 走缓存读（44%）。关键是「一轮的第一次调用」32 次里命中 12 次——上一段这个数是 0。

### 缓存剩下的两件事：一件是我们的，一件不是

**前缀仍然提前断。** 起点没动的那 11 轮里，每一轮与上一轮的第一个差异都落在**上一轮输入块的位置**：同一条消息这轮作为 `original_input` 渲染（完整原文加图片像素），下一轮变成 `recent_history` 的压缩投影，内容不同，缓存就断在那里。断点位置与实测缓存量吻合到个位数：断在 76/140 条时缓存读 20,443，断在 274/294 条时缓存读 45,077。每轮因此白付 6%—46% 的前缀。未修。

**其余的丢失不在我们这边。** 同群同起点，按距上一轮的间隔分：0.9 / 1.2 / 2.2 / 2.2 / 2.8 分钟五轮全部命中，3.4 / 4.0 / 4.3 / 5.4 分钟四轮全部为 0（另有一次 2.1 分钟例外）。请求前缀相同而命中与否只跟间隔相关，是隐式缓存 TTL，约 3 分钟。群里两轮对话的实际间隔常在 3—5 分钟，所以过半轮次整份重算。**【已作废】** 这条结论由 14 次请求推出，2026-09-19 用 772 次调用重测未复现；真正决定命中的是窗口起点稳不稳，见本文件最新一段。

### 慢在哪：输出，不是输入

被 @ 到提交，30 次直接搭话中位约 224 秒，最慢 436 秒。拆开看：

| 输入量 | 平均耗时 | | 输出+推理 | 平均耗时 |
|---|---|---|---|---|
| 20k | 31.3s | | <1.5k | 27.3s |
| 36k | 31.7s | | 1.5—3k | 40.5s |
| 60k | 46.3s | | >3k | 59.7s |
| 71k | 49.6s | | | |

输入从 20k 涨到 65k 只贡献约 15 秒，输出每多 1k 约 +10 秒。而 `reasoning_effort` 三个角色全是 `high`，conversation 平均每轮烧 2,036 个推理 token。94 次调用里 36 次超过 60 秒，而 provider G 的 `timeout_seconds` 正是 60。

排队是第二层：`_cognition_semaphore` 是**全局**的，`conversation_max_concurrent=2` 让六个群抢两个位置；历史维护不占这个信号量也没有自己的上限，追赶期 10 分钟内 48 次维护调用时对话平均 65.1 秒，降到 12 次时 41.6 秒。

### 中转链路实测：不是网络

顺着 `new-api` 的渠道查到上游是 `http://172.17.0.1:8317`，进程 `/opt/cliproxyapi/releases/7.2.154/cli-proxy-api`（CPA），配置里 `auth-dir` 只有 **1 个账号**——所以「多账号轮换打散缓存」这个解释排除。（原文在此处写「上面那条 3 分钟 TTL 的结论成立」，**该结论已于 2026-09-19 作废**；排除多账号轮换这一点本身仍然成立。）CPA 经 `proxy-url: http://127.0.0.1:7890`（CrashCore）出网，实测到 `cloudcode-pa.googleapis.com` 与 `generativelanguage.googleapis.com` 各三次：TLS 0.47—0.49s，TTFB 0.63—0.67s。**链路不是瓶颈**，46 秒是模型生成时间。

### 本次配置改动（停机状态下改，旧值备份在 `.backups/`）

| 项 | 旧 | 新 | 依据 |
|---|---|---|---|
| `models.routing.conversation.reasoning_effort` | high | **low** | 群聊回一句平均 2,036 推理 token，每 1k 约 10 秒 |
| `runtime.conversation_max_concurrent` | 2 | **6** | 全局信号量，六个群原本抢两个位置 |
| provider `G` 的 `timeout_seconds` | 60 | **120** | 94 次调用 36 次超过 60 秒 |

`maintenance` 与 `work` 的 `reasoning_effort` 仍为 high（维护平均 1,271 推理 token／31.7 秒），未动。`conversation_recent_tokens` 仍为 56000，未动：慢的主因是输出不是输入，砍它主要省的是 miss 时的钱，不是时间。

### 本次代码改动

- `attention.py`：本群有对话在跑时不做机会过期清理。重启后 19:28—19:29 三轮对话整轮作废于 `SceneCommitConflict: Handled sources are not currently pending`，来源分别是 655 / 691 / 742 秒前建立的纯观察机会——轮次开始时它们还在 `pending_wakes`，跑到提交时被 600 秒 TTL 清掉了，模型调用一起作废。三轮的来源全是 `in_flight_observation` / `sample_opportunity`，没有 @ 或回复，丢的是观察轮本身。清理在轮次结束后的下一个事件照常执行，上限只是推迟一轮。
- `cockpit.py`：`workspace-artifacts` 把 `RuntimeError` 包成 409。此前点工作产物清单时 `execution/service.py:847` 的「该工作还没有已确认的执行快照」直接穿成 500 加 traceback，运维侧看到的是服务器故障而不是那句话；日志里 4 次 `Exception in ASGI application` 都是它。

### 未确认

- **除了「能正常启动、空闲时不调模型」，其余改动仍然没有运行数据。** 到期观察在无后续消息时是否按时触发、四条车道的毫秒数、短时观察期 120 秒、分批覆盖、证据重建，全部尚未被真实流量检验。
- 本轮把 `pyflakes` 加进了实际使用的检查手段，但它只覆盖未定义名称一类；提示词与模板里的运行期错误仍然只有真实启动才暴露得出来。
- 定时观察是否真的在无后续消息时按时触发、四条车道的毫秒数是否合适、短时观察期 120 秒是否够用，都只能在真实群里看。
- 观察频率提高后的实际调用量与成本没有测。缓存已能从 `usage_json` 直接读（命中时带 `prompt_tokens_details.cached_tokens`），结论见上；`I:<asset_id>` 对前缀的影响仍未单独测。
- `tests/` 里有 9 处仍引用已退役的 `attention_sample_probability` / `attention_sample_window_seconds` / `limit_notice`（`test_scene_transactions.py`、`test_dashboard_api.py`、`test_runtime_lifecycle.py`）。按本仓库约束本轮不新增、不修改、不运行测试，这些引用原样留着。
- `AttentionPolicy.random_source` 与 `AgentRuntime(attention_random=...)` 已无调用方，但仍被上述测试引用，未删。
- 观察期开启后 `pending_wakes` 在被限额挡住的小时里仍会累积，恢复后按批读取而不是一次涌入；实际累积速度与排空节奏没有数据。
- 摘要前沿被失败批次挡住时，锚定不再额外裁剪，等于该群窗口起点回到逐轮滑动。这是有意的取舍（宁可不命中缓存，也不谎称摘要覆盖），代价已经测到：13/14 次请求锚定失败、跨轮次零命中，每轮多付约 60k 输入。维护追平之后锚定能否稳定成立，以及 `conversation_window_step_rowids=800`（默认 200）是否合适，要等积压清完再看。
