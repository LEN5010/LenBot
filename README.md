# len_bot

基于 Python 3.13、asyncio 和 SQLite WAL 的持久社会化 Agent。运行时保存事实、认识和工作状态；模型按需理解场景、使用工具并提出行动，RuntimeGate 决定是否提交。

## 当前状态

生产入口为单一 SocialCognitionCore，支持连续输入、按需检索、可修订记忆、明确发送回执、任务调度和控制面板。没有 FAST/FULL 双轨、样例轮换或风格重试流水线；运营表达样例按固定顺序提供。

当前确定性基线为 164 项通过，真实模型自然度与可靠纠错尚未完成验收。参见 [已有评估](docs/evaluation/20260906-adr0042-review.md)。新能力与验收状态见 [实施记录](docs/implementation.md)，不能将计划视为已上线。

## 运行与开发

```sh
uv sync
uv run len-bot
```

默认面板 `http://127.0.0.1:11307`，初始账号 `admin / lenbot123`。在面板配置提供商、模型、OneBot 和人格；配置持久化在工作目录的 `len_bot.db`。首次创建可从 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`ONEBOT_ACCESS_TOKEN` 播种，此后以保存值为准。

OneBot 支持主动连接或反向接入，默认反向地址 `ws://127.0.0.1:8080`；发送明确选择 WebSocket 或 HTTP，不确定发送不切换通道重试。

```sh
uv run pytest -q --tb=short
cd src/len_bot/web/frontend
npm ci
npm run build
```

前端构建到 `src/len_bot/web/static/dist`，由 FastAPI 提供。数据库升级前停机备份，不用旧代码打开不兼容的新数据库。

## 文档

- [当前架构](docs/architecture.md)
- [分阶段实施与验证](docs/implementation.md)
- [运行、验收和回退](docs/operations.md)
- [领域术语](CONTEXT.md)
- [开发约束](AGENTS.md)
- [架构决策索引](docs/adr/README.md)
- [嘉然人格来源](docs/persona/diana/README.md)
