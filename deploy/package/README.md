# LenBot 部署包

这个包里有带面板的程序、安装脚本和更新器，不带 Python，也不带任何私人配置或角色。安装需要 [uv](https://docs.astral.sh/uv/getting-started/installation/) 和网络：uv 准备 Python 3.13，按包里锁定的 `requirements.txt` 安装依赖，不需要 Node.js。用 Git 安装插件时还需要系统里有 Git。

完整说明见文档站的[部署包](https://lendevs.github.io/LenBot/guide/install-package)和[更新与恢复](https://lendevs.github.io/LenBot/guide/update)。

Windows 原生运行支持聊天、面板、插件和记忆；后台任务需要 Docker 管理任务容器，要用的话请装 Docker 版或在 WSL2 里装 Linux 包。

## 安装

目标是一个还不存在的新目录。在解压出的包目录里执行：

```sh
# Linux／macOS
./install.sh install "$HOME/lenbot"
"$HOME/lenbot/run"
```

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File .\install.ps1 install "$HOME\lenbot"
& "$HOME\lenbot\run.cmd"
```

第一次运行没有配置，会打印首次配置的链接：连接 OneBot 读取 Bot 账号、填写主人、测试聊天模型，保存后直接启动并进入面板。`run` 还会打印一行**更新与恢复**的链接，面板打不开或升级失败时从这里进恢复页。终端里按 Ctrl-C 停止。

安装目录：

```text
lenbot/
  instance/            实例：根配置、角色、数据库、记忆、插件和日志
  releases/<版本>/     各版本的程序环境
  control/             更新器
  current.json         当前使用的版本
  run                  启动入口（Windows 是 run.cmd 和 run.ps1）
  service              注册和启停系统服务（Windows 是 service.ps1）
  backups/             升级前的快照
  updates/             更新状态和日志
  logs/                macOS 服务输出、离线升级日志
```

程序只读 `instance/lenbot.config.json`，不从环境变量或启动参数读业务配置。安装后不要搬动目录，程序环境和服务配置里记的是绝对路径。

## 系统服务

先在前台完成首次配置并停掉，再注册：

```sh
"$HOME/lenbot/service" install
"$HOME/lenbot/service" start
"$HOME/lenbot/service" status
"$HOME/lenbot/service" stop
```

```powershell
powershell -ExecutionPolicy Bypass -File "$HOME\lenbot\service.ps1" install   # 还有 start、stop、status、uninstall
```

- **Linux**：当前用户的 systemd 服务 `lenbot.service`，日志用 `journalctl --user -u lenbot.service`。没有图形会话的服务器先执行 `loginctl enable-linger`，让用户服务在登出后继续运行。
- **macOS**：当前用户的 launchd 服务 `local.lenbot`，日志在 `logs/host.log` 和 `logs/host.stderr.log`；也可以双击 `start.command`、`stop.command`、`restart.command`。
- **Windows**：登录时启动的计划任务 `LenBot`，后台运行；`stop` 通知更新器先停 LenBot 再退出。

服务不开机自启（Windows 是登录时启动）、崩溃后不自动拉起。停止时更新器会等正在进行的更新步骤结束，再停 LenBot。升级不需要重新注册服务。

## 升级

推荐在面板 → 设置 → 版本与更新里升级：先准备（LenBot 照常运行），再确认停机升级；更新器会先做完整快照，失败时在更新页恢复。

也可以用新版部署包离线升级。先停掉 LenBot，在**新版本**的包目录里执行：

```sh
./install.sh upgrade "$HOME/lenbot"          # Windows：.\install.ps1 upgrade "$HOME\lenbot"
```

离线升级和面板升级做同样的事：检查已启用插件的兼容性，给实例做快照，执行数据迁移，切换版本，同时换上新版更新器；完成后不启动，由你自己启动。快照在 `backups/`，之后可以在更新页恢复到升级前。上一次升级失败还没恢复时，离线升级会拒绝执行，先 `run` 打开更新页恢复。

迁移只往新格式走，旧程序读不了迁移后的数据；要回到旧版本只能恢复升级前的快照，恢复后升级之后产生的聊天记录和修改会丢失。

## 可选服务

向量记忆、语音转写、后台任务和账号浏览都是可选的，普通聊天不需要。配置见文档站的[可选服务](https://lendevs.github.io/LenBot/guide/optional-services)。
