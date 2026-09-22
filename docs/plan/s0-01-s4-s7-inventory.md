# S4—S7 现状索引（只读源码核对）

> 基线资料：以下事实与源码行号限定于文中标明的核对时点；任务当前状态只见[路线入口](README.md)，后续实施见[当前任务](../iteration.md)。草案未确认部分不视为已采用。

- **核对 commit**：`ad41a5a71a4b5ec3693f4cc846342964c5b0b8c6`（分支 `feat/s0-product-contract`，基线业务代码 `9178c34`）
- **核对时间**：2026-09-21T14:32Z（本机 23:32 JST）
- **只读方式**：`git rev-parse/log/show/branch/tag/ls-files/check-ignore`、`grep`、`read`、`ls`、`find`、`wc`。未运行测试／夹具／探针／回放／截图／压力／故障注入，未启动服务，未调用模型或平台，未读 `len_bot.db`（1 GB）或媒体内容，未执行 `uv sync`／`npm install`／`docker build`——安装路径只做**文档与文件比对**。唯一写操作是本文件。
- **范围**：任务卡 `docs/LenBot_分阶段任务卡_20260921.md:614-1157`（S4—S7）与路线书 `docs/LenBot_成熟开源项目路线书_20260921.md:501-594`。
- **一句话结论**：**S4 的机制层几乎全部已在源码中落地，真正的缺口是“真实场景的人工观察证据”而不是实现**；**S5 的运营界面（群工作台／Schema 表单／capability_status／Jobs／Memory）已成形，缺的是唯一最小安装路径、数据保留政策、支持矩阵与 SECURITY**；**S6 的 CI 与 AGENTS“不新增测试”自洽但版本纪律近乎空白（无 CHANGELOG、无非备份 tag、无 release 分支）**；**S7 整段无任何落地物，只存在模板与计划文本**。建议 S4-01／S4-03／S4-04／S5-02／S5-03（备份部分）以“保留实现 + 补证据”方式推进，S4-05／S5-06／S6／S7 需要真实决策或新建。

> **环境时序说明（重要）**：核对期间工作区并非静止。`docs/plan/`（含本文件的同级底稿）、`.github/ISSUE_TEMPLATE/`、`.github/PULL_REQUEST_TEMPLATE.md` 在本次会话进行中才出现，且 **`git status --porcelain` 显示它们全部未跟踪**（`?? docs/plan/`、`?? .github/ISSUE_TEMPLATE/`、`?? .github/PULL_REQUEST_TEMPLATE.md`）；HEAD 处被跟踪的 `.github` 内容只有 `workflows/ci.yml`（`git ls-tree -r --name-only HEAD -- .github` → 仅 `ci.yml`）。因此**关于 Issue/PR 模板与 `docs/plan` 的判断，其“存在于工作区”与“已进入版本”是两件事**，下文分别标注。

---

## S4｜把连续性变成真实群聊 Agent 体验

### S4-01 观察窗口、接话与沉默

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| attention 真实入口 | 已实现 | `src/len_bot/runtime/attention.py:34` | `class AttentionPolicy:` | 无 |
| 单一判定入口 | 已实现 | `src/len_bot/runtime/attention.py:82` | `def apply(self, state, event, bot_actor_id, *, in_flight=(), work_participants=(),` | 入参含 `conversation_active`，见 :83 |
| 原因分类（谁在叫它） | 已实现 | `attention.py:13-22` | `ADDRESSED_REASONS = {'mention', 'reply_to_bot', 'private_message',`／`OBLIGATION_REASONS = ADDRESSED_REASONS \| {'work_participant'}` | 关键词是机会不是义务，见 :139-143 |
| scene_policy 真实入口 | 已实现 | `src/len_bot/runtime/scene_policy.py:5` | `class ScenePolicy:` | 只管启用／白名单／可委托，不参与“是否接话” |
| 群启用与白名单 | 已实现 | `scene_policy.py:8,22` | `def enabled(self, scene_id):`／`def chat_allowed(self, scene_id, requester_qq_uid):` | 私聊恒真（:10） |
| social_core 真实入口 | 已实现 | `src/len_bot/cognition/social_core.py:23,27` | `class SocialCognitionCore:`／`async def run(self,session,events,through_rowid,episode_id,...)` | 唯一实例在 `runtime/agent_runtime.py:148` |
| 全局/本群注意力解析 | 已实现 | `src/len_bot/runtime/attention_config.py:22,66` | `def effective_attention(root, scene_id=None) -> EffectiveAttention:`／`def effective_sticker_preference(...)` | `EffectiveAttention` 带 `inherited` 标记（:15） |
| 可调参数与默认值 | 已实现 | `src/len_bot/config.py:59-72` | `attention_keywords: list[str]`／`attention_observation_interval_seconds: float = Field(gt=0,`／`attention_observation_enabled: bool = Field(`／`attention_keyword_cooldown_seconds: float = Field(ge=0)`／`attention_focus_seconds: float = Field(gt=0,`／`attention_opportunity_ttl_seconds: float = Field(default=600.0, gt=0,` | 样例实际值：`attention_observation_interval_seconds = 300.0`、`attention_keyword_cooldown_seconds = 60.0`、`attention_focus_seconds = 120.0`、`attention_opportunity_ttl_seconds = 600.0`（`lenbot.config.example.json` runtime 节） |
| 合并等待（有界合并） | 已实现 | `src/len_bot/events/builder.py:109-114` | `def _windows(self, priority):`／`return self.config.addressed_debounce_idle_ms, self.config.addressed_debounce_max_ms` | 三级 priority→(idle,max)；默认见 `config.py:40-47`（400/1000、800/2000 ms） |
| 普通机会有界合并、不永久延后 | 已实现 | `attention.py:144-152` | `if not reasons and attention.observation_enabled:`／`state.attention_sample_at = min(state.attention_sample_at,` | 注释 :145-146 `The deadline belongs to the first unseen input, so later arrivals cannot postpone it.`——**正是“没有每次新消息重置到永远的等待”** |
| 话题结束可沉默 | 已实现 | `attention.py:55-80` | `def _close_stale_opportunities(self, state, now, *, conversation_active=False):`／`or now - wake.created_at < ttl` | 有半读原话时不因超时冒充已读（:79） |
| 区分“@ 后仍不需回复”与“漏掉明确问题” | 部分实现 | `attention.py:132-138` | `certain = bool(set(reasons) & ADDRESSED_REASONS)`／`reasons.append('active_observation')` | 机制上可分（`certain`／`attention_lane`）；**是否真的没漏，只有真实群聊记录能证明，本次未核实** |
| 前置 LLM 分类器 | 未找到 | — | grep `classif` 仅命中原有 `classify_event`（插件事件分类，`plugin_interactions.py:30`），非参与前置分类 | 无 |
| 关键词强制回复 | 未找到 | `attention.py:139-143` | `state.attention_keyword_at = now`／`reasons.append('keyword_opportunity')` | 关键词只加机会；`config.py:104` 明写 `address_names ... 只提供参与线索，不强制回复` |
| 每群永久话题模型 | 未找到 | — | 无 topic model／per-scene 永久模型符号 | 无 |

### S4-02 模型决策合同与短任务路径

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| persona contract 装配 | 已实现 | `src/len_bot/cognition/context.py:1497-1503` | `identity = f'''你以{config.identity_name}的角色口吻参与中文群聊。` | 五字段全取自 `config.*`，无角色硬编码 |
| persona 作为稳定前缀 | 已实现 | `context.py:31` | `STABLE_SECTIONS = {'persona'}` | 注释 :23-30 解释为何只有 persona 可提前 |
| 决策合同正文 | 已实现 | `context.py:1504-1520` | `contract = '''【当前互动与来源】` | 内含“不为证明在线而回复确认”（:1506） |
| respond 机器合同 | 已实现 | `src/len_bot/cognition/proposals.py:171-179` | `class Respond(StrictModel):`／`messages:list[TurnMessage]=Field(max_length=3,`／`next:Literal['end','continue','wait']` | 空列表=沉默（:172） |
| 关系字段（不填 intent） | 已实现 | `proposals.py:94`, `:123-126` | `_RELATIONS=('ack_ref','operation_ref','delivery_ref','work_ref')` | 每条只能选一种，:146 报错 |
| 关系形状的机器约束 | 已实现 | `proposals.py:97-112` | `def _message_shapes():` | 用 JSON Schema `oneOf`+`not` 表达，正合“模型只决定语义” |
| 短查询在原循环完成 | 已实现 | `context.py:1520` | `- 当前循环与剩余预算内可完成的短查询、计算和必要续读直接处理，分页本身不要求建工作。` | **任务卡验收句“一项普通查询不为了分页创建多份工作”在提示词层已直接对应** |
| start_work 工具说明 | 已实现 | `proposals.py:249` | `'start_work':(StartWork,'建立需要较长执行、跨轮保存进度或使用仅供工作调用能力的后台只读工作。...'` | 与 :1520 同义 |
| 宿主填来源与账务 | 已实现 | `context.py:1533` | `sources只声明silent或incomplete及原因；...由宿主生成。` | 模型的 `sources` 不承载 replied/delegated |
| 规划—审查—回复流水线 | 未找到 | — | 无多模型规划链；`AgentLoop` 单循环（`cognition/agent_loop.py:161`） | 无 |
| 改人工人格原文 | 未找到 | `config.py:103-110` | `identity_persona: str` 等由运营配置提供 | 未自动改写（符合“明确不做”） |

### S4-03 长工作、修订、取消与成果复用

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| JobStore 持久 schema | 已实现 | `src/len_bot/runtime/job_store.py:312,328` | `CREATE TABLE IF NOT EXISTS agent_jobs (`／`CREATE TABLE IF NOT EXISTS job_exchanges (` | 无独立 status 列，状态在 `tasks.status` |
| 状态枚举 | 已实现 | `src/len_bot/scheduler/models.py:6`；`cognition/jobs.py:195` | `class TaskStatus(StrEnum):`／`status: Literal["completed", "partial", "failed", "interrupted", "cancelled"]` | 执行态映射 `job_store.py:56-59` |
| 后台不阻塞主循环 | 已实现 | `job_runner.py:203-207`, `:186` | `def kick(self, scene_id):`／`task = asyncio.create_task(self._run_scene(scene_id))`／`self._slots = asyncio.Semaphore(runtime.config.job_max_concurrent)` | 无 |
| runner 不直接发群 | 已实现（否证） | `job_runner.py`（grep `ActionItem\|action_queue` 无命中） | 结果只落库并排队 `AGENT_JOB_FINISHED`（`job_store.py:827`） | 交付经 `plugin_interactions.py:235 actor.commit_turn` |
| 修订／取消入口 | 已实现 | `cognition/jobs.py:10,13` | `operation: Literal["create", "revise", "cancel", "resume"] = "create"`／`expected_revision: int \| None = Field(default=None, ge=1)` | 控制引用 = `job_id` + `expected_revision` |
| 修订版本冲突拒绝 | 已实现 | `job_store.py:413` | `if not current or current["revision"] != proposal.expected_revision: raise JobChanged(...)` | 无 |
| **成果复用 `reuse_work_ref`** | 已实现 | `proposals.py:188-189`（定义）、`:378-383`（应用）、`cognition/jobs.py:103-127`（模型） | `reuse_work_ref:str\|None=Field(default=None,min_length=1,`／`reused=ReusedWorkResult.from_job(previous)` | 9178c34 引入；`jobs.py:104` docstring `"""One original result version copied as input, without execution authority."""` |
| 复用前置“必须先真读过” | 已实现 | `proposals.py:380-381` | `if (previous['id'],previous['revision']) not in self.context.confirmed_work_results:`／`raise ValueError('reuse_work_ref 须先读取该工作当前版本的成果；...')` | 无 |
| 复用只限普通研究工作 | 已实现 | `jobs.py:118-119` | `if job['work_operation']!='information' or job['plugin_origin'] is not None: raise ValueError('本入口复用普通研究工作；...')` | **专用插件成果被显式拒绝**，符合“不另建子代理调度系统” |
| 复用排除模拟/目录 | 已实现 | `jobs.py:120-122` | `if job.get('origin_mode')!='live' or not job.get('result'): raise ValueError('原工作须有真实执行的完整或部分成果；...')` | 无 |
| 生成／可下载／已上传可区分 | 已实现 | `src/len_bot/media/files.py:61,78,120,190` | `def file_receipt_status(receipt):`／`def file_upload_state(record):`／`def public_file_candidate(...)`／`def file_delivery_facts(...)` | :62 docstring `"""A saved file receipt is success only with a real platform file identity."""`；状态集 `unknown/uploaded/submitted/failed/shadow/simulated/prepared`（:110-112） |
| 取消/修订后旧结果不作为新目标成功 | 已实现 | `job_store.py:570`；`scenes/actor.py:178-181`；`events/builder.py:58` | `UPDATE agent_jobs SET revision=?,...,result_json=NULL,...`／`event.metadata['obsolete_job_result'] = ...`／`if event.metadata.get("obsolete_job_result"): return` | 双保险；`proposals.py:393-394` 另拒旧状态引用 |
| 预算／期限跨恢复不重置 | 已实现 | `job_store.py:222-242`；`job_runner.py:822-823` | `async def record_job_deadline(...)` docstring `"""Written once and never rewritten..."""` | `job_store.py:1030` `"""Keep actual costs and completed reads across revision/cancellation."""` |
| 无独立 revision 历史表 | 未找到 | `job_store.py:570` | 修订**就地覆盖**并清空 `result_json` | 仅 `job_exchanges` 留轨迹；面板是否展示旧版本，本次未核实 |

### S4-04 事件触发、心跳与主动分享

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| 插件保存业务事件 | 已实现 | `src/len_bot/plugins/base.py:114-129` | `async def emit_event(self, name, payload, *, scene_id, event_id, timestamp)`／`await self._runtime.receive_event(event)` | 经 Actor，非直写 |
| scheduler 拾取 | 已实现 | `src/len_bot/scheduler/engine.py:152-179` | `async def on_event(self, event)`／`if await self._emit_task_due(...)` | 按 `wake_event_type`+`wake_match` 匹配 |
| 主体／范围／配额准入门 | 已实现 | `src/len_bot/runtime/gate.py:123` | `def _capability_refusal(...)` docstring `"""The ordered capability check for one non-human work proposal."""` | 配额在 `job_store.py:85-119`；并发闸 :163-168 |
| 心跳容量保留人类位 | 已实现 | `src/len_bot/runtime/heartbeat.py:55-57` | `raise ValueError('工作容量不足，至少保留一个人类工作位置')` | 无 |
| 研究结果作为 candidate | 已实现 | `src/len_bot/plugins/builtin/interest_share/plugin.py:147` | `async def on_candidate(...)` | :99-112 候选产生 + `check_publication` 过滤 |
| 分享由目标群当前语境决定 | 已实现 | `interest_share/plugin.py:181-186` | `'不相关、已知、证据不足或会打扰时用 respond 保持沉默。研究完成不意味着应当发布。'` | :163-180 带本群近期真实送达记录 |
| 旧过期事件不补刷（多层） | 已实现 | `heartbeat.py:47-48`；`interest_share/plugin.py:134-135`；`bilibili_live/plugin.py:249-250` | `if int(slot['slot']) != slot_id(store.clock()): raise ValueError('错过的心跳槽不能创建工作')`／`raise ValueError('本次兴趣分享机会已过期')`／`raise ValueError('直播样本已过期…')` | 另有 `builder.py:56-57`、`interest_publication.py:17-18`、`interests.py:143-144`、`open_loops.py:18-20` |
| heartbeat 语义 | 已实现 | `heartbeat.py:1`, `config.py:76-78` | `"""Scheduler-owned public research cycles; missed slots and unknown stops never replay."""`／`heartbeat_enabled: bool = Field(default=False, description='系统心跳；默认关闭，不补跑错过的槽')` | 槽长 `SLOT_SECONDS = 30 * 60`（:13） |
| heartbeat 不刷存在感 | 已实现 | `heartbeat.py:139-140` | `constraints_add=['只读匿名公开资料，不继承群史、成员资料、凭据或私有技能',`／`'最多 1800 秒，保留真实证据范围；本轮只保存候选，不发布群消息'],` | 无 |
| bilibili_live 触发与意图 | 已实现 | `bilibili_live/plugin.py:205-233`, `:142-160` | `async def _poll_loop(self):`／`call.run_agent(instructions=('当前是已订阅开播公告，只根据所给真实场次写一段邀请，正确指认主播。'...)` | 插件 ID 是 `bilibili_live_sensor`（`__init__.py:25`） |
| 后台直接发群 | 未找到（否证） | `actions/queue.py:197`；`plugin_interactions.py:160` | `delivery = await self.send_adapter(action)`／`raise ValueError('Background work returns results through its existing delivery; it cannot submit direct messages')` | 唯一发送出口，adapter 仅由 `main.py:41` 注入 |
| 定时刷存在感／缓存保温任务 | 未找到 | — | 无此类任务符号 | 无 |

### S4-05 通用预设与个人参考发行版分离

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| `cognition/diana.py` 性质 | 已实现 | `src/len_bot/cognition/diana.py:1`（57 行，无 import） | `"""Operator-authored persona preset; reference sources: docs/persona/diana.` | 纯数据模块，**不在上下文装配/聊天主路径** |
| 角色名硬编码点 | 部分实现 | `diana.py:8`, `:56` | `"identity_name": "嘉然",`／`"segments": segments, "tag": "嘉然", "missing_media_refs": missing})` | 仅此文件把角色名写进逻辑 |
| diana 导入方（真实） | 已实现 | `src/len_bot/events/store.py:990` | `from len_bot.cognition.diana import PERSONA, MEDIA_REF_TAGS, build_examples` | 另一处是测试（见下） |
| 面板模板链路（跨 4 层） | 已实现 | `store.py:988` → `web/query_service.py:753` → `web/routes/settings.py:251` → `frontend/src/views/AgentSettingsView.vue:245` | `@router.get("/persona/diana")`／`const result = await api('/api/settings/persona/diana')` | 这是**唯一必须动的角色专有点**；UI 文案「查看嘉然模板」（`AgentSettingsView.vue:312`） |
| 测试引用已断 | 部分实现（破损） | `tests/test_persona_upgrade.py:12` | `from len_bot.cognition.diana import PERSONA, PRESET_ID, PREVIOUS_PERSONA, PREVIOUS_EXAMPLES` | `PREVIOUS_PERSONA`/`PREVIOUS_EXAMPLES` 在当前 `diana.py` **不存在**（grep 0 命中）。未运行测试，仅静态确认符号缺失 |
| 通用默认配置文件 | 已实现 | `lenbot.config.example.json`（git 跟踪） | `"identity_name": "Len",`／`"character_context": "",`／`"identity_persona": "群聊助手。"` | **样例本身已是通用默认**；`"shadow": true`（见 S5-01 矛盾 4） |
| 个人发行版=活配置 | 已实现（未跟踪） | `lenbot.config.json`（`git ls-files` 未匹配） | `"identity_name": "嘉然",`（:74）／`"address_names": ["然比","嘉然","然然"]`（:75-78） | 靠“跟踪的 example vs 未跟踪的 config”区分 |
| preset 开关／第二配置入口 | 未找到 | `config_store.py:167-176`（`RootConfig` 字段） | 无 `preset` 字段；`grep -rin preset src/len_bot --include=*.py` 仅 3 处，全在 `diana.py` | **“两份清晰配置入口”目前只有 example 文件，无独立第二份发行文件** |
| 第二角色引擎 | 未找到 | `cognition/` 仅一个 `diana.py` | 无引擎二选一分支 | 符合“不建第二角色引擎” |
| 人物参考机制（通用） | 已实现 | `src/len_bot/config.py:17-21,106`；`media/models.py:4` | `character_reference_assets: list[CharacterReferenceAsset] = Field(default_factory=list, max_length=40,`／`CHARACTER_REFERENCE_TAG = '人物参考'` | 只要求英文 `character_key`，不绑定具体人物 |
| 人物参考×表情隔离 | 已实现 | `media/service.py:271-272`；`media/store.py:218-220` | `raise ValueError('人物参考与反应表情使用不同标签')`／`raise ValueError('人物参考与反应表情分开使用；...')` | 表情目录显式排除人物参考（`store.py:86-91`） |
| 跨插件字体依赖 | 部分实现 | `group_summary/config.py:14`；`bilibili_live/plugin.py:194` | `"/asoul_calendar/resources/font.ttf"` 同源 | **通用功能依赖 A-SOUL 插件目录资源**，是第二处真实耦合 |
| 表情频率逻辑 | 已实现（通用） | `runtime/attention_config.py:66`；`config_store.py:130-133`；`context.py:1549-1552` | `def effective_sticker_preference(root, scene_id=None) -> str:`／`sticker_preference: Literal['natural', 'slightly_more'] \| None` | 与角色无关；符合“不大改表情频率” |
| 素材目录约定 | 未找到（代码无引用） | `media/schedule-avatars/{乃琳,贝拉,嘉然,思诺,心宜}`（313 文件） | `grep schedule-avatars` 在 src/docs **零命中** | 唯一键是 `lenbot.config.json:1226-1230` 的绝对路径；属运营约定 |

### S4-06 群聊体验的真实观察记录

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| 现场记录模板 | 已实现 | `deploy/linux/release-evidence.template.md:1` | `# 发布现场记录模板`／`未观察项写“未运行”，不能拿构建结果代替实际链路。` | 模板含 commit、备份范围、A01—A30 表 |
| A01—A30 验收编号沿用 | 已实现 | `docs/LenBot_成熟开源项目路线书_20260921.md:664-680` | `| 观察场景 | 应看见什么 | 可接受证据 | 没有现场证据时 |` | 复用而非新建测试系统 |
| 观察记录落点 | 已实现 | `deploy/linux/release-evidence.template.md:3` | `阶段状态只归纳到 docs/iteration.md。未观察项写“未运行”` | 无 |
| 真实业务案例（可公开脱敏） | 未找到 | `docs/evaluation/runs/`（空目录，0 文件，未跟踪） | — | **S4-06 交付物“可公开脱敏的行为案例与剩余问题清单”不存在**；`docs/iteration.md` 只有交接剩余项 |
| 自动裁判／成功指标 | 未找到 | `docs/iteration.md` 反复声明未运行 | `没有新增、修改或运行测试、夹具、断言式探针...`（iteration.md 收尾段） | 符合“不自动裁判” |

---

## S5｜交付别人能安装和管理的产品

### S5-01 唯一最小安装路径

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| README 安装路径 | 已实现 | `README.md:9-14` | `uv sync` / `cp lenbot.config.example.json lenbot.config.json` / `(cd src/len_bot/web/frontend && npm ci && npm run build)` / `uv run len-bot` | 四行单块 |
| operations 安装路径 | 部分实现 | `docs/operations.md:21,24,56-60,338-343` | `从项目根目录运行，不在其他目录搜索配置。首次部署先执行 `uv sync``／`uv run len-bot` | **拆到三处，运行手册没有“唯一最小路径”块** |
| Dockerfile | 已实现 | `deploy/linux/Dockerfile:5,14,15,22,33,34,36` | `FROM node:22-slim AS frontend`／`FROM python:3.13-slim`／`RUN python -m pip install --no-cache-dir uv==0.12.13`／`RUN uv sync --locked --no-dev --no-editable`／`ENTRYPOINT ["/opt/lenbot/.venv/bin/len-bot"]` | **无 `EXPOSE`/`VOLUME`/`HEALTHCHECK`** |
| compose 端口与卷 | 已实现 | `deploy/linux/compose.yaml:23-41` | `"127.0.0.1:11307:11307"`／三处 bind（control / var-lib / local_plugins ro） | `read_only: true`（:15）、`stop_grace_period: 90s`（:14） |
| 三种 worker 镜像 | 已实现 | `containers/{workspace,browser,media}/Dockerfile` | 同 `python:3.13-slim`；worker UID `65532` | 与 Bot `10000` 有意区分（`gateway.config.example.json:13` `"container_user": "65532:65532"`） |
| pyproject 元数据 | 已实现 | `pyproject.toml:2,3,9,27,36` | `name = "len-bot"`／`version = "0.1.0"`／`requires-python = ">=3.13"`／`len-bot = "len_bot:main"`／`build-backend = "uv_build"` | `[tool.uv.build-backend]` :43-45 排除前端 |
| 停机与备份（覆盖 db + 文件卷） | 已实现 | `docs/operations.md:214,232-233,240-242,246` | `## 停机、备份与结构切换`／`sqlite3 ... '.backup ...'`／`cp -R /绝对路径/media ...`／`使用持久文件时同批备份 file_assets/，使用本地插件数据时备份 plugins/` | `deploy/linux/README.md:118-124` 有对等命令 |
| 高成本能力默认关闭 | 部分实现 | `docs/operations.md:17,121,369,383,393`；`lenbot.config.example.json` | `runtime.heartbeat_enabled 与 time.sleep_start/sleep_end 默认关闭`／`network_python_enabled | 默认 false` | 插件样例 `enabled:false`；**但 Shadow 表述与样例相反，见差异 4** |
| Python 版本是否写进最小路径 | 未找到 | `pyproject.toml:9`／`.python-version:1`／`Dockerfile:14`／`ci.yml:21` | `requires-python = ">=3.13"` | **README 与 operations 均未写 3.13** |
| `uv.lock` 是否被最小路径引用 | 未找到 | `deploy/linux/README.md:38,82` | `LenBot 使用 uv 0.12.13、项目 uv.lock` | **README:10 与 operations:21 用未锁定 `uv sync`，与 `Dockerfile:22 --locked` 不衔接** |
| 卷是否在 README 出现 | 未找到 | `README.md:51`（仅一跳链接） | — | README 完全未写卷 |

### S5-02 配置与能力状态的单一界面

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| capability_status 实现 | 已实现 | `src/len_bot/web/capability_status.py:16,51` | `CARDS`（10 张卡）／`def capability_status(query, scene_id=None, requester=None)` | 非枚举，返回嵌套 dict |
| 生命周期状态值 | 已实现 | `src/len_bot/plugins/host.py:47`；`capability_status.py:93` | `state: str = "loaded"  # loaded / enabled / disabled / error`／`'state': 'absent'` | 值集分散在两个 host 方法，无单一 enum 拥有 |
| 可用性状态值 | 已实现 | `host.py:655-677` | `'unconfigured'`／`'plugin_disabled'`／`'invalid_system_source'`／`'capability_denied'`／`'scene_not_enabled'`／`'callable'` | 中文标签 `capability_status.py:71-79` |
| 群工作台 | 已实现 | `src/len_bot/web/routes/setup.py:11,21`；`frontend/src/views/ScenesView.vue`；`components/SceneSettingsForm.vue:172,210` | `@router.put('/group-quick')`／`await api('/api/setup/group-quick',{method:'PUT',...` | 旧 `cockpit.py:68,76` 端点仍在但前端无调用者 |
| Schema 表单 | 已实现 | `host.py:641-642`；`frontend/src/components/PluginConfigFields.vue:11`；`lib/pluginConfig.js` | `'config_schema': spec.config_model.model_json_schema(),`／`'scene_config_schema': spec.scene_config_model.model_json_schema(),` | 非标量字段回落原始 JSON textarea（`PluginConfigFields.vue:155`） |
| 已配置 | 已实现 | `web/query_service.py:1633`；`PluginsView.vue:53` | `item['configured'] = bool(saved and saved.config is not None)`／`'已保存为启用'` | 无 |
| 已启用 | 已实现 | `query_service.py:1631`；`PluginsView.vue:54` | `item['active_enabled'] = bool(item['enabled'] and item['state'] == 'enabled')`／`'当前已加载并启用'` | 无 |
| 权限不足 | 已实现 | `capability_status.py:208-212`；`CapabilityCards.vue:31` | `decision = authority.check(capability, subject, now=now)` | 无 |
| 部署缺失 | 部分实现 | `capability_status.py:140-146`；`CapabilityCards.vue:30` | `{'label': '缺失条件', 'value': '媒体片段需要 Gateway 媒体 worker'}` | 自由文本 `{label,value}`，**无机器可判的 deploy-missing 标记** |
| 实际调用结局 | 已实现 | `capability_status.py:201,222,229,247` | `recent_observation`／`recent_execution`／`recent_delivery`／`recent_platform_action` | 无 |
| 需重启说明不自动重启 | 已实现 | `runtime/agent_runtime.py:81,332,334,432`；`CapabilitiesView.vue:58`；`routes/websocket.py:65` | `self.restart_required = False`／`"message": "OneBot 配置已写入根文件，重启后生效"` | grep `execv/systemctl/auto_restart` **无命中** |
| 保存成功≠运行已生效 | 已实现 | `routes/plugins.py:81-83`；`host.py:54-55`；`plugins.py:70-71` | `class PluginConfigurationApplyError(RuntimeError):`／`"""The root file was saved, but the plugin did not apply it."""`／`raise HTTPException(409,{'message':str(error),'config_saved':True})` | 读回态 `PluginsView.vue:299` |
| 秘密占位不回写 | 已实现 | `plugins/credentials.py:105,58,21`；`query_service.py:1648,1659-1660` | `def public_config(config, schema)`／`field["writeOnly"] = True` | 缺口：`secret_values` 只清洗插件 `last_error`（`query_service.py:1663-1669`），其他自由文本字段不回写保护 |
| 群切换不显示上一群数据 | 已实现 | `frontend/src/composables/useRequestGuard.js:11`；`SceneSettingsForm.vue:33-35,174` | `return () => !disposed && own === current && key === selection()`／`if(data.scene_id!==id)throw new Error('返回的本群设置身份与读取目标不一致，未采用。')` | `ScenesView.vue:342-351` 用整数代次而非共享 composable（等价但风格不一） |

### S5-03 数据保留、迁移与备份合同

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| 持久数据类别（代码证据） | 已实现 | `events/store.py:74,93,98,102,108,123,144,154,168`；`memory/store.py:53`；`memory/index.py:22`；`memory/history.py:93`；`tools/observations.py:11`；`media/store.py:37`；`media/files.py:269`；`job_store.py:312,328`；`call_store.py:94,118`；`execution/journal.py:92,105,110`；`skills/store.py:46,49,52`；`platform_actions.py:8`；`memory/interests.py:47` | 各 `CREATE TABLE IF NOT EXISTS ...` | 完整类别表见本文件末「反向引用清单」前的数据表 |
| 凭据存放 | 已实现 | `plugins/credentials.py:10`；`events/store.py:144` | `there is deliberately no separate secret store`／`CREATE TABLE IF NOT EXISTS dashboard_users (` | 凭据在根配置，无独立密钥库 |
| 迁移机制 | 部分实现 | `events/store.py:59-70`；`job_store.py:324-326`；`call_store.py:105-108` | `await self.initialize_observations()`／`if 'budget_json' not in columns: await self._db.execute('ALTER TABLE agent_jobs ADD COLUMN budget_json TEXT')` | 启动 DDL + 定点 ALTER；**无 `schema_version` 账本、无迁移 runner** |
| 旧结构拒启 | 已实现 | `events/store.py:49,53` | `raise RuntimeError("非现行会话结构；保持停机，使用对应旧版本完成离线处理后再启动")` | 只能靠这两处探针判断库世代 |
| `migrations/` 目录现状 | 未找到 | `src/len_bot/migrations/`（仅 `__pycache__/`，2 个 `.pyc`） | `git ls-files src/len_bot/migrations` → 空 | 真实 `.py` 在 d87b648 被删（`attention_work.py \| 161 -`） |
| 保留政策／TTL／自动清理 | 未找到 | — | 现存删除只有派生索引 `DELETE FROM memory_index`（`index.py:239,247`）、临时表（`store.py:826`）、运营删样例（`store.py:1099`）、显式 Reset（`store.py:186`） | **事件/认识/摘要/聊天媒体无保留期限**；`open_loops` 只改状态不删（`store.py:1260 UPDATE open_loops SET status = 'expired'`） |
| 文件有效期语义 | 部分实现 | `media/file_config.py:9`；`files.py:298` | `retention_seconds: int = Field(default=604800, ...)`／`or asset.expires_at <= store.clock()` | 有效期**只阻止新上传**（`docs/operations.md:389`） |
| 备份合同（db + 卷） | 已实现 | `docs/operations.md:233,240-242,246,252` | `在新的备份目录保存当前代码提交或源码归档、实际根配置、指定 SQLite 数据库及其完整媒体目录。`／`备份整个库，包含预占、调用、执行/命令、兴趣、平台动作和文件资产身份` | 无哈希清单（`operations.md:233` `不生成文件指纹或哈希清单`） |
| 回退限制 | 部分实现 | `docs/operations.md:269`；`architecture.md:427` | `不能将数据库回退当作撤销真实发送的方法`／`工作结束/取消/授权撤销不回滚收藏，也不重放 POST。` | **“仅代码回退可用条件”只写在任务卡 `:970`，运行手册未给逐条判定清单** |
| 结构转换离线执行 | 部分实现 | `scripts/migrate_observation_config.py:1`；`docs/operations.md:254-259` | `"""One-time, offline attention configuration conversion; never used at startup.` | **只有配置转换脚本；无 DB 结构离线脚本或 CLI 子命令**（`main.py:110` 无 argparse） |
| 保留原始事件与业务身份 | 已实现 | `docs/operations.md:216,234` | `不改原事件或业务身份，也不新建纠正数据`／`旧结构由对应旧版本处理，不在启动时猜测补列` | 无 |

### S5-04 运营者的日常工作入口

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| 页面清单 | 已实现 | `frontend/src/router/index.js:15-31` | `/scenes`「群聊工作台」／`/jobs`「信息工作」／`/memories`「认识与记忆」／`/activity`「运行记录」／`/agent/capabilities`「工具能力」 | 无 |
| SceneInspector | 已实现 | `ScenesView.vue:16,495,497`；`components/SceneInspector.vue` | `<SceneInspector :active="Boolean(eventId)" ...`／`aria-label="消息关联检查抽屉"` | 只读视图，无操作按钮 |
| Activity | 已实现 | `views/ActivityView.vue`；`routes/cockpit.py:441`；`routes/models.py:317` | `@router.get("/events/{event_id}")`／`@router.get("/usage/{call_id}")` | 日志标签页不回链，见下 |
| Jobs cancel/revise/resume | 已实现 | `routes/cockpit.py:242-244,266-267`；`JobsView.vue:276,348` | `@router.post("/jobs/{job_id}/{operation}")`／`event = await runtime.record_operator_event(job["scene_id"], f"job_{operation}", user, ...)` | 操作身份记入 `OPERATOR_ACTION` |
| Memory 撤销 | 已实现 | `cockpit.py:411,417`；`MemoryView.vue:137` | `@router.post("/memories/{memory_id}/refute")`／`>记录依据并撤销</v-btn>` | 无 |
| 操作身份存储 | 已实现 | `runtime/agent_runtime.py:592-593`；`events/models.py:32` | `actor_id=f"operator:{operator}",`／`EventType.OPERATOR_ACTION` | **缺口：群设置保存硬编码 `'panel'`**——`agent_runtime.py:482` `record_operator_event(scene_id, 'scene_settings', 'panel', {'policy_changed': True})`，`setup.py:25` 传的 `operator_id=user` 只进了 grant 修订 |
| 任意 DB 写按钮／端点 | 未找到 | `query_service.py:89,764,1126`（仅服务端字面 SQL） | `cursor = await self.runtime.event_store._db.execute(sql, params)` | 无 SQL/表编辑器端点或 UI |
| 日志条目回链来源 | 部分实现 | `web/log_ring.py:19-24`；`ActivityView.vue:142` | 只存 `{"timestamp", "level", "component", "message"}` | **无 `event_id/job_id/call_id`**，故“日志入口能回到来源”对事件/调用成立、对运行日志不成立 |
| 脱敏诊断导出 | 未找到 | — | 仅文件下载 `cockpit.py:156,202` | 任务卡只把它列为 S1-06 交付物（`:326,329`）；已有部分构件 `query_service._public`（:101-114）与 `credentials.secret_values` |

### S5-05 四类读者的文档

| 文档 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| README（用户快速开始） | 已实现 | `README.md:7-14,20-27` | `## 开始`／`## 当前能做什么` | 版本引用未找到；插件作者与贡献流程只有链接 |
| operations（运营者） | 已实现 | `docs/operations.md:3,29-42,214-271,299-316`（459 行） | `面向运营者。本文是从初始化到停机、备份、升级和故障处置的可执行最短路径` | 无版本引用 |
| plugins（插件作者） | 已实现 | `docs/plugins.md:3,7-17,57-71,73-127`（200 行） | `面向维护 LenBot 业务插件的开发者` | 无版本引用 |
| architecture（核心贡献者） | 部分实现 | `docs/architecture.md:3,9,22`（526 行） | `面向维护者`／`## 主链与所有权` | 有所有权表，**无评审/合并/发布流程** |
| 本地链接有效性 | 已实现 | README 10/10、operations 13/13、plugins 10/10、architecture 9/9 全部存在 | — | 四份文档**零 markdown 图片**，无过时截图问题 |
| 版本关联 | 未找到 | — | 四份文档均无 `0.1.0`/`vX.Y.Z` | `0.1.0`（`pyproject.toml:3`）与 `0.3.0`（`frontend/package.json:4`）并存且无文档说明 |
| 读者 (d) 变更流程 | 未找到 | 唯一流程文本在 `docs/plan/README.md:100-113`（未跟踪） | `一个 PR 对应一项用户结果，代码与所属文档同批` | README 文档表（`README.md:45-56`）未链接它 |

### S5-06 支持与信任边界

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| 支持矩阵 | 未找到 | 仅片段 `docs/plugins.md:194`；`README.md:29` | `### Core 支持范围`／`## 当前能力边界` | 任务卡 `:870` 的“支持矩阵”是待实现合同 |
| SECURITY／私报渠道 | 未找到 | `find . -iname "SECURITY*"`（排除 .git/.venv）无结果 | — | 任务卡 `:1100` `保留漏洞私报渠道` 未落地 |
| 适配器能力 | 部分实现 | `src/len_bot/adapters/onebot.py:286-307`；`file_upload.py:1,8` | `"""One explicitly selected file-upload implementation, not a generic OneBot promise."""`／`PROTOCOLS = {'napcat': ..., 'snowluma': ...}` | **无“适配器 × 消息/文件能力”表**；`adapters/` 只有两个文件 |
| 插件信任级别 | 未找到 | `plugins/models.py:138`；`catalog.py:74-79` | `class PluginPermission(StrEnum):`／`definition.loader.exec_module(module)` | 只有功能权限枚举；外部插件**进程内无沙箱加载**；`信任级别` 仅在任务卡 `:870` |
| 已知限制 | 部分实现 | `README.md:29-39`；`architecture.md:53` | `## 当前能力边界`／`Python 与浏览器**尚不构成已验收的生产隔离**` | 无字面“已知限制”章节（任务卡 `:873` 要求） |

---

## S6｜建立可重复的发布与升级纪律

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| CI 实际跑什么 | 已实现 | `.github/workflows/ci.yml:21,24,27,38,42` | `run: uv python install 3.13`／`run: uv sync --no-dev`／`run: uv run --no-dev python -m compileall -q src/len_bot`／`run: npm ci --no-audit --no-fund`／`run: npm run build` | 共 5 步：装 uv → Python 3.13 → 依赖 → 编译 → 前端构建 |
| CI 是否与 AGENTS“不新增测试”一致 | 已实现（一致） | `AGENTS.md:14`；`ci.yml`（grep `pytest\|test` 无实质命中） | `不新增、修改或运行测试、夹具、断言式探针、自动截图、覆盖率任务；不改检查来制造通过结果。` | **CI 不运行任何测试，与约束一致**；`pyproject.toml:47-51` 的 dev 组含 pytest 但 CI 用 `--no-dev` |
| CI 触发分支 | 部分实现 | `ci.yml:5,7` | `branches: [ master, main ]` | **当前工作分支 `feat/s0-product-contract` 不在触发范围**；`origin/HEAD -> origin/master` |
| CI 是否锁定依赖 | 部分实现 | `ci.yml:24` vs `Dockerfile:22` | `uv sync --no-dev`（未锁） vs `uv sync --locked --no-dev --no-editable` | CI 与发布镜像的解析方式不一致 |
| uv 版本固定 | 部分实现 | `ci.yml:18`；`Dockerfile:15` | `version: "latest"`／`RUN python -m pip install --no-cache-dir uv==0.12.13` | **CI 用 latest、镜像钉 0.12.13**；任务卡 S5-01 要求“固定依赖和镜像版本” |
| 构建可重复性 | 部分实现 | `ci.yml:27`；`README.md:16` | `python -m compileall -q src/len_bot` | 只有语法编译，无产物比对 |
| 版本号 | 部分实现 | `pyproject.toml:3`；`frontend/package.json:4` | `version = "0.1.0"`／`"version": "0.3.0"` | 两处版本无关联说明；`git log -p --follow pyproject.toml` 显示 version 从首版起**从未变更** |
| CHANGELOG／release notes | 未找到 | `find . -maxdepth 3 -iname 'CHANGELOG*' -o -iname 'RELEASE*'`（排除 node_modules） | 仅 `deploy/linux/release-evidence.template.md`（是**现场记录模板**，不是变更日志） | 无 `CHANGELOG.md`、无 release notes |
| 版本标签 | 未找到 | `git tag -l` | `backup/pre-rewrite-20260918-182507`／`…-dev-social-agent-con`／`…-m0-foundation` | **3 个标签全为 backup 前缀，无任何版本标签**（非 backup 计数 = 0） |
| 候选/release 分支 | 未找到 | `git branch -a` | `master`／`dev/social-agent-con`／`social-agent-m0-foundation`／`feat/s0-product-contract` | 无 `release/…` 分支 |
| 候选版验收材料 | 部分实现 | `deploy/linux/release-evidence.template.md`；`docs/iteration.md` | `Commit / Parent / 审阅 HEAD：` | 模板已备，**无任何填写实例** |
| 升级/回退决策树 | 部分实现 | `docs/operations.md:250-271` | `### 更新当前实例`／`回退先停止新能力入场，确认原执行资源和发送状态` | 无独立决策树；“仅代码回退条件”缺逐条清单（同 S5-03） |
| 插件 API 世代 | 未找到 | `src/len_bot/plugins/api.py:1-18` | `"""Public plugin authoring entry point; importing it does not start resources."""`／`__all__` 列表 | **无任何版本常量**（grep `API_VERSION\|HOST_API\|contract_version` 零命中）；插件自身版本分散（0.1.0–2.0.0，见反向引用清单） |
| 发布范围决定 | 未找到 | — | — | 任务卡 S6-06 是产品决策，本次未核实 |

---

## S7｜用独立使用者证明框架成立

| 能力 | 判定 | 实际符号（文件:行号） | 证据摘录 | 缺口/未确认 |
|---|---|---|---|---|
| Issue 模板 | 部分实现（工作区有，未跟踪） | `.github/ISSUE_TEMPLATE/task.md:1-49`；`bug.md:1-38` | `name: 路线任务`／`event_id / episode_id：`／`job_id / revision：` | `git ls-tree HEAD -- .github` 仅 `ci.yml`；**模板在本次会话中才出现且 `?? ` 未跟踪** |
| PR 模板 | 部分实现（工作区有，未跟踪） | `.github/PULL_REQUEST_TEMPLATE.md:1-33` | `## 本次兑现的用户结果`／`## 核对结果`／`明确未执行与未确认：` | 同上，未跟踪 |
| 贡献说明 | 未找到 | `CONTRIBUTING.md` 缺失 | — | 唯一流程文本在未跟踪的 `docs/plan/README.md:100-113` |
| SECURITY 说明 | 未找到 | `SECURITY.md` 缺失 | — | 同 S5-06 |
| CODE_OF_CONDUCT / SUPPORT / GOVERNANCE | 未找到 | 均缺失 | — | 路线书 `:629` 明确“不必一开始就引入复杂 CLA、委员会或企业 SLA”，故缺失可接受，但需记录 |
| 独立使用者闭环 | 未找到 | — | — | S7-01/S7-02 无任何落地物 |
| 贡献/问题流程运行 | 未找到 | — | — | S7-03 无 triage／评审记录 |
| 支持与维护策略 | 部分实现 | 路线书 `:599-637`（第 9 节，计划文本） | `任务状态可保持简单：待确认 → 可实施 → 实施中 → 待复核 → 已验收 → 已发布` | 有政策草案，无落地操作 |
| 1.0 门槛 | 部分实现 | 路线书 `:696-707` | `## 11. 1.0 的发布门槛`／`这些是建议采纳的发布合同，不是本次已验证结果。` | 6 条门槛文本齐备，无任何一条有证据 |
| 许可证文件（只报告事实） | 未找到（根）／部分（插件内） | 根目录无 `LICENSE*`；`src/len_bot/plugins/builtin/asoul_calendar/LICENSE`（AGPL v3，661 行）；`src/len_bot/plugins/builtin/group_summary/LICENSE.upstream`（MIT，21 行） | `GNU AFFERO GENERAL PUBLIC LICENSE`／`MIT License`／`Copyright (c) 2025 Helian Nuits` | **仓库根无许可证文件**；`pyproject.toml` 无 `license`/`classifiers`；README 无许可段落；**未代替维护者选择，仅报告事实** |
| 素材来源记录 | 已实现 | `plugins/builtin/{asoul_calendar,asoul_dynamics,group_summary,gscore_adapter}/SOURCE.md` | `上游许可证原件保留为 [LICENSE](LICENSE)（GNU AGPL v3）。`／`此版本上游未包含 LICENSE 文件；这里保留来源事实，不为上游补写或假定许可证。` | 各插件来源与许可证状态已分别记录，未混为一谈 |

---

## 未确认与读不到

1. **未读 `len_bot.db`**（1 GB）：所有 schema 结论均来自源码 `CREATE TABLE` 文本，**未确认实际库的表结构世代、行数与历史数据形态**。
2. **未读媒体内容**：`media/`（2803 条目）与 5 张 `docs/persona/asoul/reference-images/20260921/*.png` 未打开；素材是否可读、像素是否已登记未确认。
3. **未做任何运行验证**：未编译、未构建前端、未启动服务、未调用模型或平台。因此**所有“已实现”仅指源码存在，不代表该组合已支持或已部署**。
4. **未运行 `tests/`（32 个文件）**。`tests/test_persona_upgrade.py:12` 导入不存在的 `PREVIOUS_PERSONA`/`PREVIOUS_EXAMPLES` 是**由静态符号缺失推断**，实际失败表现未确认；其余测试是否仍与现结构一致未核实。
5. **Shadow 默认语义未确认**：`lenbot.config.example.json` 的 `"shadow": true` 与 README/operations 的“全局 Shadow 与样例默认均为停用”字面相反。`config_store.py` 的 `shadow: bool` 无默认值（样例即唯一默认），代码路径显示 `shadow` 为真时行动记 `origin_mode="shadow"` 而不实发（`actions/queue.py:103-124`、`gate.py:520`）——**倾向“true=安全（不实发）”，但文档措辞会误导运营者**，需维护者定稿。
6. **`docs/plan/` 与 `.github/` 模板的最终归属未确认**：核对期间它们从无到有且始终未跟踪。本文件写入后，`git status` 会再增一项。
7. **S4-01 的“是否真的没漏掉明确问题”无法由源码证明**：`attention.py:132` 的 `certain` 只表达“这是明确搭话”，不等于答案正确或未被漏读。
8. **无预算快照的老工作（pre-`budget_json`）恢复行为**未核实：`job_runner.py:393-394` 明示退回当前配置，实际影响未评估。
9. **面板是否展示修订历史**未核实：`job_store.py:570` 就地覆盖、无独立版本表，`query_service` 全量读取路径未逐条检查。
10. **`.dockerignore` 与根上下文**：以 `**` 起排除后仅重含 `src/`、`containers/`、`deploy/linux/`；`deploy/dev/` 被排除，且根上下文仍会扫描后丢弃 1 GB `len_bot.db`、`media/`、`.venv/`（未命名忽略）。是否影响构建未实测。
11. **`.gitignore` 未忽略 `file_assets/`、`plugins/`、`local_plugins/`**（`git check-ignore -q file_assets` 退出 1），而 `docs/operations.md:246` 视其为运行数据；实际是否产生未跟踪噪声未核实。
12. **S6-06 / S7-06 的发布与 1.0 决定**属维护者决策，本次不作判定。

---

## 与任务卡原假设的差异

1. **任务卡把 S4 当作“待实现的目标”，但 S4-03／S4-04 的机制基本已存在。** 任务卡 `:667` 列 job_runner/JobStore/work_context/actions，`:688` 列 scheduler/heartbeat/bilibili_live——这些符号全部存在且已接线；`reuse_work_ref`（`:664` 的“成果复用”）在 9178c34 已落地并有 4 处引用。**S4-03／S4-04 更接近“补证据”而非“写代码”。**

2. **任务卡 `:688` 写 `bilibili_live`，实际插件 ID 是 `bilibili_live_sensor`**（`plugins/builtin/bilibili_live/__init__.py:25`），样例配置键也是 `bilibili_live_sensor`。按 `bilibili_live` 搜索源码**零命中**（`grep -w bilibili_live src/` → 0），只有目录名是 `bilibili_live`。核对时需用真实 ID。

3. **任务卡 `:667` 写 `JobStore`，源码中无该名字的类。** 实际是 `JobStoreMixin`（`runtime/job_store.py:67`），`grep -w JobStore src/` → 0 命中。任务卡用的是概念名而非符号名（S0-01 报告也应统一此口径）。

4. **任务卡把“两份清晰配置入口”（`:716`）当作待造的交付物，但通用侧已存在。** `lenbot.config.example.json` 已全为通用值（`"identity_name": "Len"`、`"character_context": ""`），实质就是通用预设；个人发行版是**未跟踪的** `lenbot.config.json`。**缺的不是“通用配置”，而是“把这两者说成两条正式入口”的文档与命名**，代码侧只需处理 diana 模板链路与跨插件字体依赖两处。

5. **任务卡 `:859` 假设需要“页面改名同步旧→新入口说明”，实际 README 的 10 个与 operations 的 13 个本地链接全部有效，四份文档零截图。** S5-05 的“过时截图删除”在当前仓库不适用。

6. **任务卡 `:927` 的“构建可重复”在 CI 层面只做到语法编译**（`ci.yml:27 compileall`），且 CI 触发分支不含当前工作分支、`uv` 用 `latest`、依赖未 `--locked`。**“固定现有构建入口”要事实是“先固定 CI 自身”，不是固定一件已固定的事。**

7. **任务卡 `:866`/`:870` 与 `:1096`/`:1100` 把“支持矩阵 / SECURITY / 漏洞私报”列为交付物，路线书 `:627` 也要求“安全问题提供私下渠道”。** 现状是**四项全无**（无 SECURITY.md、无支持矩阵、无信任级别、无已知限制章节）。这属 S5-06/S7-04 的真实新建工作。

8. **任务卡 `:943` 要求“日常使用短功能分支；候选期只收缺陷”**，现状有 4 个本地分支但**无 `release/…`、无非备份 tag、无 CHANGELOG**。分支习惯已符合，发布纪律为零。

9. **任务卡 S5-01 `:768` 要求“从空环境到第一条真实答复的安装文档与同版观察”，现状连 Python 版本（3.13）都未写进 README/operations**，且 README 用一个四行块包含了部署镜像会自建的前端构建。**“唯一最小路径”目前反而是三处分散且互相不完全对齐。**

10. **路线书 `:629` 明确“不必一开始就引入复杂 CLA、委员会或企业 SLA”**，因此 `CONTRIBUTING.md`/`CODE_OF_CONDUCT.md` 缺失**不构成偏差**；但路线书 `:627` 要求的**私下安全渠道**属硬缺口，与前者不同，不宜一并豁免。

11. **任务卡自己的口径与本次核对一致，但工作量假设可能偏高。** 任务卡 `:4` 已标注 `基线：9178c34958d0d54843665e56e72012bb3021b644`，`:7` 写明“**已存在且同版证据充分的能力可以登记保留，不为了关闭任务再改一次代码**”，`:13` 要求“先确认现有代码是否已满足目标，再选择‘保留并补证据’‘定向修改’或‘延期’”。而对照发现多数 S4 机制在 `9178c34` 之前就已存在；`docs/iteration.md` 的 N00—N05 交接段（`:9-26`）同样自述这些能力已实现，只是**“未验收、未部署”**。因此**本报告的判定与任务卡 `:7`/`:13` 的处置口径一致**，但意味着 S4 若按“待新写”估工会偏高，宜优先走“保留并补证据”。

---

## 反向引用清单

### 常量与配置键
- `src/len_bot/config.py:59-72` — attention 六个可调键
- `src/len_bot/config.py:76-78` — `heartbeat_enabled`（默认 false）、`heartbeat_topics`
- `src/len_bot/config.py:103-110` — identity persona 五字段
- `src/len_bot/runtime/attention.py:13-22` — `ADDRESSED_REASONS`／`OBLIGATION_REASONS`／`UNAMBIGUOUS_ADDRESS`／`ENGAGED_REASONS`
- `src/len_bot/runtime/attention.py:76` — `ttl = self.config.attention_opportunity_ttl_seconds`
- `src/len_bot/runtime/heartbeat.py:13,17` — `SLOT_SECONDS = 30 * 60`／`def slot_id(now)`
- `src/len_bot/cognition/context.py:31` — `STABLE_SECTIONS = {'persona'}`
- `src/len_bot/plugins/catalog.py:22-38` — `PluginSpec` 全部字段
- `src/len_bot/cognition/proposals.py:94` — `_RELATIONS`
- `src/len_bot/cognition/jobs.py:195` — JobResult status Literal
- `src/len_bot/scheduler/models.py:6-17` — `TaskStatus` 11 个值

### 插件版本表（`plugin.py`/`__init__.py` 的 `PluginSpec.version`）
`asoul_calendar 1.0.0`／`asoul_dynamics 1.0.0`／`bilibili_content 1.0.0`／`bilibili_live_sensor 0.1.0`／`browser_agent 0.1.0`／`group_summary 2.0.0`／`gscore_adapter 0.1.0`／`interest_share 0.1.0`／`link_parser 1.0.0`／`media_analysis 0.1.0`／`python_workspace 0.2.0`／`web_search_tool 1.1.0`／`workspace 0.2.0`
（无宿主 API 世代常量；`plugins/api.py:13-18` 只有 `__all__`）

### 数据目录表（代码实际写入，供 S5-03 使用）
| 类别 | 位置 | 证据 |
|---|---|---|
| events | 表 `events` | `events/store.py:74` |
| events FTS（可重建） | 虚表 `events_fts` | `events/store.py:98` |
| 待处理运行事件 | `pending_runtime_events` | `events/store.py:93` |
| session 状态 | `scene_sessions` | `events/store.py:102` |
| 等待 open loops | `open_loops` | `events/store.py:108` |
| 提醒 tasks | `tasks` | `events/store.py:123` |
| 面板用户 | `dashboard_users` | `events/store.py:144` |
| traces／语音样例 | `traces`／`voice_exemplars` | `events/store.py:154,168` |
| 认识 | `memories` | `memory/store.py:53` |
| 可重建向量索引 | `memory_index` | `memory/index.py:22` |
| 摘要 | `history_batches`／`history_origins` | `memory/history.py:93` |
| 公共兴趣 | `public_interests` | `memory/interests.py:47` |
| 工具材料 | `tool_observations` | `tools/observations.py:11` |
| 素材元数据 | `media_assets` | `media/store.py:37` |
| 素材字节 | `<db_dir>/media/` | `media/service.py:105` |
| 文件资产元数据 | `file_assets` | `media/files.py:269` |
| 文件字节 | `<db_dir>/file_assets/` | `media/files.py:342` |
| 工作 | `agent_jobs`／`job_exchanges` | `runtime/job_store.py:312,328` |
| 调用账／预占 | `model_calls`／`usage_reservations` | `cognition/call_store.py:94,118` |
| 执行与命令 | `execution_runs`／`_events`／`_commands` | `execution/journal.py:92,105,110` |
| 网关产物 | `execution_artifacts` | `services/worker_gateway/store.py:42` |
| 技能 | `skills`／`skill_versions`／`skill_candidates` | `skills/store.py:46,49,52` |
| 平台动作 | `platform_actions` | `runtime/platform_actions.py:8` |
| 凭据 | 根 `lenbot.config.json`（无独立库） | `plugins/credentials.py:10` |

### 关键阈值与开关
- `deploy/linux/compose.yaml:24` — `"127.0.0.1:11307:11307"`
- `deploy/linux/compose.yaml:14` — `stop_grace_period: 90s`
- `deploy/linux/compose.yaml:19-20` — `mem_limit: 1g`／`cpus: 1.0`
- `deploy/linux/Dockerfile:15` — `uv==0.12.13`
- `.github/workflows/ci.yml:5,7,18,24,27` — 触发分支／`version: "latest"`／`uv sync --no-dev`／`compileall`
- `src/len_bot/cognition/proposals.py:172` — `messages:list[TurnMessage]=Field(max_length=3,`（每提交最多三条；空列表=沉默）
- `src/len_bot/plugins/builtin/asoul_calendar/LICENSE` — AGPL v3（661 行）
- `src/len_bot/plugins/builtin/group_summary/LICENSE.upstream` — MIT（21 行）

### 文档与模板
- `docs/LenBot_分阶段任务卡_20260921.md:614-1157` — S4—S7 任务卡
- `docs/LenBot_成熟开源项目路线书_20260921.md:501-594` — S4—S7 阶段合同；`:599-637` 第 9 节管理规范；`:696-707` 1.0 门槛
- `deploy/linux/release-evidence.template.md` — 现场记录模板（唯一发布材料）
- `.github/ISSUE_TEMPLATE/task.md`／`bug.md`／`.github/PULL_REQUEST_TEMPLATE.md` — **工作区存在，未跟踪**
- `docs/plan/README.md` — 路线唯一入口（未跟踪）
- `docs/evaluation/runs/` — **空目录**
- `docs/iteration.md:1-40` — N00—N05 收尾与交接剩余项（当前唯一状态记录）
