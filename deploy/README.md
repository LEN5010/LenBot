# 部署

[English](README.en.md)

LenBot 有三种运行方式，跑的是同一个程序，读的也是同一种实例目录：

| 方式 | 适合 | 需要 | 说明 |
|---|---|---|---|
| 部署包 | Linux／macOS 日常使用 | uv | [部署包](package/README.md) |
| Docker | 服务器、NAS、Windows（WSL2） | Docker | [Docker 部署](current/docker.md) |
| 源码 | 开发、跟着主线走 | uv、Node.js 22 | 下文 |

不管用哪种方式，第一次启动 `len-bot` 都会打开本机网页向导：

1. 连接 OneBot，从平台读出 Bot 自己的账号；
2. 填主人的 QQ；
3. 填模型服务商并测试一次调用；
4. 建一个角色和第一个群，设面板账号。

保存后进入面板。建议先选模拟发送，在面板的对话测试里聊几句，确认没问题再改成真实发送。

## 实例目录

实例目录保存一个 Bot 的全部数据：

| 内容 | 位置 |
|---|---|
| 运行配置（含密钥） | `lenbot.config.json` |
| 聊天数据库、记忆处理库 | 配置里的 `database`，以及同名的 `.memory.sqlite3` |
| 角色包 | `personas/<角色ID>/` |
| 记忆、日志、插件数据、任务目录 | `state/` 等，按配置 |

`lenbot.config.json` 是唯一的运行配置，不读环境变量或命令行参数。运行中在面板里改，需要重启的会提示；手工改之前先停机。备份就是停机后备份整个实例目录，以及配置里指向实例外面的任务目录。

## 从源码运行

```sh
git clone https://github.com/lendevs/LenBot.git
cd LenBot
./scripts/install.sh
uv run --no-sync len-bot
```

仓库根目录就是实例目录。更新代码：

```sh
# 先停掉 Bot（Ctrl-C）
git pull
./scripts/install.sh
uv run --no-sync python -m len_bot.next.maintenance.migrate_config
uv run --no-sync python -m len_bot.next.maintenance.migrate
uv run --no-sync python -m len_bot.next.maintenance.migrate_memory_jobs
uv run --no-sync python -m len_bot.next.maintenance.migrate_local_memory
uv run --no-sync python -m len_bot.next.maintenance.plugin_dependencies
uv run --no-sync len-bot
```

五条维护命令依次是：升级根配置，升级业务数据库，升级记忆处理库，升级本地记忆索引，恢复已安装插件的依赖。已经是最新格式时什么也不做。本地索引从格式 2 升到 3 会清除旧派生摘要，正文和修改历史保留；之后明确整理时重新生成。升级前先备份实例。

在 macOS 上可以双击 [`current/start.command`](current/start.command) 启动。要做成系统服务，见[可选服务](current/README.md#作为系统服务运行)。

## 可选能力

只聊天的话，有 OneBot 和一个聊天模型就够了。下面这些按需添加，见[可选服务](current/README.md)：

- 向量记忆检索（Ollama 等 embeddings 服务）；
- 语音转写（本地 [ASR](current/asr.md)）；
- 后台任务（Docker 和任务镜像，可选[存储池](current/task-storage.md)）；
- 账号浏览（[浏览器配套组件](browser/README.md)）。

日常使用、角色、插件和任务的维护见[使用与维护](current/operations.md)。发布新版本的流程见[发行指南](releasing.md)。
