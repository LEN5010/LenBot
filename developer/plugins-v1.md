# 插件接口 v1

## 最小插件

一个目录包含 `plugin.toml`、`__init__.py`，并恰好定义一个 `Plugin` 子类。可直接复制 [counter](examples/counter/)，只使用 `len_bot.next.plugin`，不需要引用 Chat、Store 或 NetworkRuntime。

清单必填 `name`（等于目录名）、`version`、`interface = 1`、`authors`、`license`、`description`。可选 `repository`（源码仓库 HTTP(S) URL）、`homepage`（使用说明 HTTP(S) URL）。它们显示在插件详情。

`dependencies = ["包名>=版本"]` 声明 Python 依赖，支持 uv 的 requirement 写法。宿主只在用户主动安装或更新时安装依赖，不在捕获 ImportError 后自行安装重试。当前环境已有版本作为约束，冲突原样报出，不自动替换其他插件或宿主正在使用的包。

## 安装与维护

- 面板填写完整 HTTP(S) 或 `ssh://` Git 仓库 URL。仓库根就是插件目录内容，第一版不支持 ZIP、本地路径或多插件仓库子目录；私有仓库使用本机 Git 凭据配置，URL 不放密码。
- 安装器把源码放在实例 `plugins/<name>` 并登记搜索路径。没有必填参数的插件直接加载；需要参数时先以停用状态保留，填写后启用。单群使用还需在群的插件列表中打开。
- 保存插件配置或群启用会直接应用目标插件。根 `plugins.disabled` 仅记录停用的已配置插件名，停用保留参数、群选择和数据。
- 「重载」先撤下该插件的命令／工具，取消它的处理与后台任务，执行 stop，再重新导入并执行 start，刷新工具和只读技能。其他插件与聊天不重启；处理中调用会中断，使用插件文件的在途工作不承诺无损。
- 「更新」只管理安装器目录下的独立 Git 仓库，执行快进更新、依赖安装和重载。有跟踪文件的本地修改就报错；未跟踪文件不主动删除，Git 发现覆盖冲突也会报错。没有回退版本或失败后自动重启旧代码。
- 「卸载」停止插件，删除其源码与启用配置，默认保留 `plugins.data_directory/<name>`。删除 KV／素材是单独操作，要求插件已停止，不删除聊天历史。
- 内置插件跟随主程序更新；手动配置的外部源码目录支持重载，但安装器不会替用户改动那个仓库。

推荐从内置 rss_broadcast（无模型订阅）、group_digest（单次生成与任务）和 counter 模板开始。推荐名单随仓库维护，不依赖云市场、账户或评分。

## 入口

```python
from len_bot.next.plugin import Plugin, Invocation, command, fullmatch, regex, tool, background, on_notice
```

| 装饰器 | async 方法参数 | 行为 |
|---|---|---|
| `@command("查询", "说明")` | `self, ctx: Invocation, args: str` | `/查询` 后剩余文字作为 args |
| `@fullmatch("今日直播", "说明")` | `self, ctx: Invocation` | 去首尾空白后的全文相等 |
| `@regex(r"查 (?P<name>.+)", "说明", priority=0)` | `self, ctx: Invocation, match` | Python 正则 fullmatch，命名组从 match 读取 |
| `@on_notice("group_increase")` | `self, ctx: Invocation, notice` | OneBot notice_type，也支持 type.sub_type |
| `@tool("plugin_read", "说明")` | `self, ctx: Invocation, ...` | 参数类型注解生成 schema；返回字符串 |
| `@background(every="5m")` | `self, ctx: PluginContext` | 本次完成后再等待周期，最短 10 秒，不是定点 cron |

每个方法只用一个入口装饰器。匹配优先级为命令、全文、正则；正则按 priority 降序。同一消息最多一个处理器接管，不唤醒聊天模型。匹配只接受纯文本，开头的回复段和 @自己可去掉。

处理结果记录到后续对话上下文，但不会因此启动模型。处理器返回字符串不自动发消息；调用 `await ctx.reply("文字")` 才发送。大脑选择 tool 属于模型聊天轮，插件 tool 本身是否调用模型是另一回事，插件说明必须写清楚。

## 配置表单

`[config.<字段名>]` 的 `description` 必填；写了 `default` 表示可省略，否则必填。类型为 `string`、`integer`、`number`、`boolean`、`string_list`、`object_list`、`secret`。宿主解析一次，`ctx.config` 是已经校验的普通值。

- `options`：string／integer／number 的候选值，面板使用下拉框。
- `minimum`、`maximum`：integer／number 范围，后端同样校验。
- `object_list.fields`：一层对象子字段，支持普通类型、枚举和范围；不嵌套对象列表或 secret。没有声明子字段的自由对象列表仍用 JSON 编辑。

例如订阅条目：

```toml
[config.subscriptions]
type = "object_list"
description = "订阅房间"
default = []

[config.subscriptions.fields.room_id]
type = "integer"
description = "房间号"
minimum = 1

[config.subscriptions.fields.label]
type = "string"
description = "显示名称"
default = "直播间"

[config.subscriptions.fields.mode]
type = "string"
description = "订阅类型"
options = ["live", "posts"]
default = "live"
```

运行参数仍只存在根 `lenbot.config.json`。不要把 KV 当作第二份配置，也不要从环境变量覆盖面板值。

## 调用上下文

命令和全文／正则的 `Invocation.message` 是真实触发消息；工具调用可能没有 message，不据此编造请求人。`ctx.scene` 是当前启用场景。

| 能力 | 使用方式 |
|---|---|
| 回复 | `await ctx.reply(text)`，图片 `reply_image(data, description)`，组合 `reply_parts(parts)` |
| 组合消息 | `Text(text)`、`Image(bytes, description)`、`Mention(qq)` 从公共入口导入 |
| KV | `get_kv(key, default=None)`、`set_kv(key, JSON值)`、`delete_kv(key)`；均 await |
| 原消息 | `ctx.recent_messages(limit=20)`，只读当前场景，最多 100 条 |
| 记忆 | `await ctx.memory(arguments)`，当前场景记忆服务，不直连后端数据库 |
| 主动交给大脑 | `await ctx.emit_event(text)`，与直接发送相反，它会唤醒大脑 |
| 时间 | `ctx.now()` 和 `ctx.timezone()`，明确场景时区 |

`PluginContext` 用于 `self.ctx`、start／stop 和后台入口；发送、读消息、记忆、事件方法要显式传启用场景，例如 `await self.ctx.send(scene, text)`。`self.ctx.scenes` 列出启用场景，`data_dir` 是该插件独立数据目录。KV 只按插件隔离，需要分群时把 `scene` 放进 key。

发送返回 `Sent`：区分 sent、failed、unconfirmed、simulated、partial；只有 sent 是平台确认，客户端体验仍需实际观察。

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

`generate` 的 role 只能是现有 `mind`、`voice`、`learner` 用途绑定；未配置的用途直接报错，不换模型。`PluginContext.generate` 需先传 scene。调用复用 ModelSlots、原有预算及用量记录，插件来源写进 `model_calls.plugin`，没有虚假的聊天 turn。完整系统提示与输入由插件提供，宿主不自动注入角色或历史。

`Invocation.delegate` 参数为 goal、deliverable，以及可选 context、materials（本场景共享资料文件名列表）。通知、cron 和没有触发消息的工具上下文不能委派；不传伪造的 requester。任务沿既有场景、发送者权限及角色技能许可执行。返回只表示已接单，结果事件由宿主回到群会话；不要用 `wait` 轮询占住聊天。

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

内置的 [RSS 播报与群总结示例](builtin-plugins.md) 分别覆盖无模型、单次模型、独立工作三种路径。

## 生命周期和错误

`start()` 建立资源，`stop()` 关闭资源；用 `self.ctx.start_task(name, coroutine)` 登记自有后台协程，宿主停止时会取消。它不是容器工作任务。不在 asyncio 主循环里跑阻塞网络请求。

一次处理器报错结束该次调用，原错由宿主记录，不自动重试、换服务或停用整个插件。需要特权的具体入口可用 `ctx.plugin.require_owner(ctx.scene, ctx.message.sender.uid)`；不必给普通查询加主人门槛。

插件是同进程代码，共享宿主依赖环境，不能宣称沙箱隔离。不使用 `ctx.plugin.host` 穿透到内部运行时。接口扩展随版本发布；当前安装与生效方式见 [示例说明](examples/counter/README.md)。
