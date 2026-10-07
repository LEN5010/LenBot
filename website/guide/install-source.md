# 从源码运行

这种方式适合开发，或者想使用主线上的最新代码。需要 [uv](https://docs.astral.sh/uv/)，以及 Node.js 22 或更新版本，Node.js 用于构建面板。

```sh
git clone https://github.com/lendevs/LenBot.git
cd LenBot
./scripts/install.sh      # 安装依赖并构建面板，不启动
uv run --no-sync len-bot  # 第一次会打印首次配置的链接
```

`install.sh` 只安装依赖和构建面板。它不启动程序，也不改动已有的配置和数据库。

从源码运行时，源码目录本身就是实例目录。运行时的配置和数据都放在仓库根目录，其中包括数据库和角色。这些文件已经写进 `.gitignore`。

在 macOS 上，也可以双击 `deploy/current/start.command` 启动。

## 更新

面板不会改动开发目录，从源码运行时需要手动更新。

```sh
# 先 Ctrl-C 停掉
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

LenBot 启动时不会自动改写配置文件。如果跳过 `migrate_config` 直接启动，LenBot 会停止运行，报告配置格式过旧，并给出需要执行的命令。

这几条维护命令按顺序执行以下工作。升级前请先[备份](./backup)。

1. `migrate_config` 升级根配置。
2. `migrate` 升级业务数据库。
3. `migrate_memory_jobs` 升级记忆处理库。
4. `migrate_local_memory` 升级本地记忆索引。
5. `plugin_dependencies` 恢复插件依赖。
6. `doctor` 检查升级结果。

已经是最新格式的步骤不做任何改动。

数据库迁移会在数据库旁边保留一份升级前的副本，例如 `state.db.v2.bak`。确认新版本运行正常后，可以删除这份副本。同名副本已经存在时，命令会拒绝执行，不会覆盖它。每一步升级都在一个事务中完成，中途失败时数据停留在上一个完整的格式。

`doctor` 会检查配置和数据库，也会检查插件，每一项输出一行 JSON。所有项目都是 `ok` 或 `disabled` 时再启动。`doctor` 只读取数据，Bot 运行期间也可以执行。

## 作为系统服务运行

Linux 上可以使用仓库里的 [systemd 模板](https://github.com/lendevs/LenBot/blob/master/deploy/current/lenbot.service)。使用前按实际情况修改其中的 `User` 和 `Group`，以及 `WorkingDirectory` 和 `ReadWritePaths` 里的路径。

```sh
sudo install -m 0644 deploy/current/lenbot.service /etc/systemd/system/lenbot.service
sudo systemctl daemon-reload
sudo systemctl start lenbot
sudo journalctl -u lenbot -n 100 --no-pager
```
