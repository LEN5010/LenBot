# len_bot

基于 Python 3.13、asyncio 和 SQLite WAL 的群聊 Agent。`SceneActor` 保存事实，单一对话 Agent 结合原话和图片决定接话、沉默与提案；后台工作处理查询和解题，所有表达经过 `RuntimeGate` 与真实发送回执。

## 当前能力

VNext 使用原生工具和 `finish_turn`，普通回复、沉默或已列出的表情可在一次模型调用中完成。文字、单图和混排共用表达链路，运营表情目录提供编号总览，原图直接进入模型请求。联网查询、计算、解题和资料整理交给独立后台工作，前台持续接收新输入。

持续状态包括事实 Session、有证据的认识账本、实际任务与送达状态。明确称呼和相处要求及时进入下一轮；其他认识按需读取。不再维护模型生成的持久话题树、自我兴趣或工作世界。

模型有 `conversation`、`work` 两条独立配置，反思使用 `work`。本次重构获准采用 BotCF / `gemini-3.8-flash`，对话推理强度 `low`、工作 `high`；这些是运营选择，不是源码默认值。实现与实际切换状态见 [实施记录](docs/implementation.md)，设计见 [ADR-0044](docs/adr/0044-native-conversation-and-evidence-ledger.md)。

## 运行与开发

在新库或已完成本次获准结构切换的环境中：

~~~sh
uv sync
uv run len-bot
~~~

默认面板为 `http://127.0.0.1:11307`，初始账号 `admin / lenbot123`。在面板配置提供商、`conversation/work` 模型、OneBot 和人格；配置保存在 `len_bot.db`。未配置模型也能接收事件和使用面板，不会自动创建提供商或从旧路由选择型号。`ONEBOT_ACCESS_TOKEN` 可作为首次 OneBot 配置来源，保存值优先。

默认 Shadow 开启，初始实发群名单为 `group:126300994`。实际运行使用运营保存值：关闭 Shadow 后，仅名单内群可实发。修改源码、模型或人格不会重置发送设置。操作、停机及备份步骤见 [运行手册](docs/operations.md)。

OneBot 支持主动连接或反向接入，默认反向地址 `ws://127.0.0.1:8080`；发送显式选择 WebSocket 或 HTTP，结果不确定时不换通道重试。

本次 VNext 替换旧会话结构，已获准停机备份后完整 Reset，不迁移旧自动认识。普通启动不会自动删除数据；回退需要对应的旧源码及同批数据库、媒体备份。

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
