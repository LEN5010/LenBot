# 部署

[English](README.en.md)

LenBot 有三种运行方式。三种方式运行的是同一个程序，使用的也是同一种实例目录。面向新用户的完整步骤见[文档站](https://lendevs.github.io/LenBot/)。

| 方式 | 适用场景 | 依赖 | 说明 |
|---|---|---|---|
| 部署包 | Linux、macOS、Windows 日常使用 | uv | [部署包](package/README.md) |
| Docker | 服务器、NAS，或需要后台任务的 Windows | Docker | [Docker 部署](current/docker.md) |
| 源码 | 开发，或使用主线上的最新代码 | uv、Node.js 22 | 下文 |

无论使用哪种方式，第一次启动时都会在终端打印一个本机网页向导的链接。向导分为六步。

1. 管理员账号和面板端口。
2. 连接 OneBot，读取 Bot 的 QQ 号。连接失败时无法进入下一步，因此请先启动 OneBot。
3. 主人的 QQ 号和时区。
4. 聊天使用的模型。测试通过后才能继续。
5. 角色（默认为小然），以及第一个群或私聊。
6. 官方插件。这一步可以跳过。

保存后 LenBot 直接启动，页面自动跳转到面板。第一次使用时，建议选择模拟发送，先在面板的对话测试中试聊，确认没有问题后再改为真实发送。每一步的说明见文档站的[首次配置](https://lendevs.github.io/LenBot/guide/first-setup)。

## 实例目录

实例目录保存一个 Bot 的全部数据。

| 内容 | 位置 |
|---|---|
| 运行配置（含密钥） | `lenbot.config.json` |
| 聊天数据库和记忆处理库 | 配置中的 `database`，以及同名的 `.memory.sqlite3` |
| 角色包 | `personas/<角色ID>/` |
| 记忆、日志、插件数据、任务目录 | `state/` 等，以配置为准 |

`lenbot.config.json` 是唯一的运行配置，LenBot 不读取环境变量或命令行参数。运行期间在面板中修改配置，需要重启的修改会有提示。手动编辑之前，请先停止 LenBot。

备份时，停止 LenBot 后备份整个实例目录，以及配置中指向实例以外的任务目录。

## 从源码运行

```sh
git clone https://github.com/lendevs/LenBot.git
cd LenBot
./scripts/install.sh
uv run --no-sync len-bot
```

从源码运行时，仓库根目录就是实例目录。

部署包和 Docker 自带更新器，可以在面板中升级，失败后可以恢复，见文档站的[更新与恢复](https://lendevs.github.io/LenBot/guide/update)。从源码运行时没有更新器，需要手动更新代码。

```sh
# 先停掉 Bot（Ctrl-C）
git pull
./scripts/install.sh
uv run --no-sync python -m len_bot.next.maintenance.migrate_config
uv run --no-sync python -m len_bot.next.maintenance.migrate
uv run --no-sync python -m len_bot.next.maintenance.migrate_memory_jobs
uv run --no-sync python -m len_bot.next.maintenance.migrate_local_memory
uv run --no-sync python -m len_bot.next.maintenance.plugin_dependencies
uv run --no-sync python -m len_bot.next.maintenance.doctor
uv run --no-sync len-bot
```

升级前请先备份实例。维护命令按以下顺序执行。

1. `migrate_config` 升级根配置。
2. `migrate` 升级业务数据库。
3. `migrate_memory_jobs` 升级记忆处理库。
4. `migrate_local_memory` 升级本地记忆索引。
5. `plugin_dependencies` 恢复已安装插件的依赖。
6. `doctor` 检查升级结果。每一项都是 `ok` 或 `disabled` 后再启动。

已经是最新格式的步骤不做任何改动。本地索引从格式 2 升级到 3 时，会清除旧的派生摘要，正文和修改历史保留。之后明确要求整理记忆时，摘要会重新生成。

在 macOS 上，可以双击 [`current/start.command`](current/start.command) 启动。作为系统服务运行的方法见[可选服务](current/README.md#作为系统服务运行)。

## 可选能力

只用于聊天时，有 OneBot 和一个聊天模型就足够了。以下能力按需添加，见[可选服务](current/README.md)。

- 向量记忆检索，使用 Ollama 等 embeddings 服务
- 语音转写，使用本地 [ASR](current/asr.md)
- 后台任务，需要 Docker 和任务镜像，可以另外配置[存储池](current/task-storage.md)
- 账号浏览，需要[浏览器配套组件](browser/README.md)

日常使用和维护见[使用与维护](current/operations.md)，其中包括角色和插件的维护，以及任务的维护。发布新版本的流程见[发行指南](releasing.md)。
