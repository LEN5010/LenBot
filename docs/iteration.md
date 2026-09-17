# 当前任务

## 2026-09-17：按用户授权重启并核对心跳

用户本轮明确授权「先重启一下（别备份了）」；本次跳过备份，未改根配置、模型、权限和群名单。以下运行时刻注明时区。

- 旧主进程 8486 于 23:37:37（Asia/Tokyo）收到 SIGTERM，日志记录 `Len Bot stopped cleanly.`，随后进程退出、11307 无监听。首次 nohup shell 未留下进程、日志为空；核实无重复实例后，使用独立会话 `uv run --no-sync len-bot` 启动，launcher 10553 / 主进程 10554，启动时间 23:38:58（Asia/Tokyo）。日志位于仓库外 `/tmp/lenbot-runtime/len-bot-20260917.log`；未移动旧日志。
- 新实例监听 `127.0.0.1:11307`，23:39:00（Asia/Tokyo）记录 `OneBot WebSocket connected: ('127.0.0.1', 13001)`；interest_share、web_search_tool、workspace、browser_agent 等已加载，Scheduler 启动时读到 3 个待执行槽。
- 心跳配置已开，jobs_enabled=true；主题为「A-SOUL 公开活动与视频」「嘉然」，public_research 系统授予有效，job_max_concurrent=2，工作预算 24 次模型／48 次工具／600 秒。每半小时一轮，业务时区 Asia/Shanghai，睡眠 00:00—07:00。
- 已沿 `_start_workers → Heartbeat.ensure_next → Scheduler TASK_DUE → run_slot → operator_outcome → Gate/JobStore → JobRunner → ActionReviewer → 公共只读工具 → finish_work → InterestStore → interest_share → Gate/发送回执` 阅读核对。原有授权、来源检查与睡眠限制保留。未手动触发系统槽、未运行测试或额外模型探针，未主动实发。
- 下一次持久槽为北京时间 2026-09-17 23:00（日本时间 2026-09-18 00:00）：心跳 1 个、群 1014123451 与 126300994 分享各 1 个，均 pending。这证明已排队，不证明届时成功。
- 历史心跳工作共 completed=2、failed=6。最近一轮 `job_cc90d717c6ac52048d03`（北京时间 22:30）实际创建成功，但审查记录 `act_c6e2d12d946c4c009f0d56779d5f52e3:1` 为 uncertain/review_failed；原错误：`ValidationError: 1 validation error for ReviewDecision / Invalid JSON: expected value at line 1 column 1`，输入以 Markdown 的 ```json 围栏开始。面板工作摘要被通用 PermissionError 分支写成「原群或请求者的当前配置不允许继续此工作」，真实原因是模型输出格式，不是现有 grant 缺失。此前多次失败也有相同围栏解析错误。
- 北京时间 22:00 的 `job_72329957961f5b08a914` 审查 allow，实际进入模型及 web_search，两次搜索均返回 no_results，最终零成果结束；不能把 completed 当作已取得可分享资料。当前 public_interests 为 0 条，分享轨迹为 no_candidate，没有兴趣发送尝试记录。
- 两群 interest_share 已全局和本群启用且分别有 grant，每日最多 2 次、冷却 3600 秒，主题匹配；后半段实际采用候选／社会判断／真实交付没有运行证据。
- 新实例正常对话于 23:39:44、23:39:50（Asia/Tokyo）仍记录 `APIConnectionError: Connection error.`，当前调用账型号为 `cline-pass/deepseek-v4.1-flash`。这是重启后自然产生的失败记录；本轮未替换模型，不能据历史旧模型心跳完成记录推断当前模型可用。
- 结论：实例、OneBot、调度与建工作已有运行证据；完整「研究→候选→分享」尚未跑通。阻塞优先级为当前模型连接、审查 JSON 协议、有效公共资料获取；本次仅核对和记录，未改心跳业务实现。


## 2026-09-17：删除启用向导、修复总览、精简日历

本轮基线 `5ea2280`，开始时工作区干净。用户说明实例运行中；只读进程记录确认 PID 8486，23:24:08 启动，命令为本项目 `.venv/bin/len-bot`。HEAD 日历样式提交时间 23:24:41，进程启动早于该提交，不能据此宣称运行实例已使用当前模板。

- 删除启用向导页面、导航与能力卡入口、`/api/setup/options|preview|apply`、运行时向导保存方法及专用模块；保留群快速配置、权限判定与已有配置值。
- 总览原 `readiness` 引用了未定义的 `plugins`，会在渲染时抛错；现从能力清单按插件 ID 去重取得，首屏同时加载能力清单与运行状态。读取中和读取失败不标为已就绪。
- 今日/明日/本周共用已有图 2 对应的粉色 HTML 详细模板；删除底部范围、抓取信息、来源 URL 和重复免责声明（原工具观察仍保留）。跨日结束时间补日期；Pillow 原有失败回退也删除冗余页脚。没有新建渲染或降级路径。
- 前端 `npm run build` 通过，本机 dist 已生成且不进入 Git。本轮修改的 Python 模块 `compileall` 与 `git diff --check` 通过；未新增或运行测试、断言探针、自动截图或真实发送。
- 实际页面检查未完成：Codex 内置浏览器和 Edge 打开 `http://127.0.0.1:11307/overview` 均报 `net::ERR_BLOCKED_BY_CLIENT`；Chrome 不可用。未绕过浏览器限制，未把构建结果写成运行验收。
- 未停机、重启、修改根配置、提交或推送。后端接口删除及日历运行效果待按运行手册备份并获准重启后核对。

以下为此前批次历史，不能替代本轮运行状态。


更新时间：2026-09-17 22:40。分支 `master`，基线提交 `f059191`。

## 本轮（聊天参与 + 富卡片）

**Verdict：代码已改，但 LenBot 仍在旧代码上运行（PID 88556，21:49 起），本轮改动一次都没有在 Bot 里跑过。富卡片已在本机独立渲染验证通过；容器字体修复未验证（镜像未重建）。**

### 已做

| 项 | 改动 | 已核对 |
|---|---|---|
| 聊天参与 | `cognition/context.py:1071` 重写 certain=false 段：删去「沉默是正常结果，不是遗漏」，改为「话题相关或确有具体信息/看法/玩笑可加时就接一句，不必等人点名」。守卫条款（不据此建立工作/提醒/长期认识、不硬找新话题）保留 | 仅静态改动，未重启，无运行观察 |
| 渲染地基 | 新增 `cards/html_render.py`：进程内 Playwright，`set_content` 不导航，http/https 一律 abort | 本机渲染 1200×800 RGBA PNG，中文正常，故意放入的 `https://example.invalid` 图片被拦成裂图 |
| 卡片移植 | 新增 `cards/bilibili/`：`template.html` + `logo.png` 原样复制；`models.py`（卡片数据契约）、`context.py`（移植 `build_card_context`）、`source.py`（资料取数 + 图片内联） | 真实渲染出含封面、头像挂件、UP 资料、二维码的完整卡片 |
| 直播卡片接线 | `bilibili_live`：`RoomInfo` 补 `user_cover`/`keyframe`，`LiveSample` 补 `cover_url`；`_render_card` 出富卡片，失败回落到原 PIL 卡片 | 对真实房间 22637261 采样，`cover_url` 实际取到；插件 import 与 compileall 通过 |

实测取数（2026-09-17 22:3x，UID 672328094）：`x/web-interface/card` 返回 获赞 33438533、关注 32、粉丝 1869578、头像与挂件 URL 各一；卡片footer 显示为 3343.9万 / 32 / 187万。

### 两处偏离计划，理由记此

1. **渲染不走 Gateway 浏览器容器，改为进程内。** `browser/gateway_service.py:52-55` 的 `_job()` 强制 `call.tool_call_id` 并走 `require_execution_job`，`_session()` 还要构造带 `job_id`/`job_revision`/预算期限的 `ExecutionRequest`。推送卡片由后台轮询产生，没有工作也没有 tool_call_id，接上去只能伪造一个工作。改用进程内 Playwright，与 `browser_agent/plugin_core.py:25` 已有的 `None if self.gateway else BrowserWorkerV2(...)` 回落同一模式。两条路径的分工写在 `cards/html_render.py` 的模块 docstring。
2. **不需要 `bilibili-api-python`。** 计划原定移植 `asoul_bilibili.py` 2321 行以取得 UP 资料（参考实现用三个 wbi 签名/登录接口）。核对接口文档后确认 `x/web-interface/card?mid=` **免签名、免 Cookie**，一次 GET 就返回 name/face/pendant/fans/attention/like_num，覆盖卡片footer 全部字段。因此未引入该依赖，也不再有 SESSDATA 硬性要求。

### 依赖

`pyproject.toml` 增加 `jinja2>=3.1.4`、`qrcode>=8.2`；`uv lock` 只新增 jinja2/markupsafe/qrcode，无任何升级或移除。安装用 `uv pip install jinja2 qrcode`（不用 `uv sync`，因为 `playwright` 在 `browser` 可选组里，不带 `--extra browser` 的 sync 会把它卸掉，而 Bot 正在运行）。

### 续做（同一轮，用户临时授权修改）

**动态卡片**：`asoul_dynamics` 也接入同一套模板，设计风格随之统一。新增 `asoul_dynamics/card.py` 把源记录映射到卡片契约。源站 `/search` 本就返回 `images`、`likeCount`/`commentCount`/`forwardCount`、`member.avatarUrl`/`bilibiliUid` 与 `orig`（转发），模型未声明而已。实测渲染：点赞 4869 / 评论 446 / 分享 7 与真实配图齐全。新增共用 `ProfileCache`（6 小时 TTL，刷新失败保留上一份而不是覆盖成空白）。

**模板样式修正**：`count-1` 原为 `width:100%` + `max-height:680px` + `contain`，方形图被高度卡住后在 1064px 宽的盒子里留出灰边。改为 `width:auto;max-width:100%;margin:0 auto`，单图与转发单图都贴合自身尺寸。

**Part 2.4 聊天参数收敛（已完成）**：全局 `attention_*` 由 0.6/300/60 改为 **0.8/150/30**（即两个群原本就在跑的值），并删除 `group:1014123451` 与 `group:126300994` 的 `scenes.*.attention` 覆盖，两群回到继承。`RuntimeConfig` 与 `SceneSettings` 逐个校验通过，`attention=None`。改前 Bot 已停机，配置备份在 `.backups/config-20260917-224325/`。今后新群自动继承同一套聊天参数；差异化靠 `chat` 与工作插件开关，不靠调参。

**Part 4 前端（部分完成）**：新增两个共用原语——`AdvancedSection.vue`（基础/高级分层，刻意不用 `v-expansion-panel`，避免与全站 16 个只读证据查看器混淆）与 `HelpHint.vue`（长说明移入「?」浮层）。已应用：
- `SettingsView` 运行参数页：`onebot_file_upload` 从「100 字说明 + 手打 JSON」换成真控件（实现下拉、现场版本、协议只读派生、核对开关），协议随实现自动派生以匹配 `adapters/file_upload.py:23` 的校验器；改实现或改版本会自动把 `deployment_verified` 复位为 false（核对结果不跟着新目标走）。原始 JSON 降级进 `AdvancedSection`。
- `SettingsView` 危险区：面板与确认对话框原本重复同样三段，面板改为一句摘要 + HelpHint，完整口径保留在对话框（真正要确认的时刻）。
- `SceneSettingsForm` 旁听盒：默认只显示「继承全局 / 本群已覆盖」徽标与一行有效值，覆盖入口与「旁听 +2 档」移入 `AdvancedSection` 并标注会偏离统一参数。
- `PluginsView`（原说明/标签比 106×）：90 字段落收进 HelpHint。

**R5 群与权限页（新增）**：新增 `/groups` 与 `/groups/:sceneId` 路由和 `GroupsView.vue`。列表按群展示两个权限徽标（聊天取 `settings.chat`，工作取本群是否启用 `workspace`/`python_workspace`），支持搜索与按权限筛选，并可勾选多个群批量开关聊天。后端未改动——`list_scenes` 本就在每行返回完整 `settings`。批量走既有的 `PUT /api/cockpit/scenes/{id}/settings`，逐群各带自己的 baseline，是对同一个带冲突检查的保存的便利封装，不绕过检查；未配置的群会被跳过并列出原因。权限词汇加进 `domain/status.js` 的 `scene_chat`/`scene_work`，沿用 StatusBadge 的既有机制。导航「群聊」拆为「群与权限」与「群聊消息」两项，旧的 `/scenes` 路由与书签不变。

**R6 抽公共脚手架（部分）**：新增 `composables/useRequestGuard.js`（14 份 `const own=++requestId` 守卫的唯一实现），已接入 `GroupsView` 与 `CapabilitiesView`；其余 12 个视图保留原内联守卫——在无法目视回归的情况下手改 12 个文件不划算。新增 `domain/roles.js` 收敛三处不一致的角色名（`OverviewView` 原为 对话/后台工作/维护整理，`ModelsView` 原为 对话/工作/维护），两处均改为引用。`AgentSettingsView` 的 scoped 样式删除 18 条死规则（3549 → 2134 字符），复核后仅剩 `v-btn`/`v-input` 两个 Vuetify 元素选择器（媒体查询内，有意保留）。

**R7 合并能力表面（部分）**：删除 `PluginsView` 里复制主导航的「系统能力」链接卡。`CapabilitiesView` 页头加「启用向导」按钮，把「看能力」与「开能力」接成一条路径。四个能力表面**未合并成一个视图**——它们职责确实不同（Capabilities 是只读证据、SetupWizards 会改配置、Overview 是摘要），合并是大改写且无法目视回归，因此只做了去重与串联。

`npm run build` 通过。

### 未做 / 未验

- **Part 1 卡住，`deployment_verified` 未翻。** Docker Desktop 已停（22:43 核对：`Cannot connect to the Docker daemon`），SnowLuma/Gateway/QQ 客户端都不在，**无法核对现场版本**。按本轮计划的自我约束「版本核不上就不翻开关」，没有翻。控件已就位，Docker 起来后读一次版本即可一键完成。
- **未重启**，提示词、卡片接线与聊天参数都没有在真实群里生效过。LenBot 进程已不在（近 20 分钟零事件）。
- **前端页面未做人工目视检查**：面板未运行，且 AGENTS.md 禁止自动截图，这一步留给运营。已完成的只有构建通过。
- `containers/browser/Dockerfile` 的 `fonts-noto-cjk` **镜像未重建，未验证**。本机渲染的中文靠 macOS 自带 PingFang SC，不能作为容器已修好的证据。
- R6 只接入 2 个视图，其余 12 份请求守卫仍是内联副本；`useSettingsDraft(domain)`（6 份 tab+baseline+dirty 脚手架）未做。
- R7 未把四个能力表面合并成单一视图，只做了去重与串联。
- 未新增、未运行测试（AGENTS.md）。

---

## 上一批（B1—B4 与 G4）

## 当前结论

**Verdict：B1—B4 与 G4 已提交，前端产物改为构建期生成；LenBot 已按运行手册停机（SIGTERM，20:53 起的进程已退出）。QQ OneBot 因 SnowLuma 重建后尚未重新登录，`deployment_verified` 仍为 false，没有任何真实文件上传回执，不能宣称已能发群文件。新代码尚未启动运行过。**

备份：`.backups/lenbot-dev-backup-20260917-203616`（HEAD、根配置、SQLite、media，477MB）。经用户授权删除其余 35 份历史快照，`.backups` 从 6.4GB 降到 477MB；保留这一份是因为它是本批在运行手册意义上的回退点，重启到新代码前不要删。

## 本批代码改动

| 批次 | 已做 | 未验 |
|---|---|---|
| B1 文件动作入口 | `respond` 在本轮有可交付候选时才公开互斥的 `intent=file` 分支（只填 `file_asset_id` + 唯一 `delivery_ref`/`work_ref`，无 segments）；`runtime_facts.files` 与 `file_delivery` 投影；工作详情已显示资产、尝试与回执 | 模型在真实请求中实际选中文件分支 |
| B2 平台与交付 | `FileUploadConfig` 显式支持 `napcat`/`snowluma` 并校验协议配对；`upload_response` 记录所选协议；`deployment_verified` 收口为「版本与挂载已人工核对」，不再要求先有成功上传；新增只读 `POST /api/websocket/read-version` 读现场 `get_version_info` 并与已声明实现/版本对照 | 真实 `FILE_UPLOADED` 与 `file_id`；只读挂载在 node 侧的实际读取 |
| B3 旁听与表情 | `scenes[group].attention` / `expression` 覆盖；单一解析器 `attention_config.effective_attention`；下一次抽样改为绝对时刻 `SceneSession.attention_sample_at`，窗口改短时收到 `now+新窗口`，改长不冻结；palette 传 tags、描述放宽到 120 字并标注截断，未发过的素材在限额内优先 | 自然聊天下的命中率、沉默比例与费用对比 |
| B4 一页群配置 | `GET/PUT /api/setup/group-quick` 一次事务保存本群设置与 `send_file` 授予（按 grant 修订比对冲突）；群列表合并 OneBot `get_group_list` 的已加入群；`SceneSettingsForm` 改为参与／能力／申请者三段加固定保存区 | 两个页面并发改同一群、窄屏与切群的实际操作 |
| G4 授予判定 | 主体／能力／范围／到期的匹配收敛到 `capabilities.grant_allows`，`CapabilityAuthority.grant_for` 与启用向导共用；已过期授予不再显示成「已授权」，向导据此提议补发并注明 | 面板上的实际预览与保存 |

同批修正：`file_delivery_facts` 原先固定取第一个全局启用的 workspace 实现判断本群是否能生成文件，两种实现同时存在而本群只开了另一个时会误报未开放，已改为按本群实际启用的实现判断。

构建管线（提交 `294dca1`）：控制面板产物不再进 Git。原先前端相关提交里 40—59／45—87 个文件是构建产物；`deploy/linux/Dockerfile` 新增 Node 阶段自建面板，`.dockerignore` 只挡装好的 toolchain 与本机产物，`pyproject.toml` 增加 source/wheel 排除。核对：`uv build --wheel` 从 47.7MB（含 3291 个 node_modules 文件）降到 13MB 且 81 个面板资源齐全；`docker build -f deploy/linux/Dockerfile .` 成功，镜像内已安装包含 78 个资源加 index.html、无 `web/frontend`；镜像内构建与本机 `npm run build` 产出相同哈希。历史未重写，旧提交仍带产物。

构建：`src/len_bot/web/frontend` 执行 `npm run build` 通过，产物留在本机工作副本供本地运行。未运行测试（按 AGENTS.md）。未做自动截图。

## 文档收敛

- `AGENTS.md` 3051 → 2661 字节，规则条目保留，去掉已过期的「前期只做契约」阶段说明。
- 删除已完成批次的 `docs/LenBot_全链路审计与产品化重构计划_1683b8a.md`（R0—R6 已交付，正文保留在 Git 提交 `3eadcad`）。
- 新增本批合同 `docs/LenBot_文件交付与群聊快速配置实施计划_3eadcad.md`；README、架构与运行手册的引用同步改到它。
- `docs/` 文档正文从 425.5KB 降到 352.1KB（含本批新增的合同 41.5KB）。

## 现场状态（未变）

| 项 | 结果 |
|---|---|
| SnowLuma 版本 | `get_version_info`：app_name=SnowLuma，app_version=`1.14.15-node`，protocol_version=v11（重建前读到） |
| `upload_group_file` | 存在；空参数返回 `group_id: is required`（1400），不是 unknown action |
| 已加入群 | `1014123451` 造密码、`126300994` 类人群星（枝江）建筑梦限公司员工群 |
| 文件目录 | 宿主 `file_assets/` 0750；当前容器只读挂到 `/lenbot-files`，node(1000) 可列出 |
| `onebot_file_upload` | `implementation=snowluma`，`protocol=upload_group_file`，`version=1.14.15-node`，`deployment_verified=false` |
| 旁听 +2 | 两群 `p=0.8`、`W=150`、`K=30`（由当时全局 0.6/300/60 计算并保存具体值） |
| 表情 | 两群 `expression.sticker_preference=slightly_more` |
| LenBot | 已停机：向主进程 81211 发 SIGTERM，81211/81209 均已退出，`127.0.0.1:11307` 不再监听；Gateway `127.0.0.1:8790`（Docker）未动 |
| OneBot | 容器内 3001 未监听；LenBot 对 `ws://127.0.0.1:13001/` 报 InvalidMessage。QQ 需在 noVNC/WebUI 完成登录后才会打开 OneBot |

停用容器 `snowluma-prev` 在登录恢复前不要删。登录态卷仍是 `qq-client-config` / `qq-client-data` / `qq-gateway-data`。

未登录面板（默认密码已失效）。未向群发送、未把 `deployment_verified` 标成已核验。18:20 开发群「群里发不了文件附件」的实发文字回退属于旧协议缺失，不是本批新回执。

## 下一步

1. 启动 LenBot 以加载本批代码（已停机；`.backups/lenbot-dev-backup-20260917-203616` 是当前回退点）。本机运行需先有面板产物，缺少时后端照常起但面板不可用。
2. 在 `http://127.0.0.1:6081`（noVNC）或 `http://127.0.0.1:5099`（SnowLuma WebUI）完成 QQ 登录，直到容器监听 3001。
3. 用连接页「读取平台实现与版本」核对现场实现与版本，确认 node 能读 `/lenbot-files`，再把 `deployment_verified` 改为 true 并重启。
4. 在群 `1014123451` 由人类提出资料整理并发送 CSV；成功证据必须是 `FILE_UPLOADED` 与真实 `file_id`（V01、V02）。
5. 再用一个不依赖日程的表格计算请求复验同一通用链（V03）。
