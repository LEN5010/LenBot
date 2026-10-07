# 开发者文档

| 文档 | 内容 |
|---|---|
| [架构](architecture.md) | 各部分做什么、数据怎么流动 |
| [上下文与模型输入](context.md) | 请求组装、预算、压缩和续接协议 |
| [角色包](personas.md) | 角色目录结构和写法 |
| [群聊表达](expression-materials.md) | 表达原则和提示词分工 |
| [插件接口 v1](plugins-v1.md)（[English](plugins-v1.en.md)） | 插件清单、装饰器、上下文、测试和发布 |
| [独立插件与示例](plugin-examples.md) | 群聊总结、GSUID 桥接与教学模板 |
| [插件目录](plugin-catalog.md) | 静态插件目录的格式 |

## 写插件

插件接口版本是 **interface = 1**，从 `len_bot.next.plugin` 导入。最快的开始方式是在 GitHub 上用[插件模板](https://github.com/lendevs/lenbot-plugin-template)生成仓库，或者复制[计数插件](examples/counter/)。

插件是和宿主同进程的可信 Python 代码，适合精确命令、规则接管、后台监测和专用工具。需要在容器里完成的长工作属于后台任务，插件可以通过委派交给它。

开发文档跟着代码版本一起发布，不依赖维护者本机的任何资料。参与宿主本身的开发见 [CONTRIBUTING](../CONTRIBUTING.md)。

工具作者可从 [模型说明契约](plugins-v1.md#工具说明如何交给聊天模型) 和 [工具回放用例](../examples/plugin-tools/README.md) 检查发现、参数、来源和实际发送含义。

工具按用户能力组织；同一能力的查询操作使用严格 action 分支。当前 0.2.0 仍未发行，本轮只合并开发改动。
