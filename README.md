<img src="src/len_bot/web/frontend/src/assets/lenbot-mark-tile.svg" width="64" height="64" alt="LenBot">

# LenBot

[English](README.en.md) · [文档](https://lendevs.github.io/LenBot/) · [快速开始](https://lendevs.github.io/LenBot/guide/quick-start)

LenBot 是一个长期待在 QQ 群里的聊天机器人。它通过 OneBot v11 收发 QQ 消息，用你自己选的大模型思考和说话。

和它聊天不用敲命令。群友说话时，它自己判断这句话要不要接、接谁的话、要不要回个表情；有人让它写报告、查资料，它把活交给后台慢慢做，做完再把结果发回群里。角色、记忆、插件、模型和权限都在网页面板里管理。

<img src="website/public/screenshots/home.png" alt="面板首页" width="860">

## 能做什么

- **像群友一样聊天。** 每个群有一段一直延续的对话，聊久了会把早先的内容整理成回想。会引用、会 @ 人、会发表情，也可以设置成没被叫到时主动插话。
- **有自己的角色。** 设定、说话方式、底线、样例、资料和表情放在一个角色包里。默认角色是小然，装好就能用，也可以自己写。改完先在面板里试聊，满意再保存。
- **记得群里的事。** 记忆是本地的 Markdown 文件，每个群分开存。面板里能看、能改，也能让它彻底忘掉某件事。
- **能干耗时的活。** 写报告、整理资料交给 Docker 容器里的后台任务去做，聊天不用等。做的过程中可以追问、补充要求或者取消。
- **会用工具。** 搜网页、读网页、看图、转写语音、看合并转发、查群成员，也能接 MCP 服务。
- **能装插件。** 插件用 Python 写，可以加命令、定时任务和给模型用的工具。官方插件有群聊总结、GSUID Core 桥接、A-SOUL 和哔哩哔哩。
- **跟着群学。** 它会留意群里常用的说法、黑话和表情，学到的内容都能在面板里查看、修改或停用。

## 不做什么

- 目前只接 QQ。
- 自己不登录 QQ，需要另外运行一个 OneBot 实现，比如 [NapCat](https://github.com/NapNeko/NapCatQQ)。
- 不自带模型，要用你自己的模型服务和 API Key。支持 OpenAI 兼容接口、OpenAI Responses、Anthropic 和 Gemini。
- 能听懂语音，但只用文字回复。
- 提示词和面板只有中文。

## 快速开始

首个公开版本 0.2.0 还在准备，部署包和 Docker 镜像发布之前，先从源码运行。开始前准备好：

- [uv](https://docs.astral.sh/uv/) 和 Node.js 22
- 一个已经登录 QQ 的 OneBot 实现
- 模型服务的接口地址和 API Key

```sh
git clone https://github.com/lendevs/LenBot.git
cd LenBot
./scripts/install.sh
uv run --no-sync len-bot
```

第一次启动时，终端会打印一个链接：

```text
尚无根配置。请打开 http://127.0.0.1:52811/#token=...
```

在浏览器里打开它，跟着向导填完六步：管理员账号、连接 QQ、主人、模型、角色和第一个群、官方插件。保存后 LenBot 直接启动，页面会自动跳到面板。

「回复怎么发」第一次建议选模拟发送，回复只出现在面板里。到面板的对话测试页和它聊几句，觉得可以了，再去「设置 → 连接」把发送方式改成真实发送到 QQ。

详细步骤见文档里的[快速开始](https://lendevs.github.io/LenBot/guide/quick-start)。

## 安装方式

三种方式跑的是同一个程序，按你的情况选：

| 方式 | 适合 |
|---|---|
| [部署包](https://lendevs.github.io/LenBot/guide/install-package) | 在自己的电脑或 Linux 服务器上长期运行。只需要 uv，带服务启停脚本，在面板里升级，升级失败能恢复 |
| [Docker](https://lendevs.github.io/LenBot/guide/install-docker) | 服务器和 NAS；在 Windows 上想用后台任务也选它 |
| [源码](https://lendevs.github.io/LenBot/guide/install-source) | 想改代码，或者想跟着开发版走 |

0.2.0 发布后，部署包在 [GitHub Releases](https://github.com/lendevs/LenBot/releases)，镜像在 `ghcr.io/lendevs/lenbot`，Docker Hub 上有同名镜像。

## 文档

| 想做什么 | 去哪看 |
|---|---|
| 安装、首次配置、日常使用 | [文档站](https://lendevs.github.io/LenBot/) |
| 选部署方式、配可选服务 | [部署](deploy/README.md) |
| 维护角色、插件和任务 | [使用与维护](deploy/current/operations.md) |
| 写插件 | [插件开发](developer/README.md)、[插件模板](https://github.com/lendevs/lenbot-plugin-template) |
| 写角色包 | [角色包](developer/personas.md)、[小然的角色包](examples/personas/companion/) |
| 了解内部结构 | [架构](developer/architecture.md) |
| 参与开发 | [开发指南](CONTRIBUTING.md) |

版本说明的草稿在 [changelogs](changelogs/)。

## 许可证

原创代码采用 [AGPL-3.0-only](LICENSE)，见 [NOTICE](NOTICE)。第三方依赖和独立服务保持各自的许可，见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。
