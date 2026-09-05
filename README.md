# len_bot

基于 Python 3.13、asyncio 和 SQLite WAL 的持久社会化 Agent。运行时保存事实、认识和工作状态；模型按需理解场景、使用工具并提出行动，RuntimeGate 决定是否提交。

## 当前状态

生产入口为单一 SocialCognitionCore，支持连续输入、通用网页读取、Runtime 信息工作、可修订记忆、图片资产与表情库和分群发送节奏。运营表达样例按固定顺序提供。

群聊效果通过真实群聊发现问题并迭代。面板保留配置、实际工作管理、事件与运行日志，已移除回放、人工评分、报告导入和实发准入门槛。当前提供商、模型和人格保持原配置；全局 Shadow 开启，初始实发群名单为 `group:126300994`。已有文字型号的图片探测失败记录保留，使用图片理解需要单独配置可用的视觉型号。能力与已知问题见 [实施记录](docs/implementation.md)。

## 运行与开发

```sh
uv sync
uv run len-bot
```

默认面板 `http://127.0.0.1:11307`，初始账号 `admin / lenbot123`。在面板配置提供商、模型、OneBot 和人格；配置持久化在工作目录的 `len_bot.db`。首次创建可从 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`ONEBOT_ACCESS_TOKEN` 播种，此后以保存值为准。

实发只由持久化实发群名单和全局 Shadow 控制：关闭 Shadow 后，机器人可以在名单内的群发送，名单外保持 Shadow。修改源码、模型或人格不会要求重新验收，也不会重置发送配置。开始实群聊天和暂停方式见 [运行手册](docs/operations.md)。

OneBot 支持主动连接或反向接入，默认反向地址 `ws://127.0.0.1:8080`；发送明确选择 WebSocket 或 HTTP，不确定发送不切换通道重试。

修改后只做相关检查。涉及前端时：

```sh
cd src/len_bot/web/frontend
npm ci
npm run build
```

前端构建到 `src/len_bot/web/static/dist`，由 FastAPI 提供。数据库升级前停机备份；Python 检查与开发原则见 [工程约束](AGENTS.md)。

## 文档

- [当前架构](docs/architecture.md)
- [实施记录](docs/implementation.md)
- [运行与实群迭代](docs/operations.md)
- [领域术语](CONTEXT.md)
- [开发约束](AGENTS.md)
- [架构决策索引](docs/adr/README.md)
- [嘉然人格来源](docs/persona/diana/README.md)
