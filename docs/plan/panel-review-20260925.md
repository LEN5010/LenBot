# 管理面板审阅清单草稿（2026-09-25）

读者：维护者，决定面板下一步要不要改、先改哪项。

这份清单只记录问题和建议，本批没有按它改任何代码。清单里每一项都会改变用户看到的页面（文字、布局、交互或颜色），所以按约定只列出、不动手，等维护者逐项确认。

行号以 commit 9d7a6c8 为准，路径相对 `src/len_bot/web/frontend/src/`。所有结论都来自读代码，没有打开页面、截图或实机测试，所以移动端和对比度问题只是从样式推断，需要现场确认。

每项的写法：位置、现状、建议改法、理由。优先级分三档：高＝影响判断或操作安全，中＝影响阅读效率，低＝细节。

## 1. 运行概览重复显示 OneBot 和 Shadow 状态

同一个运行状态在同一屏里出现三次，而且数据来源和措辞都不一样。用户很难判断三处说的是不是同一件事。

| # | 位置 | 现状 | 数据来源 |
|---|---|---|---|
| 1a | `views/OverviewView.vue:124-126`（数据），`:162-166`（就绪列表渲染） | 就绪列表里的 OneBot 连接行，文案为：连接已建立；每条消息是否送达仍看真实回执 | `stats.websocket_connected` |
| 1b | `views/OverviewView.vue:245-251` | ONEBOT 连接卡片，显示圆点加已连接／未连接，下方附一段说明 | `data.stats.websocket_connected` |
| 1c | `layouts/AppShell.vue:139` | 顶栏状态菜单里的 OneBot：已连接／未连接 | `app.status.onebot.connected` |
| 1d | `views/OverviewView.vue:263-266` | 发送方式卡片，显示 Shadow 观察／按各群规则发送 | `data.stats.shadow_mode` |
| 1e | `layouts/AppShell.vue:140` | 顶栏状态菜单里的发送方式：Shadow · 仅记录候选／按各群规则实际发送 | `app.status.shadow_mode` |
| 1f | `layouts/AppShell.vue:152-160` | 顶栏右侧的模式标签，显示 Shadow／按群规则发送；屏幕宽度 600px 以下隐藏（`:208-209`） | `app.status.shadow_mode` |

**建议改法（优先级：中）**

- 概览页只保留一处连接与发送方式：两张连接卡片（1b、1d）。
- 就绪列表删掉 OneBot 行（1a）。如果就绪列表需要完整，改成一行链接，指向连接卡片。
- 顶栏状态菜单（1c、1e）和模式标签（1f）是全局入口，其他页面也要用，所以保留。可以考虑在概览页隐藏模式标签。
- 三处措辞统一。例如 Shadow 统一叫 Shadow 观察，另一种统一叫按各群规则发送。

**理由**

- 1b／1d 用概览接口的 `stats`，1c／1e／1f 用全局状态接口的 `app.status`。两个接口的采样时间不同，所以同一屏可能出现一处已连接、另一处未连接。
- 用户不知道该信哪一处。
- 减少重复后，就绪列表只剩真正需要动手处理的项目。

**要先确认的事**

- 就绪列表的 OneBot 行是不是有意保留，用来做一个全部就绪的总判断？
- 如果是，建议只在未连接时显示这一行。

## 2. 精简说明性小字，把长免责说明收进 HelpHint

**口径**

- 模板里带 `muted`／`muted-copy` 类的 `<p>` 共 233 个；
- 所有带这两个类的元素共 280 处；
- 其中直接写死、且不少于 30 个字符的静态说明约 92 处；
- 不少于 60 个字符的静态说明 21 处。

原计划里的约 158 条可能用的是另一种统计口径。本清单按上面这几种口径计数，数字能用 `grep -rnE '<p[^>]*class="[^"]*muted' src` 复现。

HelpHint（`components/HelpHint.vue`）是一个问号按钮，点开显示说明，目前只用在 4 处：

- `SceneSettingsForm.vue:516`
- `PluginsView.vue:630`
- `settings/RuntimeSettings.vue:15`
- `settings/AccountSettings.vue:54`

**建议通用做法（优先级：中）**

- 页面上只留一句用户做决定时必须知道的话，不超过 30 个字左右。
- 边界声明、实现细节、不保证什么之类的内容，移进旁边标题上的 HelpHint。
- 同一个边界在多个页面重复的，只在最相关的一处保留。

**理由**

- 这些长段落大多在讲系统不保证什么。
- 它们放在每个表单上方，把真正的操作字段挤到了下面。
- 小字颜色本身对比度也偏低（见第 4 节），长段落读起来更累。

下面按字数从长到短列出最长的 15 处，按上面的做法逐项处理：

| # | 位置 | 现状（节选） | 建议留在页面上的一句 |
|---|---|---|---|
| 2a | `components/jobs/JobBudgetTab.vue:36` | 修订、暂停和恢复不重置累计账……不设限仅指已明确记录为 null 的次数或 token 维度，不是免费或无限执行。（约 120 字） | 累计用量不会因修订、暂停或恢复而清零。 |
| 2b | `components/settings/MemberSettings.vue:17` | 这里登记的是 B 站主播与订阅对象，不是群详情里的 QQ 参与者…… | 这里登记 B 站主播与订阅对象。 |
| 2c | `components/settings/AccessSettings.vue:12` | 白名单成员仍可正常提问……日程命令及引用评论仍保持安静。 | 关闭普通聊天的群里，白名单成员仍可提问。 |
| 2d | `components/settings/AccessSettings.vue:36` | 只影响本计划新增的自主能力……已发出的字节无法撤回。 | 只影响自主能力，普通聊天不需要授予。 |
| 2e | `views/PluginsView.vue:747` | 这里展示注册声明，不是当前调用授权……展示页长不等于原始资料总长。 | 这里是注册声明，实际执行时会重新核对权限。 |
| 2f | `views/PluginsView.vue:662` | 此处只展示已经取得的状态……不因为没有最近成功获取源而算故障。 | 只显示已取得的状态。 |
| 2g | `components/settings/RuntimeSettings.vue:49` | 两种实现均调用 upload_group_file……成功仍看真实 FILE_UPLOADED 与 file_id。 | 已在 `:15` 的 HelpHint 里，建议合并过去，这里不留。 |
| 2h | `components/settings/RuntimeSettings.vue:85` | 新对话与新建工作采用当前发布预算…… | 修改只影响之后新建的对话和工作。 |
| 2i | `views/ModelsView.vue:502` | 预占中／已结算是账户当日已占用的事实……不给单一余量。 | 已占用和准入余量是两个数，不能相减。 |
| 2j | `components/jobs/JobProgressTab.vue:196` | 这是宿主 worker 当前目录，不是按执行保存的不可变快照…… | 这是当前目录，后续运行可能改变文件。 |
| 2k | `components/agent/AttentionSettings.vue:9` | 普通原话有截止时间，按容量分批读取…… | 移入 HelpHint，页面不留。 |
| 2l | `views/JobsView.vue:502` | 离开或切换工作只停止本页跟踪，不撤销已提交控制…… | 离开本页不会撤销已提交的操作。 |
| 2m | `components/settings/ConnectionSettings.vue:91` | 连接配置保存后需手动重启服务生效…… | 保存后需手动重启服务才会生效。 |
| 2n | `components/models/RoutingDialog.vue:59` | 切换供应商或模型会清除旧绑定的推理强度和视觉确认…… | 换模型会清除推理强度和视觉确认。 |
| 2o | `components/agent/TimeSettings.vue:10` | 日程与群总结按照这里填写的时区……留空表示不启用睡眠。 | 日程和群总结按这里的时区解释日期。 |

其余不少于 60 字的 6 处，按同样的做法处理：

- `components/jobs/JobBudgetTab.vue:46`
- `components/jobs/JobProgressTab.vue:154`
- `components/CharacterReferencesPanel.vue:159-160`
- `components/FileAssetsPanel.vue:32`
- `components/ObservationCoverage.vue:23`
- `views/PluginsView.vue` 其余长段

**要先确认的事**：哪些边界说明必须一直露在页面上，不能收进 HelpHint？

- 可能的候选：2d（撤销不能撤回已发出内容）和 2l（离开页面不等于取消）。这两条和操作安全有关。
- 如果保留，建议改成 `v-alert` 提示条，不要和普通说明混在一起。

## 3. 统一确认交互：37 处 window.confirm 与手写确认对话框

**现状**

浏览器原生确认框 `window.confirm` 共 37 处：

| 文件 | 行号 |
|---|---|
| `components/CharacterReferencesPanel.vue` | 79、89、97 |
| `components/MemoryIndexPanel.vue` | 33 |
| `components/SceneSettingsForm.vue` | 349 |
| `components/agent/usePersonaSettings.js` | 97、132 |
| `components/models/useProviderSettings.js` | 70、108、117、139 |
| `components/models/useRoutingSettings.js` | 50、68 |
| `components/settings/useDeliverySettings.js` | 10 |
| `composables/useUnsavedChanges.js` | 5（离开未保存页面的确认，所有页面共用） |
| `layouts/AppShell.vue` | 64（退出登录） |
| `views/AgentSettingsView.vue` | 82、145、159 |
| `views/JobsView.vue` | 266、273、348 |
| `views/MediaView.vue` | 255、264、296、345、409 |
| `views/MemoryView.vue` | 211 |
| `views/ModelsView.vue` | 119、157、210、268 |
| `views/PluginsView.vue` | 238、368 |
| `views/SettingsView.vue` | 171、185 |
| `views/TasksLoopsView.vue` | 184 |

另外有 6 个手写的确认用 `v-dialog`，每个的按钮顺序、颜色和能否点外面关闭都各不相同：

- `views/SkillsView.vue:461`（发布技能）
- `views/TasksLoopsView.vue:665`
- `views/JobsView.vue:799`
- `components/settings/ResetDataDialog.vue:7`
- `components/scenes/RetryHistoryDialog.vue:6`
- `components/models/TestConfirmDialog.vue:7`

其余 13 个 `v-dialog` 用于查看详情或编辑，不在本项范围内。

**建议改法（优先级：高）**

1. 新增一个确认对话框组件，例如 `components/ConfirmDialog.vue`，再加一个组合函数 `useConfirm()`，返回 `confirm({title, text, confirmLabel, danger}) → Promise<boolean>`。
   - 这样调用处只要把 `window.confirm(...)` 换成 `await confirm(...)`，控制流基本不变。
2. 组件统一以下规则：
   - 危险操作的确认按钮用 error 色，放在右侧；
   - 取消按钮获得初始焦点，按 Esc 等于取消；
   - 执行中禁止关闭。
3. 分三步迁移：
   - 先迁移不需要同步返回的调用；
   - 再迁移上面 6 个手写确认对话框；
   - 最后处理 `useUnsavedChanges.js:5`。
4. `useUnsavedChanges.js:5` 需要单独设计。
   - 它在路由守卫里同步调用，改成异步要配合 vue-router 的 Promise 守卫；
   - 页面关闭时的 `beforeunload` 仍只能用浏览器原生提示。

**理由**

- 原生确认框不能套页面样式，也不能区分危险等级。长文字在手机上会被折叠或截断。
- 自动化环境和部分浏览器设置可能直接屏蔽原生对话框。
- 37 处和 6 个对话框各写各的，同样是危险操作，按钮位置却不一样，容易点错。

**要先确认的事**：是否接受改成异步确认后，确认前的那一小段时间里页面仍可交互？

- 原生确认框会阻塞整个页面。
- 改成异步后需要在调用处加忙碌锁。目前大多数调用处已有 `busy` 判断，但需要逐处核对。

## 4. 移动端与无障碍

以下都是读代码得出的推断，需要在真机或浏览器缩放下确认。对比度按 WCAG AA 计算：正文要求至少 4.5:1，图形元素至少 3:1。

| # | 位置 | 现状 | 建议改法 | 理由 | 优先级 |
|---|---|---|---|---|---|
| 4a | `styles/tokens.css:3` `--muted:#63748b` | 在浅灰底 #f4f6f9 上对比度约 4.41:1，在 #f8fafc 上约 4.56:1 | 加深到约 #5b6b80（在 #f4f6f9 上约 5.0:1） | 灰底上的小字低于 4.5:1，而全站几百处说明文字都用这个颜色 | 高 |
| 4b | `views/OverviewView.vue:433` `.readiness-mark.ok` | 11px 粗体，成功绿字配浅绿底，对比度约 4.08:1 | 字号改为 12px，或加深文字颜色 | 就绪状态是概览页的核心判断，11px 加低对比度难以辨认 | 中 |
| 4c | `views/OverviewView.vue:423` `.scene-initial` | 群头像字 #647c9b 配 #edf2fa，对比度约 3.81:1 | 文字加深 | 12px 字低于 4.5:1 | 低 |
| 4d | `styles/tokens.css` `--status-idle:#adb8c7`、`--status-warning:#bd8340` | 状态圆点在白底上分别约 2.01:1 和 3.24:1 | 空闲色加深到约 #8494a7（白底约 3.1:1） | 空闲圆点（`OverviewView.vue:399` `connection-dot`）低于图形元素的 3:1。两处圆点（另一处是 `AppShell.vue:202`）旁边都有状态文字，不会只靠颜色传达状态，所以影响有限 | 低 |
| 4e | 11px 字号共 18 处，例如：`components/MessageItem.vue:158`、`:168`，`components/scenes/SceneMessagesTab.vue:66`，`layouts/AppShell.vue:193`，`views/ScenesView.vue:998-999`，`views/OverviewView.vue:397`、`:426`、`:429`、`:433`，`views/ActivityView.vue:940`、`:943`、`:951`、`:953`、`:954`、`:962` 等 | 时间、编号、计数等辅助信息用 11px | 最小字号统一为 12px，写成 tokens.css 变量 | 11px 加灰色，在高分屏手机上很难读 | 中 |
| 4f | `layouts/AppShell.vue:193` `.nav-section` | 这个类在模板里已经没有元素使用 | 删除这条样式 | 死代码。它不影响显示，但属于样式清理，放在这里提醒 | 低 |
| 4g | `components/HelpHint.vue` 按钮 `size="x-small"` | 可点击区域明显小于 40px（x-small 加 comfortable 密度） | 保持图标大小，把点击区域扩大到 40px 以上，例如加 padding 或伪元素 | 手机上容易点不到。如果按第 2 节多用 HelpHint，这项会更明显 | 中 |
| 4h | `views/ModelsView.vue:652` `.reservation-table`，`components/settings/RuntimeSettings.vue:140` `.budget-table` | 单元格 `white-space:nowrap`，窄屏只能横向滚动 | 窄屏改为卡片式列表，或允许关键列换行，并给表格外层加可见的滚动提示 | 手机上看不到右侧列，用户不知道还能往右滑 | 中 |
| 4i | `views/ScenesView.vue:998` `.scene-id` | 11px、单行、超出显示省略号 | 允许换行，或提供复制按钮 | 群号是用户要核对的身份，被截断后无法确认 | 低 |
| 4j | `views/ActivityView.vue:353`（4 个标签），`views/PluginsView.vue:557`（详情里 3 个标签） | `v-tabs` 没有 `show-arrows` | 加 `show-arrows`，与 Settings、Jobs、Scenes、AgentSettings、TasksLoops 一致 | 窄屏上标签可能超出宽度，而且没有箭头提示。这两处在窄屏上是否真的溢出需要现场确认 | 低 |
| 4k | `layouts/AppShell.vue:208-209` | 600px 以下隐藏模式标签 | 手机上保留一个紧凑的 Shadow 标记，或在状态按钮文字里带上 | 手机用户在顶栏看不到当前是否实际发送，只能点开菜单才知道。这是判断风险的关键信息 | 中 |
| 4l | 第 3 节列出的原生确认框 | 长确认文字在手机上显示不全 | 见第 3 节 | 同第 3 节 | 高（随第 3 节处理） |

**已检查、未发现问题的部分**

- 纯图标按钮都有 `aria-label`；
- 没有发现把点击事件绑在 `div`、`span` 这类不可交互元素上的情况；
- 主导航有 `aria-label`，当前区域导航有 `aria-current`。

## 5. 建议的处理顺序

1. 第 3 节确认对话框。它涉及操作安全，而且改完后第 2 节的长说明可以放进对话框正文。
2. 第 4 节里 4a、4k 这两项颜色和可见性问题，改动小、影响面广。
3. 第 1 节概览页去重。
4. 第 2 节小字精简，需要维护者逐条确认措辞，工作量最大。
5. 其余低优先级项。

每一步都会改变页面外观，需要在同版构建上逐页人工核对后再合并。
