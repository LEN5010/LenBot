# 部署包

部署包装的是带面板的程序和安装脚本，不带 Python。安装时 [uv](https://docs.astral.sh/uv/getting-started/installation/) 会准备 Python 3.13，并按包里锁定的依赖清单安装，所以需要网络，不需要 Node.js。用 Git 安装插件时还需要系统里有 Git。

::: warning 还没有发布
部署包会随 0.2.0 发布到 [GitHub Releases](https://github.com/lendevs/LenBot/releases)，在那之前请[从源码运行](./install-source)。
:::

| 系统 | 下载 |
|---|---|
| Linux（x86_64、ARM64） | `lenbot-<版本>-linux.tar.gz` |
| macOS（Intel、Apple 芯片） | `lenbot-<版本>-macos.tar.gz` |
| Windows x64 | `lenbot-<版本>-windows.zip` |

Windows 原生运行支持聊天、面板、插件和记忆。后台任务需要 Docker 管理任务容器，Windows 上请用 [Docker 安装](./install-docker)或 WSL2。

## 安装

目标目录必须是一个还不存在的新目录。

::: code-group

```sh [Linux／macOS]
tar -xzf lenbot-0.2.0-linux.tar.gz   # macOS 换成 macos 包
cd lenbot-0.2.0-linux
./install.sh install "$HOME/lenbot"
"$HOME/lenbot/run"
```

```powershell [Windows]
Expand-Archive lenbot-0.2.0-windows.zip -DestinationPath .
cd lenbot-0.2.0-windows
powershell -ExecutionPolicy Bypass -File .\install.ps1 install "$HOME\lenbot"
& "$HOME\lenbot\run.cmd"
```

:::

第一次运行没有配置，会打印向导链接，按[首次配置](./first-setup)填完就进入面板。之后每次 `run` 都直接启动。在终端里按 Ctrl-C 停止。

`run` 还会打印一行**更新与恢复**的链接。面板打不开、升级失败时，从这个链接进入恢复页。

## 安装目录

```text
lenbot/
  instance/            实例：配置、角色、数据库、记忆、插件和日志
  releases/<版本>/     各版本的程序环境
  control/             更新器
  run                  启动入口（Windows 是 run.cmd）
  service              注册和启停系统服务（Windows 是 service.ps1）
  backups/             升级前的快照
  updates/             更新状态和日志
  logs/                macOS 服务输出
```

安装后不要搬动目录，程序环境和服务配置里记的是绝对路径。

## 作为系统服务

先在前台完成首次配置，Ctrl-C 停掉，再注册服务：

::: code-group

```sh [Linux／macOS]
~/lenbot/service install
~/lenbot/service start
~/lenbot/service status
~/lenbot/service stop
```

```powershell [Windows]
powershell -ExecutionPolicy Bypass -File "$HOME\lenbot\service.ps1" install
powershell -ExecutionPolicy Bypass -File "$HOME\lenbot\service.ps1" start
powershell -ExecutionPolicy Bypass -File "$HOME\lenbot\service.ps1" stop
```

:::

- **Linux** 注册为当前用户的 systemd 服务 `lenbot.service`。日志用 `journalctl --user -u lenbot.service` 看。没有图形会话的服务器，需要先 `loginctl enable-linger` 让用户服务在登出后继续运行。
- **macOS** 注册为当前用户的 launchd 服务 `local.lenbot`，日志在 `logs/host.log`。还可以双击 `start.command`、`stop.command`、`restart.command`。
- **Windows** 注册为登录时启动的计划任务 `LenBot`，在后台运行；`stop` 会让程序正常关闭。

服务不会在崩溃后自动拉起，异常退出时先看日志找原因。升级不需要重新注册服务。

## 更新

在面板的设置里检查新版本，经更新页升级，见[更新与恢复](./update)。也可以下载新版部署包离线升级：

```sh
~/lenbot/service stop        # 或在终端里 Ctrl-C
./install.sh upgrade "$HOME/lenbot"   # 在新版本的包目录里执行
~/lenbot/service start
```
