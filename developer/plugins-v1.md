# 插件接口 v1

## 最小插件

一个目录包含 `plugin.toml`、`__init__.py`，并恰好定义一个 `Plugin` 子类。可从[插件模板](https://github.com/lendevs/lenbot-plugin-template)开始，只使用 `len_bot.plugin`，不需要引用 Chat、Store 或 NetworkRuntime。

清单必填以下字段。`name` 等于安装目录名；仓库根或 ZIP 根不要求预先使用该目录名。公共接口代次为 **1**，自 0.2.0 起冻结：之后只做兼容增加（新的可选参数、新方法、新的清单可选字段），不改已有签名和语义；确需破坏时升代。宿主只加载当前代次，不猜旧包字段。

```toml
name = "counter"
version = "1.0.0"
interface = 1
requires_lenbot = ">=0.2,<1"
requires_python = ">=3.13"
platforms = ["linux", "darwin", "win32"]
reload = "plugin"
authors = ["插件维护者"]
license = "GPL-3.0-only"
description = "每群独立计数"
```

`version` 按 Python packaging 版本规范解析和规范化，推荐 X.Y.Z；两个 requires 字段是显式版本范围，与实际宿主和解释器比较。`platforms` 使用 Python 的 `sys.platform` 名称，宿主有 Linux（`linux`）、macOS（`darwin`）和 Windows（`win32`）三种部署包。`reload` 为 `plugin`（允许单插件换版）或 `host`（需要宿主重启）。可选 `repository`、`homepage` 是 HTTP(S) 地址。可选 `data_version`（正整数，默认 1）是插件数据目录和 KV 的格式编号，改变数据形状时提高它并实现 `migrate_data`，见下文[数据版本](#数据版本)。

`dependencies = ["包名>=版本"]` 声明 Python 依赖，由 uv 解析。当前环境已有版本作为约束，冲突原样报出，不自动换服务或改版本重试。系统软件、外部服务及凭据要求写在插件 README。公开作者入口是 `len_bot.plugin`（含它导出的 `ChatMessage`、`Notice`、`Sender`、`Segment` 消息类型）、下文的 `len_bot.plugin_testing`，以及 `len_bot.image_assets`、`len_bot.text_cards` 辅助模块；`len_bot.next` 下的内部模块不承诺兼容。

## 安装与维护

- 在面板手填完整 HTTPS 或 `ssh://` Git URL，可选标签、分支或提交；仓库根包含清单和包入口。私有仓库用本机 Git 凭据，URL 不放密码。未指定 ref 时记录默认分支，更新沿用该分支；显式 ref 的更新重新获取同一 ref，不自动切最新版本。
- ZIP 导入支持清单在根目录，或全部内容在唯一顶层目录且直接含清单。必须有 `__init__.py`；路径越界、重复文件、符号链接、Python 字节码、超过 4096 项或上传／解压超过 200 MiB 都会报错，Finder 元数据忽略。ZIP 源码可离线准备，依赖安装可能仍需要网络。
- 两种入口都只**准备候选**：下载、解析、检查身份与兼容，保存来源。当前运行版本继续运行，不导入候选、不修改 Python 环境。面板随后填写候选配置并保存，点击应用；新插件初始停用，应用成功后可启用并选群。
- 插件统一放在实例根的 `plugins/`：`plugins/<name>` 是已安装源码，`plugins/.candidates/<name>` 是候选，`plugins/.installations/<name>.json` 保存 Git commit／ZIP SHA-256、已安装和候选版本、生效方式、是否已选择重启应用及最近错误。生效参数和群选择在根 `lenbot.config.json`；候选参数暂存在 `.installations/.candidate-values/`，应用对应源码时才发布，取消候选不会把新字段留给旧版本。业务数据只在 `plugins.data_directory/<name>`，默认 `plugins/.data/<name>`。`plugins.paths` 默认是 `["plugins"]`，手工复制进 `plugins/` 的插件同样会被发现；手工目录不被安装器接管。
- Git 受管源码有本地修改（包括未跟踪文件，宿主生成的 `__pycache__` 除外）就停止应用。ZIP 换版重新导入；同名包更换 Git 仓库或 Git／ZIP 来源需选“替换安装来源”，沿用原插件数据身份。
- 无依赖变化且 `reload=plugin`：停止目标插件，切换源码，实际重新导入／start。保存普通参数或选群也只重载目标插件。重载会中断它的在途处理，其他插件和聊天继续运行。
- 依赖声明变化或 `reload=host`：应用动作选定候选，面板列为待重启。明确重启时，启动器等待旧宿主退出，再由 `maintenance.apply_plugins` 持实例锁，核对候选配置、一次安装合并依赖、切源码，最后启动新宿主。普通启动不消费候选。失败结束本次启动器，候选和原错保留；修正后显式再操作，不自动回滚环境。
- 直接运行 host 的实例停机后，在实例根执行 `python -m len_bot.next.maintenance.apply_plugins`，再明确启动。它只处理面板已经选择应用的候选。重建环境后用 `python -m len_bot.next.maintenance.plugin_dependencies` 恢复已安装插件声明依赖；首次安装尚未应用的候选不参与恢复，已有安装的插件按已安装源码恢复。使用目标环境的解释器及 uv；服务和容器说明见[部署](../deploy/current/README.md)。
- “取消候选”保留已安装源码；首次安装尚未应用时取消会移除其配置入口。停用保留参数、群选择和数据；卸载删除源码、候选和启用配置，保留插件业务数据和共享依赖包。删除数据仍是插件停止后的单独动作。
- 应用新版本时，被替换的源码移到 `plugins/.previous/<name>`，同时保留当时的参数，面板可「回到上一版本」一次。新版本加载、启动或数据迁移失败时，宿主回到上一版本源码和参数；如果新版本已经迁移了数据，数据也恢复成迁移前的备份。面板上手动回退换源码和参数，不动数据（见下文）。
- 增加必填配置或删除字段须在应用前明确填写；宿主不自动删除未知配置。

### 数据版本

插件数据放在 `ctx.data_dir`（含 KV 文件）。宿主在其中记录 `.lenbot-data.json`，写明数据按哪个 `data_version` 写成；没有记录的已有数据算版本 1，全新的空目录直接记为清单当前版本。

清单的 `data_version` 比记录高时，宿主在 `start()` 之前：

1. 把整个数据目录复制到 `plugins.data_directory/.backups/<name>-v<旧版本>-<时间>`；
2. 调用 `await plugin.migrate_data(from_version)`，此时插件已加载，可以读写 `ctx.data_dir` 和 KV，但还没有 `start()`；
3. 成功后写入新版本号并启动；抛错时把数据目录恢复成迁移前的样子，插件标记为失败，原错写进日志。

一次迁移要能从任何受支持的旧版本走到当前版本（按 `from_version` 依次处理）。没有实现 `migrate_data` 却提高了 `data_version` 会直接失败。清单的 `data_version` 比记录低时宿主拒绝启动：旧版本插件不读新数据，回退版本需要同时从 `.backups` 恢复对应的数据。

[独立插件与示例](plugin-examples.md)覆盖命令接管、生成与委派、外部服务。发现页仍使用[静态目录](plugin-catalog.md)，首版没有市场后端。

## 入口

```python
from len_bot.plugin import Plugin, Invocation, command, fullmatch, regex, tool, background, on_notice
```

| 装饰器 | async 方法参数 | 行为 |
|---|---|---|
| `@command("查询", "说明")` | `self, ctx: Invocation, args: str` | `/查询` 后剩余文字作为 args |
| `@fullmatch("今日直播", "说明")` | `self, ctx: Invocation` | 去首尾空白后的全文相等 |
| `@regex(r"查 (?P<name>.+)", "说明", priority=0)` | `self, ctx: Invocation, match` | Python 正则 fullmatch，命名组从 match 读取 |
| `@on_notice("group_increase")` | `self, ctx: Invocation, notice` | OneBot notice_type，也支持 type.sub_type |
| `@tool("plugin_read", "完整说明", summary="发现简介", needs_source=False)` | `self, ctx: Invocation, ...` | 参数类型生成 schema；返回文本或 JSON 值 |
| `@background(every="5m")` | `self, ctx: PluginContext` | 本次完成后再等待周期，最短 10 秒，不是定点 cron |

每个方法只用一个入口装饰器。匹配优先级为命令、全文、正则；正则按 priority 降序。同一消息最多一个处理器接管，不唤醒聊天模型。匹配只接受纯文本，开头的回复段和 @自己可去掉。

处理结果记录到后续对话上下文，但不会因此启动模型。处理器返回字符串不自动发消息；调用 `await ctx.reply("文字")` 才发送。大脑选择 tool 属于模型聊天轮，插件 tool 本身是否调用模型是另一回事，插件说明必须写清楚。

## 配置表单

面板按 `[config.<字段名>]` 的声明生成表单，运营者不用手写 JSON。每个字段的 `type` 和 `description` 必填；写了 `default` 表示可省略，否则必填。宿主解析一次，`ctx.config` 是已经校验的普通值。

| 类型 | 值 | 面板控件 |
| --- | --- | --- |
| `string` | 文字 | 输入框；`multiline = true` 时为多行文本框 |
| `secret` | 文字，面板不回显 | 密码框，已保存时留空表示不修改 |
| `integer`、`number` | 整数、数字 | 数字输入框 |
| `boolean` | 开关 | 开关 |
| `string_list` | 文字列表 | 多行文本框，每行一项 |
| `scene` | 一个群，例如 `onebot:group:123` | 从宿主已配置的群里选，显示群名 |
| `scene_list` | 多个群 | 多选，显示群名 |
| `path` | 绝对路径 | 输入框 |
| `url` | http 或 https 地址 | 输入框 |
| `object_list` | 对象列表 | 每项一张卡片，卡片标题显示前几个已填的值 |

通用的可选属性：

- `label`：表单里显示的名字，缺省时显示字段名。建议写成运营者看得懂的中文。
- `group`：分组标题，只用于顶层字段。同组字段放在一起，没分组的排在最前。
- `placeholder`：输入框里的示例文字，用于 string、secret、integer、number、path、url。
- `options`：string／integer／number 的候选值，面板用下拉框。可以直接写值，也可以写 `{ value = "auto", label = "自动" }`，保存 value、显示 label。
- `minimum`、`maximum`：integer／number 的范围，后端同样校验。
- `object_list.fields`：一层对象子字段，可用除 secret 和 object_list 以外的类型；子字段不能设 `group`。没有声明子字段的对象列表只能按 JSON 编辑，面板会提示补充声明。

`path` 和 `url` 写了 `default = ""` 时允许留空，表示不填；没写默认值的必须填有效值。

`scene` 和 `scene_list` 保存和加载时都会核对群是否在宿主配置里；插件作者不知道运营者有哪些群，所以 `scene` 不能写 `default`，`scene_list` 的 `default` 只能是 `[]`。后来从配置里删掉的群仍留在插件参数里时，插件加载失败并报出这个群，需要在面板里改掉。

例如订阅条目：

```toml
[config.card_mode]
type = "string"
label = "卡片样式"
group = "显示"
description = "推送消息用哪种样式"
options = [{ value = "auto", label = "自动" }, { value = "image", label = "图片" }]
default = "auto"

[config.subscriptions]
type = "object_list"
label = "订阅的直播间"
description = "每个直播间单独选推送到哪些群"
default = []

[config.subscriptions.fields.room_id]
type = "integer"
label = "房间号"
description = "直播间页面地址里的数字"
minimum = 1

[config.subscriptions.fields.name]
type = "string"
label = "显示名称"
description = "推送消息里用的名字"
default = "直播间"

[config.subscriptions.fields.scenes]
type = "scene_list"
label = "推送到的群"
description = "开播时在这些群里提醒"
default = []
```

运行参数仍只存在根 `lenbot.config.json`。不要把 KV 当作第二份配置，也不要从环境变量覆盖面板值。

## 工具说明如何交给聊天模型

工具有三层说明：`summary` 用于发现目录与搜索结果；`description` 是实际调用的完整说明；类型、默认值和 `Annotated[..., Field(...)]` 提供参数 schema。`summary` 可省略，此时完整 description 原样作为简介，不按标点截断。Field 的 description、examples、枚举与范围保留。函数 docstring、README、清单介绍和后台任务技能不自动进入聊天提示。

角色须同时允许 `tool_search` 和实际工具名。聊天模型先从短目录选择能力，再按完整工具名搜索；原词搜索是辅助，不做语义检索。命中工具从下一次请求开始可用。目录、搜索结果和面板使用同一发现简介与来源，实际工具定义始终提供完整说明和参数 schema。

跨工具流程放在可选共享指南中：

```toml
[model]
instructions = "prompts/tools.md"
```

文件路径必须位于插件目录内。当前角色允许且已发现插件工具时，指南进入系统提示；同插件多个工具只加载一份。定向重载刷新它；停用、卸载或压缩清除发现状态后撤下。写真实 ID 的传递、分页、参数选择和结果含义，不复制整份 README，也不把凭据写进去。

```python
from typing import Annotated
from pydantic import Field
from len_bot.plugin import Invocation, Plugin, tool

class Example(Plugin):
    @tool("recent_count", "统计当前群指定时段的消息数量；不发送。",
          summary="查询本群最近几小时的消息数")
    async def count(self, ctx: Invocation,
                    hours: Annotated[int, Field(ge=1, le=72,
                        description="向前回溯的小时数", examples=[12])] = 24) -> dict:
        before = ctx.now()
        after = before - hours * 3600
        count = 0
        while True:
            page = ctx.messages_between(after, before, offset=count, limit=500)
            count += len(page)
            if len(page) < 500:
                return {"hours": hours, "count": count}
```

工具可直接返回 dict、list、int、float、bool、None 等 JSON 值，宿主在调用边界统一编码。返回 str 保持原文，原有 JSON 文本工具继续可用；不支持任意 Python 对象、非字符串对象键、tuple 或非有限浮点数。当前群由宿主确定，不让模型填写群号。

查询返回数据；发送返回实际 Sent 字段；后台生成返回 `{"status": "started", "delivery": "plugin"}` 等业务结果，并在说明里明确完成后自行发送。这是写法建议，不是强制业务 schema。只有 sent 表示平台确认；未知或失败结果不当作成功，也不自动重发。

## 工具按能力组织

不要把每个外部接口都做成工具。列表、搜索、详情属于同一查询能力时，用严格的 action 请求分支；查询与发送／账号写入分开。A-SOUL 只暴露日程、动态、二创、发卡片四项；B 站只暴露视频、搜索、监测、账号读取、账号写入五项。群总结两项、GSUID 一项、计数模板两项已按用途拆分，无需凑成一个万能工具。

```python
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

class Query(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

class Search(Query):
    action: Literal["search"]
    query: str = Field(description="关键词")

class Read(Query):
    action: Literal["read"]
    item_id: str = Field(description="来源列表返回的真实 ID")

Request = Annotated[Search | Read, Field(discriminator="action")]

@tool("content_query", "搜索或读取内容，不发送。", summary="查询内容")
async def query(self, ctx: Invocation,
                request: Annotated[Request, Field(description="搜索或详情请求")]) -> dict:
    if request.action == "search":
        return await self.search_source(request.query)
    return await self.read_source(request.item_id)
```

模型传 `{"request": {"action": "read", "item_id": "真实ID"}}`。read 不接受 query；未知字段、缺少 ID 或无效 action 在参数边界报错。保持可调用定义的作用范围明确，不堆几十个互斥的可选参数。

`PluginTest.preview_tools()` 和面板显示与实际模型定义相同的参数：去掉生成的 title，保留业务属性名、说明、示例、枚举与约束。减少工具数后也要检查目录、共享指南和加载 schema 的体积，不能把数量本身当作上下文节省。

## 工具的真实请求来源

委派工作和账号操作可声明 `needs_source=True`。宿主仅为这些工具增加必填 `source_message_id`，作者不要在函数参数中声明它。模型从群聊记录选择实际请求消息，宿主按当前场景的真实平台消息 ID 读取发送者，放入 `Invocation.message`。

```python
@tool("owner_action", "按主人真实请求执行操作。",
      summary="主人操作", needs_source=True)
async def action(self, ctx: Invocation) -> dict:
    ctx.require_owner()
    return {"requester": ctx.message.sender.uid}
```

来源必须是当前群已保存、未撤回、非机器人自己的接收消息；已知其他 Bot 和黑名单账号不可使用。群请求归属由模型判断，宿主不默认最后一个发言人，也不接受模型提供的主人账号代替来源。普通查询不需要此字段。

## 配置可用性

插件可按当前配置返回不可用的工具名和原因：

```python
def unavailable_tools(self, scene: str) -> dict[str, str]:
    if not self.ctx.config["account_read_enabled"]:
        return {"account_read": "账号读取未开启"}
    return {}
```

这些工具保留在面板预览中，但不进入发现目录，也不能执行；配置保存后的定向重载刷新能力。入口是同步、无网络的配置判断，不代替实际请求人的权限检查。原因和指南不能包含凭据。面板「群聊 → 工具」统一显示群启用、角色许可、配置原因、发现简介、完整说明、schema 与共享指南。

## 调用上下文

命令和全文／正则的 `Invocation.message` 是真实触发消息；`needs_source=True` 工具有宿主解析的真实来源，普通工具没有 message。`ctx.scene` 是当前启用场景，例如 `onebot:group:80001`；`ctx.message.sender.uid` 和 `self.ctx.bot_id` 是带平台的账号，例如 `onebot:70001`。提及使用 `Mention("onebot:70001")`。

| 能力 | 使用方式 |
|---|---|
| 回复 | `await ctx.reply(text)`，图片 `reply_image(data, description)`，组合 `reply_parts(parts)` |
| 组合消息 | `Text(text)`、`Image(bytes, description)`、`Mention(user)` 从公共入口导入 |
| KV | `get_kv(key, default=None)`、`set_kv(key, JSON值)`、`delete_kv(key)`；均 await |
| 原消息 | `ctx.recent_messages(limit=20)`，只读当前场景，最多 100 条 |
| 时间段消息 | `ctx.messages_between(after, before, offset=0, limit=200)`，Unix 时间 `after <= time < before`，按时间从早到晚，每页最多 500 条，用 offset 翻页 |
| 记忆 | `await ctx.memory(arguments)`，当前场景记忆服务，不直连后端数据库 |
| 主动交给大脑 | `await ctx.emit_event(text)`，与直接发送相反，它会唤醒大脑 |
| 时间 | `ctx.now()` 和 `ctx.timezone()`，明确场景时区 |
| 主人权限 | `ctx.require_owner()`，使用真实来源发送者 |
| 公网原图 | `await ctx.fetch_image(url, timeout_seconds=15)`，返回已校验原件 bytes |

`PluginContext` 用于 `self.ctx`、start／stop 和后台入口；发送、读消息、记忆、事件方法要显式传启用场景，例如 `await self.ctx.send(scene, text)`。`self.ctx.scenes` 列出启用本插件且聊天开着的场景；管理员在面板关闭某个群的聊天后，该群不再出现在这里，插件指令和通知也不再分发到该群，向它发送或发事件会报错。`data_dir` 是该插件独立数据目录。KV 只按插件隔离，需要分群时把 `scene` 放进 key。

发送返回 `Sent`：区分 sent、failed、unconfirmed、simulated、partial；只有 sent 是平台确认，客户端体验仍需实际观察。原生 JSON 返回回执时可把 message_ids 转为 list。

`PluginContext.fetch_image` 与 `Invocation.fetch_image` 复用宿主公网地址检查、重定向规则、实例 fake-ip 配置以及图片字节／像素上限；返回完整已验证图片，不自动切服务或重试。`image_assets.inspect_image`、`MAX_IMAGE_BYTES`、`MAX_IMAGE_PIXELS`、`OriginalImage`，以及 `text_cards.CardSection`、`TextCards`、`CardPage` 是稳定公共辅助能力。外部业务协议、签名和轮询仍属于插件。耗时绘图使用 `await asyncio.to_thread(renderer, ...)`，不阻塞聊天主循环。

## 单次生成、工作与定点播报

三条路径分开选择，不把所有命令都交给聊天模型：

```python
# 普通查询：直接 Python 处理与发送。
await ctx.reply("今天的订阅内容")

# 显式单次模型生成，无工具循环、不唤醒群会话、不自动发送。
text = await ctx.generate("待处理的资料", role="mind", system="本次整理要求")
await ctx.reply(text)

# 长工作或文件：ctx 必须来自真实消息，宿主使用该消息发送者。
task = await ctx.delegate("整理刚才的活动", "CSV 文件", context="实际活动记录")
```

`generate` 的 role 只能是现有 `mind`、`learner` 用途绑定；未配置的用途直接报错，不换模型。`PluginContext.generate` 需先传 scene。调用复用 ModelSlots、原有预算及用量记录，插件来源写进 `model_calls.plugin`，没有虚假的聊天 turn。完整系统提示与输入由插件提供，宿主不自动注入角色或历史。

`Invocation.delegate` 参数为 goal、deliverable，以及可选 context、materials（本场景共享资料文件名列表）。通知、cron 和普通无来源工具上下文不能委派；`needs_source=True` 工具可沿用真实消息发送者委派，不传伪造的 requester。任务沿既有场景、发送者权限及角色技能许可执行。返回只表示已接单，结果事件由宿主回到群会话；不要用 `wait` 轮询占住聊天。

在 `start()` 登记定点任务，handler 收到无 message 的 Invocation：

```python
async def start(self):
    for scene in self.ctx.scenes:
        self.ctx.cron("日报", "0 8 * * *", self.publish,
                      scene=scene, timezone=self.ctx.timezone(scene))

async def publish(self, ctx):
    await ctx.reply("定点播报，不调用模型")
```

同一插件／场景里的任务名唯一。使用明确 IANA 时区和五段 cron；复用宿主时间计算，夏令时缺失／重复时刻原样报错。登记只在本次生命周期内有效，重启后从当前时间计算下一次，不补发离线期间的次数。`background` 仍用于完成后间隔轮询。单次失败记原错并结束本次，下一次是新的日程，不是失败重试。

插件可带 `skills/<技能名>/SKILL.md` 和相关资源。宿主只为启用该插件的场景提供目录，并继续应用角色 `skills` 许可；任务以只读方式挂载。名称遵循现有技能命名规则，与其他来源冲突就报错。插件技能可在面板查看，不能通过普通技能的移动／删除操作改写插件源码。

[插件模板](https://github.com/lendevs/lenbot-plugin-template)和[独立群总结插件](https://github.com/lendevs/lenbot-plugin-group-digest)覆盖无模型、单次模型、独立工作三种路径。

## 生命周期和错误

`start()` 建立资源，`stop()` 关闭资源；启动协程也由宿主拥有，重载或停用会先取消并等待未完成的启动，再串行关闭该实例的资源。用 `self.ctx.start_task(name, coroutine)` 登记自有后台协程，宿主停止时会取消。它不是容器工作任务。不在 asyncio 主循环里跑阻塞网络请求。

一次处理器报错结束该次调用，原错由宿主写进运行日志（`plugin_error`），不自动重试、换服务或停用整个插件。插件自己的记录用 `self.ctx.log`（标准 `logging.Logger`），写进宿主的 `logs/lenbot.jsonl`，自动带上插件名和当前的群、一轮、工具调用 ID；面板日志页可按插件筛选。需要特权的具体入口可用 `ctx.require_owner()`；不必给普通查询加主人门槛。

插件是同进程代码，共享宿主依赖环境，不能宣称沙箱隔离。不使用 `ctx.plugin.host` 穿透到内部运行时。接口扩展随版本发布；安装与生效方式见[安装与维护](#安装与维护)。

## 本地测试与发布

宿主提供无需启动 OneBot、面板或 worker 的公共测试入口，使用实际生命周期、配置、入口匹配、工具校验和磁盘 KV，发送仅捕获为 simulated：

```python
from pathlib import Path
from len_bot.plugin_testing import PluginTest

async def check():
    async with PluginTest(Path("counter"), config={"step": 2}) as bot:
        assert await bot.message("计数加一")
        assert bot.deliveries[-1].text == "本群计数：2"
        result = await bot.tool("counter_read", {})
        assert '2' in result
```

`scenes` 和 `owners` 可在构造器指定，`scene`／`scene_list` 参数按 `scenes` 校验；`message(text, scene=..., sender=...)` 模拟实际身份，返回是否被接管。`deliveries` 包含插件、场景、组合内容、reply_to 和 simulated 状态；`events()` 查看实际记录的插件事件。处理器或启动失败会使本次测试报错，退出上下文会停止插件并删除临时实例。模型默认关闭；`models=` 明确配置测试模型时，generate 经过真实宿主协议、预算与用量记录。记忆或工作服务仍明确报错；插件自行调用外部网络仍会执行，同进程测试不是沙箱。

测试数据迁移时传入旧数据目录：`PluginTest(package, data=Path('tests/data-v1'), data_version=1)`，进入上下文时会像真实升级一样先跑 `migrate_data` 再 `start()`。

固定时钟使用 `PluginTest(..., now=lambda: 1791298800)`。`add_message(text, sender=..., scene=..., message_id=..., timestamp=...)` 保存测试消息并返回 ChatMessage；用其 platform_message_id 调用需要来源的工具。无需修改宿主私有字段。

`preview_tools(scene)` 返回发现简介、完整说明、共享指南、参数、needs_source、允许群与配置原因。`tool()` 经过真实调用边界，结果为模型实际收到的字符串。`wait_tasks(name_prefix)` 仅等待作者明确命名的后台任务，勿选择永久轮询或连接任务。

测试本机模型协议时可传入完整模型配置，例如：

```python
models = {
    "providers": {"test": {"api": "openai-chat", "base_url": local_server_url,
                           "api_key": "synthetic"}},
    "roles": {"mind": {"provider": "test", "model": "local",
                       "context_window_tokens": 8192}},
}
async with PluginTest(package, models=models, now=lambda: fixed_time) as bot:
    print(bot.preview_tools())
```

模型配置不是网络沙箱；用本机协议服务验证，不填写生产凭据。模板同时提供 JSON 查询 `counter_read` 与后台生成／自行发送 `counter_card`。四个业务插件与[回放场景](../examples/plugin-tools/README.md)用于核对真实 ID、时间范围与结果含义；协议通过不表示真实模型选择或平台送达已经实测。

在 GitHub 上打开[插件模板仓库](https://github.com/lendevs/lenbot-plugin-template)，点 Use this template 生成自己的仓库。模板 CI 调用 LenBot 提供的可复用工作流 `plugin-ci.yml`，默认用宿主 `master` 跑插件测试和打包检查，可传入 `host_ref` 固定宿主版本；打 `v*` 标签时，发布工作流把完整运行源码、prompts／skills／assets 资源及许可说明打成可导入的 ZIP 挂到 Release。发布前填写自己的 name／authors，更新版本及兼容范围。

**许可证**：LenBot 宿主采用 AGPL-3.0-only，插件模板和计数示例采用 GPL-3.0-only。插件和宿主运行在同一个进程里，推荐插件也使用 GPL-3.0；GPLv3 第 13 条允许 GPLv3 作品与 AGPLv3 作品组合使用。选择其他许可证前，请自行确认它与 GPLv3／AGPLv3 兼容。

[English](plugins-v1.en.md)

0.2.0 仍未发行。官方插件的 CI 跟随宿主 `master`，宿主 CI 也会按插件目录固定的版本跑官方插件测试，两边任何一方破坏兼容都会直接失败。公开后补丁版保持兼容，新接口最低宿主版本写进 requires_lenbot；破坏接口才升代。
