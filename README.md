# LenBot

基于 Python、asyncio 和 SQLite 的 QQ 群聊 Agent，通过 OneBot v11 接入（NapCat、Lagrange、SnowLuma 等）。它在群里参与聊天、判断什么时候说话；短查询直接调用工具，复杂的研究、计算和资料整理交给后台工作；提醒、认识和发送结果都会保存，可以在管理面板查看和操作。

## 项目状态

核心正在重写，目标是：每个群一个常驻的 Agent 会话、用角色口吻说话的表达器、可以浏览和编辑的文件式记忆、在沙箱里完成长任务的工作 Agent。现有核心已冻结，只修阻断运行的缺陷。开源发布前会补齐安装和使用文档。

## 运行

需要 Python 3.13、uv、Node 22，以及一个 OneBot v11 实现。

```sh
# 项目根目录：安装依赖并构建管理面板（面板产物不进 Git）
uv sync --locked --no-dev
(cd src/len_bot/web/frontend && npm ci && npm run build)

# 仅在还没有配置文件时复制样例，然后填写连接、Bot QQ、模型和群
cp lenbot.config.example.json lenbot.config.json

uv run --no-sync len-bot
```

- `lenbot.config.json` 是唯一的运行配置：连接、账号、模型、群和插件都在里面，运行中由面板保存，不用环境变量、命令行或数据库覆盖。它含密钥，只留在本机，已排除在 Git 之外。
- 配置不合法时启动会指出具体字段并拒绝启动。
- 样例默认开启 Shadow（只生成不发送），群列表和模型为空，不能直接收发群消息。
- 面板地址和初始登录账号来自配置文件。
- Linux 容器部署见 [deploy/linux](deploy/linux/README.md)。

## 现在能做什么

- 在启用的群里聊天：文字、真实 @、图片、语音和混排消息，也可以选择不说话。
- 插件命令：日程、群报告、动态查询、开播订阅与提醒，按群启用。
- 后台工作：分步检索、计算、归纳，产出带来源的结果；可以查看进度、继续、修改和取消。
- 提醒：分别记录提出者、被提醒的人、原话和触发时间。
- 管理面板：查看消息、认识、工作、模型调用、发送结果和额度；保存插件参数与群设置。

Python 执行与浏览器容器还不是经过验收的生产隔离，默认关闭。

## 参与开发

工程规则见 [AGENTS.md](AGENTS.md)，开发流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题报告见 [SECURITY.md](SECURITY.md)。
