# 开发者文档

| 文档 | 内容 |
|---|---|
| [架构](architecture.md) | 各部分做什么、数据怎么流动 |
| [上下文与模型输入](context.md) | 请求组装、预算、压缩和续接协议 |
| [角色包](personas.md) | 角色目录结构和写法 |
| [表达材料](expression-materials.md) | 群聊表达原则的来源 |
| [插件接口 v1](plugins-v1.md)（[English](plugins-v1.en.md)） | 插件清单、装饰器、上下文、测试和发布 |
| [内置插件](builtin-plugins.md) | 随程序提供的四个插件 |
| [插件目录](plugin-catalog.md) | 静态插件目录的格式 |

## 写插件

插件接口版本是 **interface = 1**，从 `len_bot.next.plugin` 导入。最快的开始方式是在 GitHub 上用[插件模板](https://github.com/lendevs/lenbot-plugin-template)生成仓库，或者复制[计数插件](examples/counter/)。

插件是和宿主同进程的可信 Python 代码，适合精确命令、规则接管、后台监测和专用工具。需要在容器里完成的长工作属于后台任务，插件可以通过委派交给它。

开发文档跟着代码版本一起发布，不依赖维护者本机的任何资料。参与宿主本身的开发见 [CONTRIBUTING](../CONTRIBUTING.md)。
