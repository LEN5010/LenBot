# 当前任务

更新时间：2026-09-17 21:55。分支 `dev/social-agent-con`，基线提交 `3eadcad`，本批提交 `294dca1`（构建管线）与本次提交（B1—B4 与 G4）。本批已获授权推进现场配置与起停。

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
