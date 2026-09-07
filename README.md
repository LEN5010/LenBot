# len_bot

基于 Python 3.13、asyncio 和 SQLite WAL 的群聊 Agent。`SceneActor` 保存事实，单一对话 Agent 结合原话和图片决定接话、沉默与提案；后台工作处理查询和解题，所有表达经过 `RuntimeGate` 与真实发送回执。

## 当前能力

对话使用原生工具和 `finish_turn`。普通旁听只保存，有呼唤、连续互动、工作事件或一次观察机会时，才进入同一个社会判断入口；关键词和抽样不决定发言。文字、单图和混排共用 Actor/Gate 与送达链。

持续状态包括事实 Session、待处理唤醒、有证据的认识、带原文范围的历史摘要、实际任务与工作预算。工作保存进度、完整工具检查点与固定模型绑定，可在原预算内压缩和显式恢复；方法技能按需读取，并依据工作结果与原话纠正形成新版本。

`conversation`、`work`、`maintenance` 三类模型分别显式配置。维护未配置时不会继承其他型号。每个模型请求保存用途、原始 usage、独立估算和失败/取消/未确认状态；没有真实价格资料时费用保持未核实。实现与实际验证状态见[实施记录](docs/implementation.md)，契约见[ADR-0045](docs/adr/0045-attention-maintenance-and-durable-work.md)。

## 运行与开发

在新库或已完成本次获准结构切换的环境中：

~~~sh
uv sync
uv run len-bot
~~~

默认面板为 `http://127.0.0.1:11307`，初始账号 `admin / lenbot123`。在面板配置提供商、`conversation/work/maintenance` 模型、OneBot 和人格；配置保存在 `len_bot.db`。未配置模型也能接收事件和使用面板，不会自动创建提供商或从旧路由选择型号。`ONEBOT_ACCESS_TOKEN` 可作为首次 OneBot 配置来源，保存值优先。

默认 Shadow 开启，初始实发群名单为 `group:126300994`。实际运行使用运营保存值：关闭 Shadow 后，仅名单内群可实发。修改源码、模型或人格不会重置发送设置。操作、停机及备份步骤见 [运行手册](docs/operations.md)。

OneBot 支持主动连接或反向接入，默认反向地址 `ws://127.0.0.1:8080`；发送显式选择 WebSocket 或 HTTP，结果不确定时不换通道重试。

本次结构升级必须停机备份并运行显式迁移命令，保留旧原话、认识、任务、工作与素材，不能用 Reset 代替。旧库直接启动会被拒绝；步骤见运行手册。回退需要对应源码及同批数据库、媒体备份。

涉及前端修改时：

~~~sh
cd src/len_bot/web/frontend
npm ci
npm run build
~~~

前端构建到 `src/len_bot/web/static/dist`，由 FastAPI 提供。仅运行与改动相关的检查，检查实际页面与 Markdown 链接。开发原则见 [工程约束](AGENTS.md)。

## 文档

- [当前架构](docs/architecture.md)
- [实施记录](docs/implementation.md)
- [运行与实群迭代](docs/operations.md)
- [领域术语](CONTEXT.md)
- [开发约束](AGENTS.md)
- [架构决策索引](docs/adr/README.md)
- [嘉然人格来源](docs/persona/diana/README.md)
- [2026-09-06 实群失败审计](docs/research/20260906-live-run-audit.md)
