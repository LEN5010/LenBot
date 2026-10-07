# 日志与排查

Bot 没回、回错了、插件报错，先看日志。

## 在面板里看

面板 → 日志有两部分：

- **最近 20 次回复**：选一次回复，看 Bot 当时收到了什么、调用了哪些模型和工具、最后发了什么。点「这一轮的运行日志」，下面的运行日志只显示这一轮的记录。
- **运行日志**：LenBot 各部分写下的记录。可以只看错误或警告以上，只看当前群，也可以按插件名、任务编号筛选。

「日志文件」里按天列出日志文件，可以直接下载。

## 日志文件

运行日志写在实例目录的 `logs/lenbot.jsonl`，每天换一个文件，默认保留 14 天，目录可以在设置 → 上下文、媒体与日志里改。

每行是一条 JSON 记录，带时间、级别、事件名，以及能串起一件事的编号：

| 编号 | 用来串起 |
|---|---|
| `turn_id` | 一轮回复：收到消息、调用模型和工具、插件处理、发出消息 |
| `task_id` | 一个后台任务从创建到结束 |
| `scene` | 同一个群或私聊 |
| `plugin` | 同一个插件 |

出错的记录带完整的错误类型、信息和调用栈。配置里的密钥和插件的密钥字段不会写进日志；下载日志文件时还会遮去 5 位以上的数字，方便发给别人看。

## 其他日志

| 安装方式 | 位置 |
|---|---|
| 部署包 | 更新器的记录在安装目录的 `updates/updater.jsonl` 和 `updates/updater.log`；macOS 服务自身的输出在 `logs/service.stdout.log`、`logs/service.stderr.log`；Linux 服务用 `journalctl --user -u lenbot.service` 看 |
| Docker | 终端输出用 `docker compose -p lenbot logs lenbot`，更新器用 `docker compose -p lenbot logs lenbot-updater` |
| 从源码运行 | 终端里每条记录显示一行简短摘要，完整内容以日志文件为准 |

## 检查实例

数据格式或插件出了问题时，在实例目录执行检查命令：

| 安装方式 | 命令 |
|---|---|
| 部署包 | 在 `instance` 目录执行 `../releases/<当前版本>/.venv/bin/python -m len_bot.next.maintenance.doctor`（Windows 是 `..\releases\<当前版本>\.venv\Scripts\python.exe`） |
| Docker | `docker compose -p lenbot exec -w /srv/lenbot lenbot /opt/lenbot/.venv/bin/python -m len_bot.next.maintenance.doctor` |
| 从源码运行 | `uv run --no-sync python -m len_bot.next.maintenance.doctor` |

它逐项检查配置、数据库和插件（包括插件数据版本），每项输出一行，全部是 `ok` 或 `disabled` 就没问题。它只读数据，Bot 运行时也能执行。

## 报告问题

到 [GitHub Issues](https://github.com/lendevs/LenBot/issues) 提问时，附上：

- LenBot 版本和安装方式；
- 出问题那一轮或那个任务的日志（日志页下载，或者从 `lenbot.jsonl` 里按 `turn_id`、`task_id` 摘出来）；
- `doctor` 的输出。

发之前再看一眼，确认里面没有你不想公开的聊天内容。
