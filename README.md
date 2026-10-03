# LenBot

基于 Python、asyncio 和 SQLite 的 QQ 群聊助手，通过 OneBot v11 接入。当前核心支持多场景会话、角色表达、双记忆后端、沙箱任务和管理面板；仍处于本地开发与联调阶段。

## 开始使用

普通使用选择 **Linux／macOS 成品部署包**：内含已构建面板的 wheel、锁定依赖清单和安装入口，只需 uv（Python 3.13 可由 uv 安装），不用 Node.js。取得当前构建产物并解压后：

```sh
./install.sh install "$HOME/lenbot"
"$HOME/lenbot/run"     # 首次向导保存后退出；再次执行才启动业务
```

安装后 `instance/` 保存根配置和业务数据，`releases/<版本>/` 保存程序与可写依赖环境。启动、停止、重启和停机升级见[成品包说明](deploy/package/README.md)。也可手工安装独立 wheel；Docker 用户可在新卷中[直接离线初始化](deploy/current/docker.md#直接初始化新卷)，不必先在主机装 wheel。Windows 本轮使用 WSL2／Docker。

当前产物由[打包命令](CONTRIBUTING.md#构建与提交)生成，尚未公开发布；下载链接以实际 Release 为准，不把本地构建当作已上传。修改源码时再安装 Node.js 22 并执行 `./scripts/install.sh`。

QQ 聊天还需一个 OneBot v11 服务；可先在面板试聊。Docker、OpenViking 和 ASR 均为可选能力。完整步骤和一个最小演示见[部署说明](deploy/current/README.md)。

`lenbot.config.json` 是唯一运行配置，包含凭据，不进 Git。运行中从面板保存，提示重启的修改由用户明确重启生效；手工修改前先停机。`delivery: "onebot"` 会真实发送到 QQ，`"simulated"` 仅模拟发送，模型仍可能计费。

## 文档

| 要做什么 | 入口 |
|---|---|
| 成品安装、原生服务与停机升级 | [平台包说明](deploy/package/README.md) |
| Docker、新卷初始化与任务挂载 | [Docker 部署](deploy/current/docker.md) |
| 可选服务、模型与 QQ 接入 | [部署说明](deploy/current/README.md) |
| 角色、任务资料、数据升级与记忆迁移 | [使用与离线维护](deploy/current/operations.md) |
| 本地语音转写／原生记忆分类 | [ASR](deploy/current/asr.md)／[记忆模板](deploy/current/memory-templates.md) |
| 源码职责、开发与打包 | [开发指南](CONTRIBUTING.md) |
| 工程约束／第三方来源 | [AGENTS.md](AGENTS.md)／[第三方材料](THIRD_PARTY_NOTICES.md) |

维护者的设计、计划和运行记录在本机 `docs/`，不随 Git 或发行包发布。

## 能力

| 范围 | 实现 |
|---|---|
| 聊天与角色 | 持久会话、注意力与消息合并、压缩恢复、角色表达／直出；设定、知识、样例、表情、头像、角色包和草稿试聊 |
| 记忆 | 本地 Markdown／全文及可选向量检索，OpenViking 原生远端后端；召回、可选后台整理、历史与正文迁移 |
| 任务 | 独立容器、Pi RPC、追问／确认、取消续接、选定资料、技能、文件登记与显式上传 |
| 工具 | 搜索、网页读取、看图、QQ 语音转写、公共浏览、独立账号浏览任务、插件与 MCP |
| 学习与管理 | 表达学习、自动黑话及人工纠正、可关闭的表情采集、回复效果、提醒与主动话题；权限、预算、费用及时间线 |

能力需要根配置、角色许可和真实服务同时就绪；未安装的服务不注册占位工具，个人插件不默认启用。

## 限制与验收

- 双记忆后端历史形式不同；OpenViking 完整遗忘需要[专门的服务扩展](deploy/current/memory-forget.md)，普通删除不清除旧快照。
- 任务支持实例级 ext4／APFS 存储池硬上限、离线入池、用量查看与文件清理。公共与账号浏览均支持任务文件上传／下载；账号文件传输使用[配套 BrowserSkill 扩展与文件助手](deploy/current/browserskill-files.md)。
- ASR 处理本场景 QQ 语音，不接受任意本地音视频，也不提供 TTS。
- 已从 wheel 在 macOS 全新虚拟环境完成试聊和任务文件演示；另在无 Node/npm 的全新 Linux ARM64 容器完成安装、首次配置、真实模型试聊、插件新依赖安装及模拟协议续聊。Linux 使用 CLI／HTTP 接口验证，不是完整桌面或 systemd 部署验证。真实 QQ 交付、浏览器扩展和语音质量仍需现场观察。
- 仅保留当前运行核心和显式离线历史格式工具，不再支持旧核心启动。

## 许可证

原创代码采用 [AGPL-3.0-only](LICENSE)，见 [NOTICE](NOTICE)。第三方依赖、独立服务与个人角色保持各自许可。源码包包含源码和部署材料，wheel 包含当前核心及已构建面板；[本地打包](CONTRIBUTING.md#构建与提交)不等于上传或正式发布。
