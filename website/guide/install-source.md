# 从源码运行

适合开发，或者想跟着主线走。需要 [uv](https://docs.astral.sh/uv/) 和 Node.js 22 或更新版本（用来构建面板）。

```sh
git clone https://github.com/lendevs/LenBot.git
cd LenBot
./scripts/install.sh      # 安装依赖并构建面板，不启动
uv run --no-sync len-bot  # 第一次会打印首次配置的链接
```

`install.sh` 只装依赖、构建面板，不启动程序，也不碰已有的配置和数据库。

源码目录本身就是实例目录：配置、数据库、角色和运行数据都放在仓库根目录，已经写进 `.gitignore`。

macOS 上可以双击 `deploy/current/start.command` 启动。

## 更新

面板不会改动开发目录，源码运行时要自己更新：

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

跳过 `migrate_config` 直接启动的话，LenBot 会停下并报出配置格式太旧，附上该执行的命令；启动时不会自动改写配置文件。

维护命令依次升级根配置、业务数据库、记忆处理库、本地记忆索引，再恢复插件依赖，最后用 `doctor` 检查一遍。已经是最新格式的步骤什么也不做。升级前先[备份](./backup)。

数据库迁移会在数据库旁留一份升级前的副本，比如 `state.db.v2.bak`，确认新版本正常后可以删掉；同名副本已经存在时命令拒绝执行，不会覆盖。每一步升级在一个事务里完成，中途失败就停在上一个完整格式。

`doctor` 逐项检查配置、数据库和插件，每项输出一行 JSON。全部是 `ok` 或 `disabled` 再启动。它只读数据，Bot 运行时也能跑。

## 作为系统服务

Linux 上可以用仓库里的 [systemd 模板](https://github.com/lendevs/LenBot/blob/master/deploy/current/lenbot.service)，按实际情况改用户、工作目录和可写路径：

```sh
sudo install -m 0644 deploy/current/lenbot.service /etc/systemd/system/lenbot.service
sudo systemctl daemon-reload
sudo systemctl start lenbot
sudo journalctl -u lenbot -n 100 --no-pager
```
