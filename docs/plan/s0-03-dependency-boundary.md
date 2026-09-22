# S0-03 依赖边界测绘（静态，只读）

> 基线资料：以下事实与源码行号限定于文中标明的核对时点；任务当前状态只见[路线入口](README.md)，后续实施见[当前任务](../iteration.md)。草案未确认部分不视为已采用。

核对基线：commit `ad41a5a71a4b5ec3693f4cc846342964c5b0b8c6`，分支 `feat/s0-product-contract`。
测绘时间：2026-09-21T14:21Z。代码基线为同 commit；工作区另有并行产出的
`docs/plan/README.md`、`delegation-plan.md`、`s0-02-...md` 未被本报告引用为证据。

只读方式：`git rev-parse`／`git status`／`grep -rn`／`read`。未运行服务、未调用模型或外部平台、
未读 `len_bot.db`、未运行测试／夹具／探针／截图、未修改任何业务文件。本报告是**静态依赖测绘**，
所有「关闭后会坏」的判断均为 import 图与配置读取点的静态推断，**不是运行验证**。

**一句话结论**：人格与角色资产已基本收敛为「根配置 + 一份可选模板」，耦合面很窄；
真正的边界破口在**内核承载了具体业务的名字**——`runtime/` 与 `cognition/` 直接写死
`interest_share`、`bilibili_live_sensor`、`bilibili_content` 的 plugin_id、事件类型与能力名，
以及 `events/models.py` 把 `LIVE_STARTED/LIVE_ENDED/PLUGIN_EVENT` 固定进核心事件枚举。

---

## 一、人格与业务耦合点

| 耦合点 | 位置（文件:行号） | 类型 | 关闭后影响（静态推断） | 拆出难度 |
|---|---|---|---|---|
| 人格原文模板 `PERSONA`（名字／口吻／角色资料全部文字） | `src/len_bot/cognition/diana.py:6-23` | 硬编码（可选资产包） | 仅 `/api/settings/persona/diana` 与该按钮失效；对话仍读根配置 | 低 |
| 模板表达样例与素材标签 | `src/len_bot/cognition/diana.py:25-57` | 硬编码（可选资产包） | 同上；样例逐条新增功能失效 | 低 |
| `preview_diana_persona()` 落在事件存储上 | `src/len_bot/events/store.py:988-1003` | 硬编码（**内核反向依赖人格**） | 核心存储类带一个人格专用方法；不影响其它能力 | 低 |
| Web 查询代理 | `src/len_bot/web/query_service.py:753-754` | 硬编码（薄转发） | 接口 404，无其它影响 | 低 |
| Web 路由 | `src/len_bot/web/routes/settings.py:251-253` | 硬编码（路由） | 同上 | 低 |
| 前端按钮与弹窗 | `src/len_bot/web/frontend/src/views/AgentSettingsView.vue:245`、`:312`、`:358` | 硬编码（界面文案） | 按钮 404；人格字段编辑本身仍可用 | 低 |
| 运行生效值 | `lenbot.config.json:74-82`（`identity_name`/`identity_persona`/`identity_core`/`conversation_style`/`character_context`） | 配置 | 不关；这是运行人格权威 | — |
| 人格注入点 | `src/len_bot/cognition/context.py:1497-1501` | 内核机制（通用字段） | 通用：读任意配置文本，无人格名硬编码 | — |
| `character_reference_assets` 字段与保存 | `src/len_bot/config.py:106`、`src/len_bot/web/routes/settings.py:271`、`:276` | 配置（通用形状） | `character_key` 正则 `^[a-z][a-z0-9_-]{0,31}$` 已是通用键，非角色名单 | — |
| 人物参考目录注入 | `src/len_bot/cognition/context.py:1466-1486`、`:1528` | 内核机制（通用） | 无人格名硬编码 | — |
| 订阅对象名单（B 站 UID／房间号／别名含 `diana`/`asoul`） | `lenbot.config.json:1071-1120` | 配置（业务数据） | 关直播／日程后为死数据 | 低 |
| 示例配置的通用人格 | `lenbot.config.example.json:49-56`（`identity_name: "Len"`、`character_context: ""`） | 配置（已经是非 A-SOUL） | 无需改动即可作非 A-SOUL 起点 | — |
| 角色原文与素材索引 | `docs/persona/diana/`（17 个文件，含 `sources/` 6 份原文、`sticker-pack-20260906.json`） | 文档（**运行不读**） | 关闭后无任何影响 | 低 |
| A-SOUL 资料审阅稿 | `docs/persona/asoul/`（资料稿、5 张原图、19 张表情审阅） | 文档（**运行不读**；`docs/persona/asoul/README.md:3-4` 自述"不是运行时目录"） | 无影响 | 低 |
| 运行媒体目录 | `/media/`（2801 项，`.gitignore:46` 未跟踪） | 数据（运行资产，无角色身份） | 运行必需，但内容与角色解耦 | — |
| 日程头像目录 | `media/schedule-avatars/{乃琳,嘉然,心宜,思诺,贝拉}`，由 `lenbot.config.json:1225-1231` 装配 | 配置 + 数据（**业务专用**） | 只影响日程卡片头像；文件由日历插件配置显式引用 | 低 |
| 卡片品牌硬编码 | `src/len_bot/cards/schedule/__init__.py:25`、`:96`；`src/len_bot/cards/bilibili/context.py:30`；`src/len_bot/cards/bilibili/template.html:261` | 硬编码（**品牌串**） | "A-SOUL LIVE"／"爱驼推送" 对所有发行版可见 | 低 |
| 跨插件字体文件 | `src/len_bot/plugins/builtin/bilibili_live/plugin.py:194`、`asoul_dynamics/plugin.py:97`、`group_summary/plugin.py` 经 `lenbot.config.example.json:171` | 硬编码（跨插件相对路径） | 关掉 `asoul_calendar` 会导致这三处兜底渲染抛错 | 中 |
| 心跳主题 | `lenbot.config.json:51`（`["A-SOUL 公开活动与视频", "嘉然"]`） | 配置（可替换） | 关掉直播后为死数据 | 低 |
| 开播公告提示词 | `lenbot.config.json:1139`（"…不把其他主播写成嘉然…"） | 配置（含人格专名） | 非 A-SOUL 发行需改写 | 低 |
| `bilibili_content` 别名 | `lenbot.config.json:1071`（`aliases: [..., "diana"]`） | 配置 | 角色英文名进了查询别名 | 低 |
| 人格升级测试 | `tests/test_persona_upgrade.py:12`（import `PERSONA, PRESET_ID, PREVIOUS_PERSONA, PREVIOUS_EXAMPLES`）与 `:54-100` | 测试（**已断**） | 见「未确认与读不到」第 1 项 | 低 |

数据引用 vs 代码硬编码的分界：全仓 `diana` 在 `src/` 只有 **6 处**（`cognition/diana.py` 自身 3 处、
`web/query_service.py:753-754`、`web/routes/settings.py:251`、`AgentSettingsView.vue:245`），
其余全部是配置与文档。`asoul`／`嘉然` 在 `src/` 的代码硬编码集中在三个 builtin 插件目录
（`asoul_calendar/`、`asoul_dynamics/`、`bilibili_live/` 的字体路径）与 `cards/` 品牌串。

---

## 二、素材耦合

| 素材 | 位置 | 运行必需？ | 依据 |
|---|---|---|---|
| 人格模板文字 | `src/len_bot/cognition/diana.py` | 否（可选按钮） | 对话读根配置，见 `context.py:1497-1501` |
| `font.ttf`（25.6MB，已跟踪） | `src/len_bot/plugins/builtin/asoul_calendar/resources/font.ttf` | 是（**被 3 个插件借用**） | `bilibili_live/plugin.py:194`、`asoul_dynamics/plugin.py:97`、`lenbot.config.example.json:171` |
| `cards/bilibili/logo.png`、两个 `template.html` | `src/len_bot/cards/**` | 是（被 builtin 插件 import） | `cards/bilibili/context.py:29`、`cards/schedule/__init__.py:15` |
| 运行运营素材（表情／媒体） | `/media/`（2801 项，未跟踪） | 是 | 根 `MediaService` 从 `db_path` 同级目录推导：`src/len_bot/media/service.py:105` |
| 日程头像目录 | `media/schedule-avatars/*`（未跟踪） | 仅日程卡片 | `lenbot.config.json:1225-1231`；`asoul_calendar/plugin.py:55-56` |
| 六份角色原文 | `docs/persona/diana/sources/*.md` | 否 | 全仓无任何代码读取 `docs/persona`；`diana.py:1` 仅 docstring 提及 |
| 表情包索引 | `docs/persona/diana/sticker-pack-20260906.json` | 否 | 同上 |
| A-SOUL 5 张常服原图与 19 张表情审阅 | `docs/persona/asoul/**` | 否 | `docs/persona/asoul/README.md:3-4` 明示"服务不会扫描本目录" |

结论：**运行必需素材只有 `font.ttf`、`cards/` 模板与 logo、`/media/` 运营素材**；
`docs/persona/` 两个目录整体是文档资料，删除不影响运行。

---

## 三、平台／业务耦合进入内核的位置

| 业务 | 进入内核的位置 | 形态 |
|---|---|---|
| 直播事件类型 | `src/len_bot/events/models.py:41-42`（`LIVE_STARTED`/`LIVE_ENDED`） | 核心 `EventType` 枚举固定 |
| 直播事件的注意力资格 | `src/len_bot/runtime/attention.py:23`、`src/len_bot/runtime/agent_runtime.py:59`、`src/len_bot/events/builder.py:31`、`src/len_bot/cognition/context.py:34` | 5 处核心集合列举 `LIVE_STARTED/LIVE_ENDED` |
| 直播历史与记忆 | `src/len_bot/memory/history.py:78`、`src/len_bot/memory/writes.py:14` | 核心写入路径固定 |
| 直播延期重核 | `src/len_bot/runtime/agent_runtime.py:682-684`、`:688-698` | **内核按 plugin_id 字符串分派**，并访问 `plugin_host._plugins` 私有字典、调用**不在公共 API 中**的 `refresh_deferred` |
| 兴趣分享 | `src/len_bot/runtime/gate.py:233`（提交规则）、`agent_runtime.py:483-485`、`:703-711`、`:777`、`events/store.py:1428`、`:1820` | 内核 6 处写死 `interest_share` |
| 兴趣分享候选类型 | `src/len_bot/runtime/agent_runtime.py:705` | 内核直接 import `plugins.builtin.interest_share.config.Candidate` |
| B 站账号能力名 | `src/len_bot/runtime/capabilities.py:45-47` | 核心 `Capability` 枚举含三个 `bilibili_*` |
| B 站动作类型审查 | `src/len_bot/cognition/action_review.py:31` | 核心 `SENSITIVE_TYPES` 含 `bilibili_like`/`bilibili_favorite` |
| B 站平台动作回执 | `src/len_bot/runtime/platform_actions.py:55`、`:91` | 内核事件 actor 写死 `plugin:bilibili_content` |
| 公共研究工具归属表 | `src/len_bot/runtime/public_research.py:8-16` | 内核维护工具名→plugin_id 映射 |
| 工作来源投影判定 | `src/len_bot/skills/store.py:134` | 核心技能账本按 plugin_id 白名单 |
| 能力卡展示分组 | `src/len_bot/web/capability_status.py:31-33`、`:35`、`:45`、`:47`、`:176`、`:233` | Web 展示层写死 asoul/bilibili/group_summary/gscore 插件 ID 与其事件类型 |
| 日程命令／开播公告审计 | `src/len_bot/web/query_service.py:1171`、`:1349`、`:1360`、`:1452-1453`、`:1471` | 查询服务写死 `'calendar_command','live_announcement'` 两种 trace kind |
| 日程插件依赖业务时区 | `src/len_bot/plugins/builtin/asoul_calendar/__init__.py:11`、`asoul_dynamics/__init__.py:11`、`group_summary/__init__.py:12`、`interest_share/__init__.py:11`、`bilibili_live/__init__.py:12-15` | **插件反向依赖订阅对象名单**（`bilibili_live` 要求 `root.members` 非空） |
| 卡片品牌 | `src/len_bot/cards/schedule/__init__.py:25`、`:96`；`cards/bilibili/context.py:30` | 静态串 |
| 日程内容源 | `lenbot.config.json:1177`（`https://asoul.love/calendar.ics`）、`:1248`（`AstrBot-DynamicASoul/1.0`） | 配置（可替换） |

调用链举例（静态）：
- 开播 → `bilibili_live/plugin.py:219-229` 轮询产生 `live_started` → `:135` `emit_event('live_started')`
  → 核心 `EventType.LIVE_STARTED`（`events/models.py:41`）→ `attention.py:23` → `builder.py:31`
  → 提交后 `agent_runtime.py:682` 按 plugin_id 走延期重核。
- 点赞／收藏 → `bilibili_content/actions.py:12-14` 用 `runtime.capabilities.Capability` +
  `runtime/platform_actions` → `platform_actions.py:55` 写死 `plugin:bilibili_content` actor。

---

## 四、内核依赖方向异常

| 依赖方 | 被依赖的具体业务 | 位置 | 为什么算方向异常 |
|---|---|---|---|
| `events/store.py`（EventStore） | 人格模板 `cognition/diana` | `events/store.py:988-1003`（函数内 import，`:990`） | 核心持久层带一个人格专用方法；任何发行版都背一个具名人格 |
| `runtime/agent_runtime.py` | `bilibili_live_sensor` | `agent_runtime.py:682-684`、`:688-698` | 内核按字符串识别业务插件，并读 `plugin_host._plugins`（`host.py:80`）私有属性、调用未进 `plugins/api.py` 的 `refresh_deferred` |
| `runtime/agent_runtime.py` | `interest_share` | `agent_runtime.py:483-485`、`:703-711`、`:777` | 内核启动／出站／事件三条路径写死一个业务插件 ID；`:705` 直接 import 其 `Candidate` 模型 |
| `runtime/gate.py` | `interest_share` 提交规则 | `gate.py:233-241` | 通用提交闸门内含单一业务的表达约束 |
| `events/store.py` | `interest_share` 任务语义 | `events/store.py:1428`、`:1820-1824` | 核心任务表按 kind 字符串特判业务恢复语义 |
| `runtime/capabilities.py` | B 站账号写入 | `capabilities.py:45-47` | 核心能力枚举把一家平台的账号动作写进通用权限集 |
| `cognition/action_review.py` | `bilibili_like`/`bilibili_favorite` | `action_review.py:31` | 通用敏感动作审查表含平台专名 |
| `runtime/platform_actions.py` | `bilibili_content` | `platform_actions.py:55`、`:91` | 通用"平台动作事实"表写死单一归属方 `plugin:bilibili_content` |
| `runtime/public_research.py` | bilibili/content/browser/workspace 工具表 | `public_research.py:7-16` | 内核维护具体工具名→插件映射；新增插件须改内核 |
| `skills/store.py` | `group_summary`、`interest_share` | `skills/store.py:134` | 通用技能来源判定写死两个业务插件 ID |
| `media/files.py`、`media/segment_service.py` | `plugins.builtin.workspace.config` | `media/files.py:144`、`media/segment_service.py:36` | 核心媒体层 import 具体 builtin 插件的配置模块 |
| `web/capability_status.py` | asoul/bilibili/group_summary/gscore | `capability_status.py:31-35`、`:45`、`:47`、`:176`、`:233` | 展示层硬编码插件 ID 与事件类型；`:16-48` 注释自称"display grouping, never another permission registry"，但关闭插件后卡片显示的是硬编码清单而非目录 |
| `web/query_service.py` | 日程／开播 trace kind | `query_service.py:1171`、`:1349`、`:1360`、`:1452-1453`、`:1471` | 查询层把两种业务 trace kind 写进通用关联逻辑 |
| `bilibili_live`、`asoul_dynamics`、`group_summary` | `asoul_calendar` 的 `resources/font.ttf` | `bilibili_live/plugin.py:194`、`asoul_dynamics/plugin.py:97`、`lenbot.config.example.json:171` | **插件之间**的文件级耦合：关掉日历插件会连带弄坏另两个业务 |
| `bilibili_live/__init__.py` | 根配置订阅对象 | `bilibili_live/__init__.py:12-15` | 插件校验要求 `root.time` 与 `root.members` 非空；通用发行版必须造出主播名单才能启用 |

方向正确的对照（**未**发现异常）：
`plugins/api.py:1-18` 只依赖基础类型，不依赖任何 builtin；
`plugins/host.py`、`base.py`、`catalog.py` 无业务 plugin_id 硬编码（`catalog.py:55` 按目录发现）；
`execution/`、`actions/`、`state/`、`scenes/`、`scheduler/`、`memory/`、`tools/` 全目录
grep 业务 plugin_id 为空；插件经 `plugins/api.py` 的公开符号与内核交互。

---

## 五、三层归属判定

| 模块／文件 | 归属 | 判定依据 |
|---|---|---|
| `events/`（models/store/builder） | 内核机制 | 事件与持久事实权威；`events/store.py:25` 单写者 |
| `scenes/`、`state/`、`runtime/gate.py`、`actions/` | 内核机制 | 身份、租约、提交与回执；无业务 ID（gate 除 `:233` 例外） |
| `execution/`、`browser/`、`media/{service,files,store,protocol}` | 内核机制 | 工具执行、事务、文件边界；无业务 ID |
| `runtime/capabilities.py` | 内核机制（含 3 条异常边） | 额度／授权权威；`capabilities.py:45-47` 越界 |
| `cognition/{social_core,context,projection,agent_loop,proposals,budget,gateway}` | 内核机制 | 模型调用、上下文装配、提案事务；字段名通用 |
| `plugins/{api,host,base,catalog,hooks,models,work,agent}` | 内核机制 | 插件公共合同；`api.py:1-18` 无业务依赖 |
| `cognition/diana.py` | 个人预设 | 具名人格原文 + 样例，`diana.py:6` `PRESET_ID="diana-v4"` |
| `runtime/attention*.py`、`sleep_policy.py`、`scene_policy.py` | 默认策略 | 观察／发言时机与睡眠取舍；可配置但不需要 DSL（路线书 `成熟开源项目路线书_20260921.md:139`） |
| `cognition/input_window.py`、`memory/{history,reflection}`、`runtime/work_context.py` | 默认策略 | 压缩与窗口取舍 |
| `runtime/{heartbeat,public_research,interest_publication}.py` | 默认策略（含业务边） | 「是否主动研究」属策略；但 `public_research.py:7-16` 的工具表混入业务 |
| `scheduler/` | 内核机制 | 持久认领单写者；无业务 ID |
| `plugins/builtin/asoul_calendar`、`asoul_dynamics`、`bilibili_live`、`bilibili_content` | 个人预设与业务插件 | 角色/关注对象/日历/直播/站点服务 |
| `plugins/builtin/{group_summary,gscore_adapter,interest_share,media_analysis,link_parser,browser_agent,workspace,python_workspace,web_search}` | 业务插件（通用性较强，但仍是插件） | 通用业务能力，装在插件层是正确方向 |
| `cards/{schedule,bilibili}` | 个人预设与业务插件资产 | 品牌串 + B 站专用模板 |
| `cards/{theme,tokens,layout,components,html_render}` | 内核机制 | 通用渲染原语 |
| `web/routes/*`、`web/query_service.py`、`web/group_quick.py` | 内核机制（投影），含业务硬编码 | 只读投影；`capability_status.py`、`query_service.py` 有业务 ID |
| `web/frontend/src/**` | 内核机制（界面），含一处模板按钮 | 仅 `AgentSettingsView.vue` 三处人格文案 |
| `docs/persona/**` | 个人预设（文档，非运行） | 无代码读取 |
| 根 `lenbot.config.json` / `lenbot.config.example.json` | 发行配置 | 前者是嘉然发行；后者已是通用骨架 |
| `tests/test_persona_upgrade.py` | 个人预设（测试） | 断言 `diana-v2:`/`diana-v4:` ID |
| `local_plugins/local_clock`、`plugins/workspace` | 业务插件（本地） | 目录发现，非内核 |

---

## 六、建议的最小拆分候选

只挑两处「确有耦合、改动最小、收益明确」的；不提议拆多仓库、微服务或策略 DSL。

### 候选 1（首选）：把 `interest_share` 的提交语义从 Gate／Runtime／Store 中移出

**证据**：`runtime/gate.py:233-241`、`runtime/agent_runtime.py:483-485`、`:703-711`、`:777`、
`events/store.py:1428`、`:1820-1824`、`skills/store.py:134`。

**方案**：把「单一业务插件的表达约束」改为插件注册时声明的通用属性
（例如在 `plugins/api.py` 的 `PluginSpec` 上增加一个"提交约束"字段，由 host 提供给 Gate），
内核只按属性处理，不再比较字符串 `'interest_share'`；`agent_runtime.py:705` 的
`Candidate` import 移回插件侧自解析。

**必须保留的边界**：`actions/queue.py` 发送前复核、`platform_actions` 的额度预占同事务、
`InterestStore` 版本与 `interest_publication.py` 的公开身份——都不动。这是**同一进程内的接口收窄**，
不是新服务。

**收益**：关闭兴趣分享后，Gate、Runtime、Store、Skills 四处不再残留业务分支；
新增同类"单条表达"业务无需改内核。

### 候选 2：把 `diana` 模板从 `EventStore` 移到可选资产包

**证据**：`events/store.py:988-1003`（`:990` 函数内 import）、`web/query_service.py:753-754`、
`web/routes/settings.py:251-253`。

**方案**：`preview_diana_persona` 的组装逻辑移入 `cognition/diana.py`（或同目录的模板模块），
`EventStore` 只保留通用的 `list_palette()`（已在 `events/store.py:991` 提供）；
路由改为直接调用模板模块 + `MediaService`。

**必须保留的边界**：`voice_exemplars` 的保存、启用、范围与人工编辑保留逻辑
（`events/store.py:1005-1089`）不动；模板读取仍然**永不修改已保存值**
（`events/store.py:989` 的现有承诺）；素材绑定仍走 `curated` 运营素材校验（`:1052-1055`）。

**收益**：`EventStore` 不再 import 任何具名人格；换人格或删除 `diana.py` 不影响核心存储。

---

## 七、非 A-SOUL 发行配置草案

`lenbot.config.example.json` 已是通用骨架（`identity_name: "Len"`、`character_context: ""`、
`members: []`、6 个插件 `enabled:false`、`asoul_calendar`/`asoul_dynamics` 均无 config），
因此草案主要是**替换运行配置**并按需补齐。

### 必须替换的字段

| 字段 | 当前（嘉然发行） | 非 A-SOUL 取值 |
|---|---|---|
| `runtime.identity_name` | `"嘉然"`（`lenbot.config.json:74`） | 任意助手名（示例已用 `"Len"`） |
| `runtime.identity_persona` / `identity_core` / `conversation_style` / `character_context` | 嘉然口吻与角色资料（`:80-82`） | 通用助手文本；`character_context` 可留空 |
| `runtime.address_names` | `["然比","嘉然","然然"]`（`:75-79`） | 与名字一致或留空 |
| `runtime.character_reference_assets` | 当前未配置（示例与运行值均为空） | 留空即可 |
| `runtime.heartbeat_topics` | `["A-SOUL 公开活动与视频","嘉然"]`（`:51`） | 换主题或保持 `heartbeat_enabled:false`（`:50` 已是 false） |
| `runtime.attention_keywords` | 当前为空 | 无需改 |
| `members` | 6 个 A-SOUL 条目含 `diana`/`asoul` 别名（`:1071-1120`） | 替换为本部署真实关注对象，或 `[]` |
| `plugins.bilibili_live_sensor.config.announcement_instructions` | 含"不把其他主播写成嘉然"（`:1139`） | 改写 |
| `plugins.asoul_calendar.config.source_url` | `https://asoul.love/calendar.ics`（`:1177`） | 换 ICS 源 |
| `plugins.asoul_dynamics.config.api_base_url` / `user_agent` | `https://len5010.top/dynamics/api`、`AstrBot-DynamicASoul/1.0`（`:1248`） | 换站或停用 |
| `plugins.asoul_calendar.config.avatar_directories` | 5 个成员绝对路径（`:1225-1231`） | 替换或清空 |
| `plugins.asoul_calendar.config.commands` | `今日直播`/`明日直播`/`本周直播` | 替换或清空 |
| `plugins.group_summary.config.render_font_path` | `../asoul_calendar/resources/font.ttf`（`:171` 示例同值） | **需指向独立字体**，否则停用日历后报告渲染断 |
| `plugins.bilibili_content.config.account_uid` 等 | 仅改若保留账号动作 | 可整体停用 |

### 需要关闭的插件（通用发行版）

`asoul_calendar`、`asoul_dynamics`、`bilibili_live_sensor`、`bilibili_content`、
`gscore_adapter`（`capability_status.py:47` 已标记为"可选 Core"）。
`group_summary`、`workspace`、`web_search_tool`、`link_parser`、`browser_agent`、
`media_analysis`、`interest_share` 是通用业务，可保留（示例中多数默认 `false`）。

### 必须由维护者决定

1. **是否保留 `cognition/diana.py`**：它是模板来源，删除会使 `/api/settings/persona/diana` 与
   前端"查看嘉然模板"失效——需维护者决定"通用发行版是否发布该按钮"。
2. **卡片品牌串归属**：`cards/schedule/__init__.py:25`、`:96` 与 `cards/bilibili/context.py:30`
   的"爱驼推送／A-SOUL LIVE"是硬编码，是否改为可配置、或通用发行版另出模板，需产品决定。
3. **`font.ttf` 的归属**：25.6MB 字体目前寄居在 `asoul_calendar` 下并被另两个插件引用；
   是抽成共享资源目录还是每插件自带，需维护者决定（涉及仓库体积与 S0-04 授权）。
4. **`Capability` 枚举是否保留 `bilibili_*`**：涉及已保存 grant 的兼容与迁移。
5. **`LIVE_STARTED`/`LIVE_ENDED` 是否降为插件事件**：改核心 `EventType` 会影响不可变历史
   （`events/models.py:35` 注释说明退役生产者仍保留类型），需维护者确认兼容策略。

---

## 八、未确认与读不到

1. **`tests/test_persona_upgrade.py` 当前无法通过**：`:12` import
   `PREVIOUS_PERSONA, PREVIOUS_EXAMPLES`，`:56` 使用 `PREVIOUS_EXAMPLES['diana-v2:1']`，
   但 `cognition/diana.py` 只导出 `PRESET_ID/PERSONA/MEDIA_REF_TAGS/EXAMPLES/EXAMPLE_IDS/build_examples`
   （无 `PREVIOUS_*`）；`:60` 调用的 `store.apply_diana_persona(...)` 在 `events/store.py` 中
   只有 `preview_diana_persona`（`:988`），无 `apply_`。该测试与
   `.pytest_cache/v/cache/nodeids:397` 引用的 `tests/test_v5_bounded_delivery_persona.py` 本轮未运行，
   故**只作为静态差异记录**，不判断实际失败原因与影响范围。
2. **`.runtime/`、`.backups/`、`.pytest_cache/`**：`.gitignore:64-65,37` 未跟踪，含
   `frontend-validation/before-src` 快照与贴纸 manifest。快照不是当前代码，未作为证据。
3. **`/media/` 与 `/file_assets/` 内容未读取**：只统计了条目数（2801），未打开任何媒体文件；
   `media/schedule-avatars/` 存在但内容未核对。
4. **`len_bot.db` 未读取**：因此"当前实际启用了哪些插件、哪些群订阅了直播"未确认；
   本报告的启用状态结论**全部**来自 `lenbot.config.json` 的保存值与代码默认。
5. **`capability_status.py:16-48` 的 `CARDS` 与真实插件目录的一致性未验证**：该清单写死
   12 组插件 ID，`catalog.discover()`（`catalog.py:52-83`）按目录发现，两者是否总有交集未确认。
6. **前端产物** `web/static/dist/**` 是构建产物（`.gitignore:32` 未跟踪），
   本报告只把它作为"构建产物含该文案"的记录，不代表源码之外的额外耦合。
7. **`local_plugins/local_clock`、`plugins/workspace` 的内容未逐一核对**：仅确认它们经
   `plugin_directories`（`lenbot.config.json:1126-1128`、示例 `:232-234`）被发现。
8. **未确认 `refresh_deferred` 是否存在于其他插件**：全仓只有 `bilibili_live/plugin.py:118`
   定义、`agent_runtime.py:693` 调用，但该协议未写入 `plugins/api.py`（`api.py:1-18` 无此符号），
   是否存在文档化的第三方插件扩展点未确认。
9. **各业务插件关闭后的运行行为均为静态推断**：未启动服务、未做任何关闭实验，
   "会坏掉"是按 import 图与配置校验（如 `bilibili_live/__init__.py:12-15` 要求 `root.members`）
   推得，**不是运行验证**。

---

## 九、反向引用清单

供维护者不复核 grep 即可定位：

**`diana`（`src/` 全部 6 处）**
- `src/len_bot/cognition/diana.py:1`、`:6`、`:9`
- `src/len_bot/web/query_service.py:753`、`:754`
- `src/len_bot/web/routes/settings.py:251`
- `src/len_bot/web/frontend/src/views/AgentSettingsView.vue:245`

**`asoul` / `A-SOUL` / `嘉然`（`src/` 非插件目录）**
- `src/len_bot/plugins/builtin/bilibili_live/plugin.py:194`
- `src/len_bot/cards/schedule/__init__.py:96`
- `src/len_bot/cards/schedule/__init__.py:25`（"爱驼推送"）
- `src/len_bot/cards/bilibili/context.py:30`（"爱驼推送"）
- `src/len_bot/web/capability_status.py:31`、`:32`、`:233`

**`asoul` / `A-SOUL`（插件目录内，业务自身）**
- `src/len_bot/plugins/builtin/asoul_calendar/__init__.py:15`
- `src/len_bot/plugins/builtin/asoul_calendar/plugin.py:112`、`:118`
- `src/len_bot/plugins/builtin/asoul_calendar/render.py:74`、`:84`、`:162`、`:172`
- `src/len_bot/plugins/builtin/asoul_dynamics/__init__.py:18`
- `src/len_bot/plugins/builtin/asoul_dynamics/plugin.py:97`、`:117`、`:160`、`:170`、`:177`、`:185`
- `src/len_bot/plugins/builtin/asoul_dynamics/render.py:20`

**内核反向依赖业务（按文件）**
- `src/len_bot/runtime/agent_runtime.py:59`、`:483`、`:484`、`:485`、`:682`、`:683`、`:689`、`:693`、`:703`、`:705`、`:777`
- `src/len_bot/runtime/gate.py:233`
- `src/len_bot/runtime/capabilities.py:45`、`:46`、`:47`
- `src/len_bot/runtime/platform_actions.py:55`、`:91`
- `src/len_bot/runtime/public_research.py:7`、`:8`、`:9`、`:10`、`:11`
- `src/len_bot/runtime/attention.py:23`
- `src/len_bot/events/models.py:41`、`:42`
- `src/len_bot/events/builder.py:31`
- `src/len_bot/events/store.py:988`、`:990`、`:1428`、`:1820`
- `src/len_bot/cognition/action_review.py:31`
- `src/len_bot/cognition/context.py:34`
- `src/len_bot/memory/history.py:78`
- `src/len_bot/memory/writes.py:14`
- `src/len_bot/skills/store.py:134`
- `src/len_bot/media/files.py:144`
- `src/len_bot/media/segment_service.py:36`
- `src/len_bot/web/capability_status.py:16`、`:31`、`:32`、`:33`、`:35`、`:45`、`:47`、`:176`、`:233`
- `src/len_bot/web/query_service.py:1171`、`:1349`、`:1360`、`:1452`、`:1453`、`:1471`

**跨插件资产耦合**
- `src/len_bot/plugins/builtin/asoul_calendar/resources/font.ttf`
- `src/len_bot/plugins/builtin/bilibili_live/plugin.py:194`
- `src/len_bot/plugins/builtin/asoul_dynamics/plugin.py:97`
- `lenbot.config.example.json:171`；`lenbot.config.json` 内同字段

**配置发行点**
- `lenbot.config.json:50`、`:51`、`:74`、`:75-79`、`:80`、`:82`、`:1071`、`:1071-1120`、`:1139`、`:1177`、`:1225-1231`、`:1248`
- `lenbot.config.example.json:49`、`:53`、`:54`、`:55`、`:56`、`:123`、`:155`、`:159`、`:171`

**文档（非运行）**
- `docs/persona/diana/README.md:1`、`:18`、`:22`
- `docs/persona/asoul/README.md:3`、`:4`
- `docs/operations.md:212`
- `README.md:56`
- `docs/LenBot_成熟开源项目路线书_20260921.md:137`、`:139`、`:141`、`:417`
- `docs/LenBot_分阶段任务卡_20260921.md:120`
- `docs/LenBot_群聊体验与可靠执行_完整改造计划_20260920.md:76`、`:348`、`:356`、`:445`、`:469`、`:485`、`:493`、`:523`

**已断引用**
- `tests/test_persona_upgrade.py:12`、`:54`、`:56`（`PREVIOUS_PERSONA`/`PREVIOUS_EXAMPLES`）
- `tests/test_persona_upgrade.py:60`、`:66`、`:68`、`:69`、`:81`、`:94`、`:97`、`:100`（`apply_diana_persona`）
