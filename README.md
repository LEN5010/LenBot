# LenBot

基于 Python、asyncio 和 SQLite 的群聊 Agent。接收原话与图片，决定接话或沉默；短查询直接调用工具，复杂研究、计算和资料整理交给后台工作。认识、提醒、技能和发送结果持续保存，可在管理面板查看与操作。

## 开始

从项目根目录安装依赖：

```sh
uv sync
```

按[运行手册](docs/operations.md)初始化实际根参数文件并确认当前数据库。取得当次生产启动授权后，从项目根目录执行：

```sh
uv run len-bot
```

面板地址、连接与模型由运行配置指定。完整初始化、停机、备份和故障处理步骤统一放在运行手册。

## 文档

- [产品行为](docs/product.md)：聊天、工作、资料与各状态的含义。
- [当前架构](docs/architecture.md)：正常链路、模块职责与必要术语。
- [运行手册](docs/operations.md)：配置、起停、备份与人工操作。
- [插件开发](docs/plugins.md)：目录描述、配置类型与公共工具注册。
- [工程约束](AGENTS.md)：修改方式与禁止事项。
- [当前任务](docs/iteration.md)：本轮未完成项与实际核对结果。
- [人格来源资料](docs/persona/diana/README.md)：运营提供的角色与素材数据。
