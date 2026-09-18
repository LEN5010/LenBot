# 当前任务

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

**其余的丢失不在我们这边。** 同群同起点，按距上一轮的间隔分：0.9 / 1.2 / 2.2 / 2.2 / 2.8 分钟五轮全部命中，3.4 / 4.0 / 4.3 / 5.4 分钟四轮全部为 0（另有一次 2.1 分钟例外）。请求前缀相同而命中与否只跟间隔相关，是隐式缓存 TTL，约 3 分钟。群里两轮对话的实际间隔常在 3—5 分钟，所以过半轮次整份重算。

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

顺着 `new-api` 的渠道查到上游是 `http://172.17.0.1:8317`，进程 `/opt/cliproxyapi/releases/7.2.154/cli-proxy-api`（CPA），配置里 `auth-dir` 只有 **1 个账号**——所以「多账号轮换打散缓存」这个解释排除，上面那条 3 分钟 TTL 的结论成立。CPA 经 `proxy-url: http://127.0.0.1:7890`（CrashCore）出网，实测到 `cloudcode-pa.googleapis.com` 与 `generativelanguage.googleapis.com` 各三次：TLS 0.47—0.49s，TTFB 0.63—0.67s。**链路不是瓶颈**，46 秒是模型生成时间。

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

