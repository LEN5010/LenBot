# 运行手册

面向运营者。行为含义见[产品文档](product.md)，本次切换的已完成项和未确认项见[当前任务](iteration.md)。

## 初始化与启动

从项目根目录运行，不在其他目录搜索配置。首次部署先执行 `uv sync`，仅在尚无实际配置文件时复制样例：

```sh
cp lenbot.config.example.json lenbot.config.json
```

编辑根文件中的实际连接、账号初始化信息、模型、插件和运行参数。样例中的 `runtime.bot_qq=0` 是占位，必须填写实际 Bot QQ 账号；启动要求正整数。样例不含生产凭据与群名单，不参与启动时合并。已有部署沿用一次导出的实际文件，不能用样例覆盖。文件含凭据，保存在本机并排除 Git。

| 配置节 | 内容 |
|---|---|
| `runtime` | 数据库与监听地址、身份与表达、注意力、预算、并发、媒体和维护参数 |
| `models` | 提供商连接与各角色 routing；未配置的角色明确为 null |
| `delivery` | 全局 shadow |
| `access` | 全局 QQ 回复白名单，仅用于已启用群的普通对话资格 |
| `scenes` | 每群启用、chat、开放插件、命令、公告、成员订阅和全体提及 |
| `time` | IANA 业务时区、自然周起点与下午范围；尚未填写为 null |
| `members` | 成员名称与别名、bilibili_uid、房间号；与 QQ UID 分开 |
| `plugins` | 六个内建插件的 enabled 与完整 config；未配置为 false/null |

配置解析错误会报告具体位置，缺失必需项由运营补齐。env、dotenv、CLI 和数据库不覆盖根文件。三个模型职责单独设置，不需要为启动面板强行配置所有模型；未配置能力的含义见产品文档。

确认配置和结构就绪，并取得当次生产启动授权后，在根目录执行：

```sh
uv run len-bot
```

使用启动信息与文件指定的地址打开面板。初次建号使用文件中的初始化账号，已有登录记录继续由 SQLite 保存；不要把旧文档或样例凭据当作生产登录。查看 OneBot 连接状态及当前发送设置后再进行原群操作。

## 运行中修改

运行中使用面板分节保存，保存成功才更新显示；手工编辑根文件前先正常停机。监听地址等需重启的字段显示“已保存，需重启”，不自动重启。新轮次使用新模型设置，已启动工作保留原模型绑定，恢复时原绑定不可用就明确失败。

OneBot 显式选择主动／反向 WebSocket 与发送通道，保存后需正常重启生效。插件参数保存同样按页面提示重启；HTTP 检查只检查当前正在使用的接口，不证明尚未生效的新连接可用。模型能力检查由运营主动发起，会产生真实模型请求，浏览或刷新页面不会。

工作进展冷却由 runtime.job_progress_interval_seconds 决定，正常等待回应的存续时间由 runtime.open_loop_ttl_seconds 决定。原话和工具资料共用现有字符页参数；消息检索、原文邻居、待处理目录、工具发现、摘要／发送事实候选与媒体检索的条数也从 runtime 读取。Schema 展示与实际查询使用同一组值，越界请求明确失败，存储层不另行裁切为源码里的旧页量。新增字段按样例所列的旧运行起点人工填写，样例不参与运行合并；本轮未改实际根文件。

全局暂停实发使用 Shadow；停用一个群则关闭“本群设置”的启用字段。聊天群／播报群快捷操作只填实际 chat、命令与公告等字段，不保存另一份 mode。QQ 回复白名单只在系统设置维护；白名单不会强制每条回复。恢复只修改运营明确选定的值，不从文档复制群号。已记录的候选不补发，unknown 不重试或更换通道。不要把结果就绪、任务到期或工具完成当成发送成功。

人格页点击“查看嘉然模板”，选择需要的字段并填入人格草稿；关闭预览后使用“保存人格”。表达样例另行勾选并逐条新增，每条分别显示成功或错误；缺少图片引用的样例须先补充对应素材。模板不会自动识别或替换旧默认值，不停用已有人工样例。人格文件与人工样例分属配置和数据库两个保存动作，界面分别显示结果。角色原始资料与表情索引保留在[persona](persona/diana/README.md)。

## 停机、备份与结构切换

1. 前台进程使用 Ctrl+C；后台部署向当前明确的主进程发送 SIGTERM。等待认知、维护、工作、投递和 OneBot 正常退出，再确认该实例的监听和数据库句柄已关闭。
2. 在新的备份目录保存当前代码提交或源码归档、实际根配置、指定 SQLite 数据库及其完整媒体目录。数据库使用 SQLite 的普通备份，媒体普通复制；不生成文件指纹或哈希清单。
3. 只对已确认的当前结构执行本次必要的离线转换。旧结构由对应旧版本处理，不在启动时猜测补列、补 JSON 或自动 Reset。
4. 获得当次启动授权后，使用匹配的代码、根配置与当前结构启动，按约定人工查看数据和操作。未完成转换或发生错误时保持停机，查看具体错误并决定是否恢复同批备份。

停止后的普通数据库与媒体备份示例，路径由本次部署填写：

```sh
sqlite3 /绝对路径/len_bot.db '.backup /绝对备份目录/len_bot.db'
cp -R /绝对路径/media /绝对备份目录/media
cp /项目根目录/lenbot.config.json /绝对备份目录/lenbot.config.json
```

当前媒体目录位于数据库同目录的 `media`。已保存资产的 ID、原路径和来源持续使用；不要重新下载、批量编号或改写历史事件。方案 A 已删除配置数据库表、旧升级表和媒体摘要列，已转换实例不重复执行；旧迁移与 correction 命令退出日常操作。

从 A 进入 B 时，先离线把旧 delivery.allowed_scenes 的真实群逐项对应到 scenes，再移除旧名单字段；不得从样例导入群或改全局 Shadow。新 time、members、插件具体参数由运营填写，未决定的插件明确 false/null。旧直播参数的 scene_id/room_ids 不能猜成员 UID 或自动启用订阅，应保留原文件备份后按新参数结构配置。本轮用户已决定暂不填写具体插件参数，实际根文件尚未按 B 替换，不能据源码构建成功直接启动。

旧信息工作需要一次性补齐明确业务字段。只在停机备份后对 A 的指定数据库离线执行，保留原 ID、原文和预算；缺少已保存请求人的旧工作保持 null，不从证据中的“最后一个人”猜请求者：

```sql
BEGIN IMMEDIATE;
UPDATE tasks SET payload=json_set(payload,
  '$.work_operation','information',
  '$.requester_qq_uid',CASE
    WHEN json_extract(payload,'$.requester_id') LIKE 'user:%'
      AND length(json_extract(payload,'$.requester_id'))>5
      AND substr(json_extract(payload,'$.requester_id'),6) NOT GLOB '*[^0-9]*'
    THEN substr(json_extract(payload,'$.requester_id'),6) ELSE NULL END,
  '$.summary_range',NULL,'$.summary_coverage',NULL)
WHERE json_extract(payload,'$.kind')='agent_job'
  AND json_type(payload,'$.work_operation') IS NULL;
UPDATE tasks SET payload=json_set(payload,'$.requester_qq_uid',CASE
    WHEN json_extract(payload,'$.requester_id') LIKE 'user:%'
      AND length(json_extract(payload,'$.requester_id'))>5
      AND substr(json_extract(payload,'$.requester_id'),6) NOT GLOB '*[^0-9]*'
    THEN substr(json_extract(payload,'$.requester_id'),6) ELSE NULL END)
WHERE json_extract(payload,'$.kind')='reminder'
  AND json_type(payload,'$.requester_qq_uid') IS NULL;
COMMIT;
```

这是显式离线转换，不在启动时自动修补。实际执行与核对状态只记当前任务。

回退同时恢复匹配的代码、配置、数据库与媒体；不要让旧代码打开新结构或只还原数据库。备份、运行日志与 PID 放在本地运维目录，不进入 Git。

## 日常查看与处置

首次开放新功能时，先确定一个聊天群和一个仅播报群、QQ 回复白名单与真实成员身份，填写业务时间和各来源参数。每个群先停用旧 AstrBot 同类日程／直播入口，再开放本群的对应插件与类别；同一功能同一群只保留一个负责人，不监听旧 Bot 或自动抢占。

日程的本轮确认来源为 `https://asoul.love/calendar.ics`。命令词在日程插件中映射 calendar_today、calendar_tomorrow、calendar_week，再由每群 commands 开放。评论日程时引用实际日程响应；要提问 Agent 或修改、取消工作时另发明确消息。无引用的“好耶”等内容不作隐藏语义分类。源返回空日程和请求失败含义不同，失败时先看源状态；不换来源或改发文字。

直播配置的 source_timezone 用来解释源给出的无偏移开播时间，业务时区独立保存。成员订阅选择已填写的真实主播，群的 live_started 公告和 mention_all 分别开启；@全体的账号条件由运营在实际群确认，不自动修权限或去掉提及重发。首次采样只建立基线，不补报当时已经开播的场次。是否实际生成、发送和送达分别看来源事件、公告 trace 与 action 回执。

自然询问日程、动态与已配置监测状态可直接使用短工具；当前群昨天、下午或明确区间总结由 Agent 提出同一工作。查看工作详情的请求范围、取得截点、匹配记录和完整阅读覆盖，长原文通过原资料区继续读取。动态详情仅指源已提供的记录，不据图片链接声称看过像素。

| 问题 | 沿现有入口处理 |
|---|---|
| 没有回复 | 先看输入是否保存、是否得到观察机会，再看对话调用、提交与发送回执；未唤醒旁听不是故障 |
| 对话失败或超时 | 记录原话、时间、场景和调用 ID，查看实际型号、错误与预算；不自动换型号或补发 |
| 工具资料过长 | 查看结果 ID、已展示范围与续页；原资料可继续读取，不能把未展示范围记为已读 |
| 工作迟迟未交付 | 分开看执行结果、待回应和发送阶段，核对当前目标版本及拒绝原因；不为重送完成结果恢复执行 |
| 中断工作 | 在工作详情查看原绑定、预算与完整检查点，再显式恢复符合条件的工作 |
| 维护失败 | 在场景查看失败原文范围，处理具体原因后显式重试；不删除失败范围或伪造覆盖 |
| 认识不准确 | 查看原始依据与修订链，运营撤销填写理由；保留原陈述与操作来源 |
| 图片不可用 | 查看所属场景、资产状态和原文件；缺失文件如实处理，不通过换源隐藏缺失 |
| 费用或用量疑问 | 查看所有用途的调用账，区分真实 usage、未知、取消、未确认与本地估算 |

定位时关联事件、episode／job／action ID、原始工具观察、Gate 结果和真实回执，不用时间邻近猜测因果。修改后的效果回到同一实际链路确认，构建成功不能替代实群交付或模型结论核对。

## Reset 与前端构建

Reset 是独立的破坏性管理动作，需要当次明确授权。本轮不执行。操作会先停止认知、维护、工作与投递，再清理原话、Session、摘要、自动认识和技能、任务、工作及检查点、调用账、工具资料与聊天媒体；保留登录、根配置、人工样例和运营素材及来源，追加管理记录。它不改变 Shadow、群设置或白名单。

修改前端后，在 `src/len_bot/web/frontend` 执行：

```sh
npm ci
npm run build
```

产物位于 `src/len_bot/web/static/dist`，与同批 API 一起交付。按本轮范围实际操作改动页面；不运行 pytest、自动截图、回放任务或断言式临时脚本。
