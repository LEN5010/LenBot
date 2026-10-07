# 开发者文档

| 文档 | 内容 |
|---|---|
| [架构](architecture.md) | 各部分的职责和数据流向 |
| [上下文与模型输入](context.md) | 请求组装、预算、压缩和续接协议 |
| [角色包](personas.md) | 角色目录结构和写法 |
| [群聊表达](expression-materials.md) | 表达原则和提示词分工 |
| [插件接口 v1](plugins-v1.md)（[English](plugins-v1.en.md)） | 插件清单、装饰器、上下文、测试和发布 |
| [独立插件与示例](plugin-examples.md) | 群聊总结、GSUID 桥接、A-SOUL、哔哩哔哩与教学模板 |
| [插件目录](plugin-catalog.md) | 静态插件目录的格式 |
| [模型协议](model-protocols.md) | 四种模型协议和用途绑定 |

## 写插件

插件接口版本是 **interface = 1**，接口从 `len_bot.plugin` 导入。最快的开始方式是在 GitHub 上用[插件模板](https://github.com/lendevs/lenbot-plugin-template)生成仓库。

插件是与宿主运行在同一进程中的可信 Python 代码，适合实现以下功能。

- 精确的命令
- 按规则接管消息
- 后台监测
- 专用工具

需要在容器中完成的长时间工作属于后台任务，插件可以通过委派把工作交给后台任务。

编写工具时，按用户能完成的操作来组织。

- 同一类查询放进一个工具，用严格的 `action` 参数区分分支。
- 查询与真正的发送分开，与账号写入操作也分开。

模型看到的工具说明如何拼接，见[工具说明如何交给聊天模型](plugins-v1.md#工具说明如何交给聊天模型)。可以用[工具回放用例](../examples/plugin-tools/README.md)检查模型能否选对工具、填对参数。

宿主如何接入模型，以及各用途如何绑定模型，见[模型协议](model-protocols.md)。参与宿主本身的开发，见 [CONTRIBUTING](../CONTRIBUTING.md)。
