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

当前行为、开发目标和执行进度分别维护；历史文档不作为新能力的启用依据。

| 要了解什么 | 入口 |
|---|---|
| 当前能做什么、有哪些限制 | [产品行为](docs/product.md) |
| 消息、资料、工作、提交与回执如何衔接 | [当前架构](docs/architecture.md) |
| 配置、启动、停机、备份与回退 | [运行手册](docs/operations.md) |
| Python、浏览器及 Core 的当前边界 | [执行边界](docs/execution-boundaries.md)、[Core 适配](docs/gscore-adapter.md) |
| 如何开发现有插件 | [插件开发](docs/plugins.md) |
| 工程约束 | [AGENTS.md](AGENTS.md) |
| 社会 Agent 后续目标与提交依赖 | [完整实施计划](docs/LenBot_社会Agent_完整实施计划_7a4152d.md) |
| 审查发现的问题与修复合同 | [C01—C10 fix](docs/social-agent-c01-c10-fix.md) |
| 本轮状态、下一步和实际核对结果 | [当前任务](docs/iteration.md) |
| 历史计划、阶段记录和运维证据 | [历史文档索引](docs/archive/README.md) |
| 人格与素材的原始资料 | [人格来源资料](docs/persona/diana/README.md) |
