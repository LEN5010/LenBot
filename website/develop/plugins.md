# 写插件

插件是一个 Python 包：一个 `plugin.toml` 清单，一个 `__init__.py`，里面恰好定义一个 `Plugin` 子类。插件和 LenBot 在同一个进程里运行，只从 `len_bot.plugin` 导入接口。

最快的开始方式是在 GitHub 上用[插件模板](https://github.com/lendevs/lenbot-plugin-template)生成仓库。

## 清单

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

- `interface` 是插件接口的代次，现在是 1。同一代里只做兼容扩展。
- `requires_lenbot` 写首个提供你所需能力的 LenBot 版本。`0.2.0` 是首个公开版本，写 `>=0.2,<1` 即可。
- `reload = "plugin"` 表示可以单独重载；需要整个程序重启才能换版时写 `"host"`。
- `dependencies = ["包名>=版本"]` 声明 Python 依赖。

## 入口

```python
from len_bot.plugin import Invocation, Plugin, command, fullmatch


class Counter(Plugin):
    @command('计数', '查看本群计数，不调用模型')
    async def count(self, ctx: Invocation, args: str) -> None:
        await ctx.reply(f"{ctx.config['label']}：{await ctx.get_kv(ctx.scene, 0)}")

    @fullmatch('计数加一', '按配置步长增加本群计数，不调用模型')
    async def increment(self, ctx: Invocation) -> None:
        value = await ctx.get_kv(ctx.scene, 0) + ctx.config['step']
        await ctx.set_kv(ctx.scene, value)
        await ctx.reply(f"{ctx.config['label']}：{value}")
```

| 装饰器 | 触发方式 |
|---|---|
| `@command` | `/命令 参数` |
| `@fullmatch` | 整条消息完全相等 |
| `@regex` | 正则完整匹配 |
| `@on_notice` | OneBot 通知事件，比如有人进群 |
| `@tool` | 注册成 Bot 可以调用的工具 |
| `@background` | 周期性后台工作 |

命令、全文和正则匹配到时由插件直接处理，不唤醒聊天模型。`@tool` 由模型在聊天中决定调用。

## 配置表单

在清单里用 `[config.<字段名>]` 声明参数，面板会生成表单，插件里从 `ctx.config` 读到已经校验过的值。支持文字、密钥、数字、开关、列表、群选择、路径、网址和对象列表。

## 测试与发布

`len_bot.plugin_testing` 提供本地测试工具，模板仓库里带好了测试和发布工作流：打 `v*` 标签时把插件打成可导入的 ZIP 挂到 Release。

完整的接口说明见仓库里的[插件接口 v1](https://github.com/lendevs/LenBot/blob/master/developer/plugins-v1.md)。
