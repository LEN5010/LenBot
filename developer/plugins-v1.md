# 插件接口 v1

## 最小插件

一个目录包含 `plugin.toml`、`__init__.py`，并恰好定义一个 `Plugin` 子类。可直接复制 [counter](examples/counter/)，只使用 `len_bot.next.plugin`，不需要引用 Chat、Store 或 NetworkRuntime。

清单必填以下字段。`name` 等于安装目录名；仓库根或 ZIP 根不要求预先使用该目录名。公共接口代次为 **1**，同代接口兼容增加，破坏签名或语义时升代。宿主只加载当前代次，不猜旧包字段。

```toml
name = "counter"
version = "1.0.0"
interface = 1
requires_lenbot = ">=0.1,<1"
requires_python = ">=3.13"
platforms = ["linux", "darwin", "win32"]
reload = "plugin"
authors = ["插件维护者"]
license = "MIT"
description = "每群独立计数"
```

`version` 按 Python packaging 版本规范解析和规范化，推荐 X.Y.Z；两个 requires 字段是显式版本范围，与实际宿主和解释器比较。`platforms` 使用 Python 系统名称；列出 win32 不表示宿主已有 Windows 原生发行包。`reload` 为 `plugin`（允许单插件换版）或 `host`（需要宿主重启）。可选 `repository`、`homepage` 是 HTTP(S) 地址。

`dependencies = ["包名>=版本"]` 声明 Python 依赖，由 uv 解析。当前环境已有版本作为约束，冲突原样报出，不自动换服务或改版本重试。系统软件、外部服务及凭据要求写在插件 README。公开作者入口是 `len_bot.next.plugin`、它返回的 `len_bot.next.platform.messages` 类型，以及下文的 `len_bot.next.plugin_testing`；其他内部模块不承诺兼容。

## 安装与维护

- 在面板手填完整 HTTP(S) 或 `ssh://` Git URL，可选标签、分支或提交；仓库根包含清单和包入口。私有仓库用本机 Git 凭据，URL 不放密码。未指定 ref 时记录默认分支，更新沿用该分支；显式 ref 的更新重新获取同一 ref，不自动切最新版本。
- ZIP 导入支持清单在根目录，或全部内容在唯一顶层目录且直接含清单。必须有 `__init__.py`；路径越界、重复文件、符号链接和超过 200 MiB 的解压内容会报错。ZIP 源码可离线准备，依赖安装可能仍需要网络。
- 两种入口都只**准备候选**：下载、解析、检查身份与兼容，保存来源。当前运行版本继续运行，不导入候选、不修改 Python 环境。面板随后填写候选配置并保存，点击应用；新插件初始停用，应用成功后可启用并选群。
- 实例 `plugins/<name>` 保存已安装源码，`.plugin-candidates/<name>` 保存候选，`plugin-installations/<name>.json` 保存 Git commit／ZIP SHA-256、已安装和候选版本、生效方式、是否已选择重启应用及最近错误。参数和群选择只在根 `lenbot.config.json`，业务数据只在 `plugins.data_directory/<name>`。手工目录不被安装器接管，内置同名包不能覆盖。
- Git 受管源码有本地修改（包括未跟踪文件，宿主生成的 `__pycache__` 除外）就停止应用。ZIP 换版重新导入；同名包更换 Git 仓库或 Git／ZIP 来源需选“替换安装来源”，沿用原插件数据身份。
- 无依赖变化且 `reload=plugin`：停止目标插件，切换源码，实际重新导入／start。保存普通参数或选群也只重载目标插件。重载会中断它的在途处理，其他插件和聊天继续运行。
- 依赖声明变化或 `reload=host`：应用动作选定候选，面板列为待重启。明确重启时，启动器等待旧宿主退出，再由 `maintenance.apply_plugins` 持实例锁，核对候选配置、一次安装合并依赖、切源码，最后启动新宿主。普通启动不消费候选。失败结束本次启动器，候选和原错保留；修正后显式再操作，不自动回滚环境。
- 直接运行 host 的实例停机后，在实例根执行 `python -m len_bot.next.maintenance.apply_plugins`，再明确启动。它只处理面板已经选择应用的候选。重建环境后用 `python -m len_bot.next.maintenance.plugin_dependencies` 恢复已安装插件声明依赖。使用目标环境的解释器及 uv；服务和容器说明见[部署](../deploy/current/README.md)。
- “取消候选”保留已安装源码；首次安装尚未应用时取消会移除其配置入口。停用保留参数、群选择和数据；卸载删除源码、候选和启用配置，保留插件业务数据和共享依赖包。删除数据仍是插件停止后的单独动作。
- 插件自有文件／KV 格式由作者维护。增加必填配置或删除字段须在应用前明确填写；数据转换用作者提供的停机命令。选择旧源码不代表数据能回退，宿主不自动删除未知配置或回滚 KV。

[四个内置插件](builtin-plugins.md)覆盖命令接管、无模型订阅、生成与委派、外部服务。发现页仍使用[静态目录](plugin-catalog.md)，首版没有市场后端。

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

命令和全文／正则的 `Invocation.message` 是真实触发消息；工具调用可能没有 message，不据此编造请求人。`ctx.scene` 是当前启用场景，例如 `onebot:group:80001`；`ctx.message.sender.uid` 和 `self.ctx.bot_id` 是带平台的账号，例如 `onebot:70001`。提及使用 `Mention("onebot:70001")`。

| 能力 | 使用方式 |
|---|---|
| 回复 | `await ctx.reply(text)`，图片 `reply_image(data, description)`，组合 `reply_parts(parts)` |
| 组合消息 | `Text(text)`、`Image(bytes, description)`、`Mention(user)` 从公共入口导入 |
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

`generate` 的 role 只能是现有 `mind`、`learner` 用途绑定；未配置的用途直接报错，不换模型。`PluginContext.generate` 需先传 scene。调用复用 ModelSlots、原有预算及用量记录，插件来源写进 `model_calls.plugin`，没有虚假的聊天 turn。完整系统提示与输入由插件提供，宿主不自动注入角色或历史。

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

`start()` 建立资源，`stop()` 关闭资源；启动协程也由宿主拥有，重载或停用会先取消并等待未完成的启动，再串行关闭该实例的资源。用 `self.ctx.start_task(name, coroutine)` 登记自有后台协程，宿主停止时会取消。它不是容器工作任务。不在 asyncio 主循环里跑阻塞网络请求。

一次处理器报错结束该次调用，原错由宿主记录，不自动重试、换服务或停用整个插件。需要特权的具体入口可用 `ctx.plugin.require_owner(ctx.scene, ctx.message.sender.uid)`；不必给普通查询加主人门槛。

插件是同进程代码，共享宿主依赖环境，不能宣称沙箱隔离。不使用 `ctx.plugin.host` 穿透到内部运行时。接口扩展随版本发布；当前安装与生效方式见 [示例说明](examples/counter/README.md)。

## 本地测试与发布

宿主提供无需启动 OneBot、面板或 worker 的公共测试入口，使用实际生命周期、配置、入口匹配、工具校验和磁盘 KV，发送仅捕获为 simulated：

```python
from pathlib import Path
from len_bot.next.plugin_testing import PluginTest

async def check():
    async with PluginTest(Path("counter"), config={"step": 2}) as bot:
        assert await bot.message("计数加一")
        assert bot.deliveries[-1].text == "本群计数：2"
        result = await bot.tool("counter_read", {})
        assert '2' in result
```

`scenes` 和 `owners` 可在构造器指定；`message(text, scene=..., sender=...)` 模拟实际身份，返回是否被接管。`deliveries` 包含插件、场景、组合内容、reply_to 和 simulated 状态；`events()` 查看实际记录的插件事件。处理器或启动失败会使本次测试报错，退出上下文会停止插件并删除临时实例。它不提供模型、记忆或工作服务，这些调用明确报错；插件自行调用外部网络仍会执行，同进程测试不是沙箱。

独立仓库模板内容在 [plugin-template](plugin-template/README.md)，当前是待发布到组织的本地准备件。模板 CI 安装指定宿主源码版本再执行实际插件测试；标签发布工作流将清单、入口、README 和 LICENSE 打成可导入 ZIP。发布前填写自己的 name／authors／license，更新版本及兼容范围。插件许可证不因使用宿主接口而自动等于模板许可证。

[English](plugins-v1.en.md)
