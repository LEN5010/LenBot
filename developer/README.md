# 开发者文档

| 文档 | 内容 |
|---|---|
| [架构](architecture.md) | 各部分做什么、数据怎么流动 |
| [上下文与模型输入](context.md) | 请求组装、预算、压缩和续接协议 |
| [角色包](personas.md) | 角色目录结构和写法 |
| [群聊表达](expression-materials.md) | 表达原则和提示词分工 |
| [插件接口 v1](plugins-v1.md)（[English](plugins-v1.en.md)） | 插件清单、装饰器、上下文、测试和发布 |
| [独立插件与示例](plugin-examples.md) | 群聊总结、GSUID 桥接、A-SOUL、哔哩哔哩与教学模板 |
| [插件目录](plugin-catalog.md) | 静态插件目录的格式 |
| [模型协议](model-protocols.md) | 四种模型协议和用途绑定 |

## 写插件

插件接口版本是 **interface = 1**，从 `len_bot.plugin` 导入。最快的开始方式是在 GitHub 上用[插件模板](https://github.com/lendevs/lenbot-plugin-template)生成仓库。

插件是和宿主同进程的可信 Python 代码，适合精确命令、规则接管、后台监测和专用工具。需要在容器里完成的长工作属于后台任务，插件可以通过委派交给它。

写工具时，按用户能做的事来组织：同一类查询放进一个工具，用严格的 `action` 参数分支，查询和真正的发送、账号写入分开。模型看到的说明怎么拼出来，见[工具说明如何交给聊天模型](plugins-v1.md#工具说明如何交给聊天模型)；可以用[工具回放用例](../examples/plugin-tools/README.md)检查模型会不会找对工具、填对参数。

宿主怎么接模型、各用途怎么绑定，见[模型协议](model-protocols.md)。参与宿主本身的开发见 [CONTRIBUTING](../CONTRIBUTING.md)。
