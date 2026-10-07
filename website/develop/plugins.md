# 写插件

插件是一个 Python 包：一份 `plugin.toml` 清单，加一个 `__init__.py`，里面恰好定义一个 `Plugin` 子类。插件和 LenBot 跑在同一个进程里，只从 `len_bot.plugin` 导入接口。

下面用一个「每群独立计数」的插件走一遍。想直接开工，在 GitHub 上用[插件模板](https://github.com/lendevs/lenbot-plugin-template)生成仓库，模板里就是这个插件的完整版，CI 和发布也配好了。

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

- `interface` 是插件接口的代次，现在是 1。同一代里只做兼容扩展。
- `requires_lenbot` 写首个提供你所需能力的 LenBot 版本。`0.2.0` 是首个公开版本，写 `>=0.2,<1` 即可。
- `reload = "plugin"` 表示可以单独重载；需要整个程序重启才能换版时写 `"host"`。
- `dependencies = ["包名>=版本"]` 声明 Python 依赖。
- `data_version` 是插件数据的格式编号，默认 1。改了数据的存法时提高它，并实现 `migrate_data`，见下面的[数据版本](#数据版本)。
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
| `@fullmatch` | 整条消息完全相等 |
| `@regex` | 正则完整匹配 |
| `@on_notice` | OneBot 通知事件，比如有人进群 |
| `@tool` | 注册成 Bot 可以调用的工具 |
| `@background` | 周期性后台工作 |

装好、在某个群启用后，群里发 `/计数` 或 `计数加一` 就会直接得到回复。命令、全文和正则匹配到时由插件直接处理，不唤醒聊天模型；`@tool` 则由模型在聊天中决定要不要调用。

## 配置表单

在清单里用 `[config.<字段名>]` 声明参数，面板据此生成表单，插件里从 `ctx.config` 读到已经校验过的值。每个字段必须写 `type` 和 `description`；写了 `default` 就是选填，否则必填。支持文字、密钥、数字、开关、列表、群选择、路径、网址和对象列表。

## 日志

`self.ctx.log` 是标准的 `logging.Logger`。写进去的记录进入 LenBot 的运行日志 `logs/lenbot.jsonl`，自动带上插件名和当前的群、一轮、工具调用 ID，面板日志页可以按插件筛选。处理器抛出的错误由 LenBot 记录，不需要自己再写一遍。

## 数据版本

插件数据放在 `ctx.data_dir`（KV 也在里面）。LenBot 在这个目录里记下数据是按哪个 `data_version` 写的。清单里的 `data_version` 比记录的高时，LenBot 在 `start()` 之前：

1. 把整个数据目录复制到 `.backups/<插件名>-v<旧版本>-<时间>`；
2. 调用 `await self.migrate_data(from_version)`，这时可以读写 `ctx.data_dir` 和 KV；
3. 成功就记下新版本再启动；出错就把数据恢复成迁移前的样子，插件标为失败，原错写进日志。

```python
class Notes(Plugin):
    async def migrate_data(self, from_version: int) -> None:
        if from_version < 2:
            old = self.ctx.data_dir / 'notes.txt'
            await self.ctx.set_kv('notes', old.read_text(encoding='utf-8').splitlines())
            old.unlink()
```

一次迁移要能从任何旧版本走到当前版本。清单的 `data_version` 比记录的低时 LenBot 拒绝启动：旧插件不读新数据。

## 测试与发布

`len_bot.plugin_testing` 的 `PluginTest` 不启动 LenBot 就能模拟消息、配置和工具调用，发出的消息记在 `deliveries` 里：

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

传入 `data=` 和 `data_version=` 可以测数据迁移。

[插件模板](https://github.com/lendevs/lenbot-plugin-template)的 CI 和发行直接调用 LenBot 提供的可复用工作流：

```yaml
jobs:
  test:
    uses: lendevs/LenBot/.github/workflows/plugin-ci.yml@master
```

默认用 LenBot `master` 跑插件测试和打包检查，需要固定版本时传 `host_ref`，测试要额外的系统包时传 `apt_packages`。打 `v*` 标签时，`plugin-release.yml` 核对标签和 `plugin.toml` 的版本一致，把插件打成可导入的 ZIP 挂到 Release，说明取 CHANGELOG 里这个版本的一节。

完整的接口说明见仓库里的[插件接口 v1](https://github.com/lendevs/LenBot/blob/master/developer/plugins-v1.md)。
