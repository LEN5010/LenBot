<img src="src/len_bot/web/frontend/src/assets/lenbot-mark-tile.svg" width="64" height="64" alt="LenBot">

# LenBot

[English](README.en.md) · [文档站](https://lendevs.github.io/LenBot/) · [快速开始](https://lendevs.github.io/LenBot/guide/quick-start)

LenBot 是一个住在 QQ 群里的聊天 Agent。它通过 OneBot v11 接入 QQ，用 Python、asyncio 和 SQLite 写成，自带网页管理面板。

<img src="website/public/screenshots/home.png" alt="面板首页" width="860">

## 设计思路

LenBot 以群聊场景为基本单位。每个群（或私聊）都有一份一直存在的 Agent 会话：

- 群友的消息、插件发来的通知、后台任务的进展，都进入同一个运行时，由这份会话统一处理。
- Agent 自己决定什么时候开口、说什么、发不发表情、要不要把一件长活交给后台任务。
- 宿主负责记清每件事的实际状态：任务做到哪一步，话有没有真的发出去，文件有没有真的送到。

精确命令和关键词规则仍由插件直接处理，不经过模型。但协调整个群聊的是 Agent，而不是某条命令触发的一个功能。

## 能做什么

| 范围 | 内容 |
|---|---|
| 群聊 | 每个场景一份持久会话；自己判断回不回、回谁；表情、引用、@；长对话自动压缩成回想 |
| 角色 | 角色包包含设定、说话方式、底线、样例、知识和表情，可导入导出，改动前先试聊 |
| 记忆 | 本地 Markdown，按群隔离；全文检索，可选向量检索；后台整理，可以修改、删除和彻底遗忘 |
| 后台任务 | 长活交给独立 Docker 容器里的 Pi，可以追问、取消、续接；产物登记后发到群里 |
| 工具 | 网页搜索与阅读、看图、语音转写、合并转发、群成员资料、MCP、浏览器任务 |
| 插件 | 同进程 Python 插件：命令、规则、定时、工具和技能；Git 和 ZIP 安装、版本检查、单插件重载 |
| 学习 | 从群聊里学说法、黑话和表情，观察群友对回复的反应；每一项都能在面板里采用、修改或停用 |
| 管理 | 面板管理模型、预算、权限、提醒和日志；主人也可以在群里用一句话改设置 |

群聊总结、GSUID Core 桥接、A-SOUL 和哔哩哔哩是[独立插件](developer/plugin-examples.md)，单独安装和更新。

## 快速开始

首个公开版本 0.2.0 还在准备，部署包和镜像还没有发布，现在请从源码运行。需要 [uv](https://docs.astral.sh/uv/) 和 Node.js 22：

```sh
git clone https://github.com/lendevs/LenBot.git
cd LenBot
./scripts/install.sh      # 安装依赖并构建面板，不启动
uv run --no-sync len-bot  # 第一次会打印首次配置的链接
```

打开链接，按向导连接 OneBot、读出 Bot 账号，填主人、模型、角色和第一个群，保存后进入面板。角色默认用小然，带现成的设定、说话方式、知识和表情，复制到实例后可以随便改。

刚开始建议选**模拟发送**：Bot 照常收消息、想回复，但不会真的发到 QQ，可以先在面板里试聊。模拟发送同样会调用模型、产生费用。

源码目录本身就是实例目录，配置、数据库、角色和运行数据都在仓库根目录，已经写进 `.gitignore`。`lenbot.config.json` 是唯一的运行配置，里面有密钥，不要提交。运行中在面板里改，要手工编辑先停机。

完整步骤见文档站的[快速开始](https://lendevs.github.io/LenBot/guide/quick-start)。

## 安装方式

三种方式运行的是同一个程序：

| 方式 | 适合 | 说明 |
|---|---|---|
| 部署包 | Linux、macOS、Windows 日常使用 | 只需要 uv；自带服务启停，在面板里升级，失败可以恢复。见[部署包](https://lendevs.github.io/LenBot/guide/install-package) |
| Docker | 服务器、NAS，或 Windows 上要用后台任务 | 实例数据在命名卷里，同样在面板里升级。见 [Docker](https://lendevs.github.io/LenBot/guide/install-docker) |
| 源码 | 开发、跟着主线走 | 见上文和[开发指南](CONTRIBUTING.md) |

0.2.0 发布后的下载位置：

- 部署包：[GitHub Releases](https://github.com/lendevs/LenBot/releases) 上的 `lenbot-<版本>-linux.tar.gz`、`-macos.tar.gz`、`-windows.zip`。
- 镜像：`ghcr.io/lendevs/lenbot`、`lenbot-updater`、`lenbot-worker`，Docker Hub 上是同名的 `docker.io/lendevs/...`，支持 amd64 和 arm64。

## 文档

| 想做什么 | 去哪看 |
|---|---|
| 安装、首次配置、日常使用 | [文档站](https://lendevs.github.io/LenBot/) |
| 选部署方式、可选服务 | [部署](deploy/README.md) |
| 角色、插件、任务的日常维护 | [使用与维护](deploy/current/operations.md) |
| 了解内部结构 | [架构](developer/architecture.md) |
| 写插件 | [插件开发](developer/README.md)、[插件模板](https://github.com/lendevs/lenbot-plugin-template) |
| 参与开发、构建发行包 | [开发指南](CONTRIBUTING.md) |
| 写角色包 | [角色包](developer/personas.md)、[示例角色](examples/personas/companion/) |

## 现状

0.2.0 是第一个公开版本，目前仍在准备中，版本说明草稿在 [changelogs](changelogs/)。只有 OneBot（QQ）一个平台适配器，提示词和面板只有中文，不做语音合成（TTS）。

## 许可证

原创代码采用 [AGPL-3.0-only](LICENSE)，见 [NOTICE](NOTICE)。第三方依赖和独立服务保持各自的许可，见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。
