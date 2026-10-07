# 写插件

插件是一个 Python 包，由清单 `plugin.toml` 和 `__init__.py` 组成。`__init__.py` 中必须恰好定义一个 `Plugin` 子类。插件和 LenBot 运行在同一个进程中，只从 `len_bot.plugin` 导入接口。

本页以一个每群独立计数的插件为例，说明编写插件的完整过程。也可以直接在 GitHub 上用[插件模板](https://github.com/lendevs/lenbot-plugin-template)生成仓库。模板中就是这个插件的完整版本，CI 和发布流程也已经配置好。

```text
counter/
  plugin.toml
  __init__.py
```

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

[config.label]
type = "string"
description = "计数的显示名称"
default = "本群计数"

[config.step]
type = "integer"
description = "发送「计数加一」时增加的数值"
minimum = 1
default = 1
```

- `interface` 是插件接口的代次，目前是 1。同一代次内只做兼容的扩展。
- `requires_lenbot` 填写首个提供插件所需功能的 LenBot 版本。`0.2.0` 是首个公开版本，填写 `>=0.2,<1` 即可。
- `reload = "plugin"` 表示插件可以单独重新加载。需要重启整个程序才能更换版本时，填写 `"host"`。
- `dependencies = ["包名>=版本"]` 声明 Python 依赖。
- `data_version` 是插件数据的格式编号，默认为 1。修改数据的存储方式时，需要提高这个编号，并实现 `migrate_data`，见下文的[数据版本](#数据版本)。
- `[config.*]` 声明插件参数，见[配置表单](#配置表单)。

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
| `@fullmatch` | 整条消息与文本完全相同 |
| `@regex` | 整条消息完整匹配正则表达式 |
| `@on_notice` | OneBot 通知事件，例如有人进群 |
| `@tool` | 注册为 Bot 可以调用的工具 |
| `@background` | 周期性后台工作 |

插件安装并在某个群启用后，在群里发送 `/计数` 或 `计数加一` 就会直接得到回复。命令和全文匹配由插件直接处理，正则匹配也一样，都不调用聊天模型。`@tool` 注册的工具由模型在聊天中决定是否调用。

## 配置表单

在清单中用 `[config.<字段名>]` 声明参数，面板根据声明生成表单。插件从 `ctx.config` 读取已经校验过的值。每个字段都必须填写 `type` 和 `description`。填写了 `default` 的字段为选填，否则为必填。

支持的字段类型如下。

- 文字和密钥
- 数字和开关
- 列表和对象列表
- 群选择
- 路径和网址

## 启动和停止

需要准备资源时，重写 `async def start(self)`。清理工作放在 `async def stop(self)` 中。

- `start()` 限时 60 秒。超时后会被取消，插件被标记为失败，LenBot 的其他部分照常启动。
- `stop()` 限时 30 秒。超时后同样会被取消，不会阻塞关闭过程。

等待网络或预热缓存等耗时操作，请用 `self.ctx.start_task(name, coroutine)` 放到后台执行。

## 日志

`self.ctx.log` 是标准的 `logging.Logger`。写入的记录会进入 LenBot 的运行日志 `logs/lenbot.jsonl`，并自动带上插件名和当前的群，以及当前回复轮次和工具调用 ID。面板的日志页可以按插件筛选。处理器抛出的错误由 LenBot 记录，插件不需要另外记录。

## 数据版本

插件数据存放在 `ctx.data_dir` 中，KV 存储也在这个目录里。LenBot 会在这个目录中记录数据写入时使用的 `data_version`。清单中的 `data_version` 高于记录值时，LenBot 在调用 `start()` 之前执行以下步骤。

1. 把整个数据目录复制到 `.backups/<插件名>-v<旧版本>-<时间>`。
2. 调用 `await self.migrate_data(from_version)`。此时可以读写 `ctx.data_dir` 和 KV。
3. 迁移成功时，记录新版本，然后启动插件。迁移出错时，把数据恢复到迁移前的状态，将插件标记为失败，并把原始错误写入日志。

```python
class Notes(Plugin):
    async def migrate_data(self, from_version: int) -> None:
        if from_version < 2:
            old = self.ctx.data_dir / 'notes.txt'
            await self.ctx.set_kv('notes', old.read_text(encoding='utf-8').splitlines())
            old.unlink()
```

一次迁移必须能从任何旧版本升级到当前版本。清单中的 `data_version` 低于记录值时，LenBot 会拒绝启动插件，因为旧版本插件不读取新格式的数据。

## 测试与发布

`len_bot.plugin_testing` 中的 `PluginTest` 无需启动 LenBot，就可以模拟消息和配置，也可以模拟工具调用。插件发出的消息记录在 `deliveries` 中。

```python
from pathlib import Path

import pytest

from len_bot.plugin_testing import PluginTest


@pytest.mark.asyncio
async def test_increment():
    async with PluginTest(Path(__file__).parents[1], config={'step': 2}) as bot:
        await bot.message('计数加一')
        assert bot.deliveries[-1].text == '本群计数：2'
```

传入 `data=` 和 `data_version=` 可以测试数据迁移。

[插件模板](https://github.com/lendevs/lenbot-plugin-template)的 CI 和发布流程直接调用 LenBot 提供的可复用工作流。

```yaml
jobs:
  test:
    uses: lendevs/LenBot/.github/workflows/plugin-ci.yml@master
```

工作流默认使用 LenBot 的 `master` 运行插件测试和打包检查。

- 需要固定 LenBot 版本时，传入 `host_ref`。
- 测试需要额外的系统软件包时，传入 `apt_packages`。

推送 `v*` 标签时，`plugin-release.yml` 会核对标签与 `plugin.toml` 中的版本是否一致，然后把插件打包成可导入的 ZIP，附加到 Release 上。Release 的说明取自 CHANGELOG 中对应版本的一节。

完整的接口说明见仓库里的[插件接口 v1](https://github.com/lendevs/LenBot/blob/master/developer/plugins-v1.md)。
