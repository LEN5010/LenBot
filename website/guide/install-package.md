# 部署包

部署包里有构建好的程序和面板，以及安装脚本，但不包含 Python。安装时由 [uv](https://docs.astral.sh/uv/getting-started/installation/) 准备 Python 3.13，再按包里锁定的版本安装依赖。因此安装过程需要联网，但不需要 Node.js。如果要通过 Git 安装插件，系统里还需要有 `git`。

::: warning 0.2.0 尚未发布
部署包会随 0.2.0 发布到 [GitHub Releases](https://github.com/lendevs/LenBot/releases)。在此之前请[从源码运行](./install-source)。
:::

| 系统 | 下载 |
|---|---|
| Linux（x86_64、ARM64） | `lenbot-<版本>-linux.tar.gz` |
| macOS（Intel、Apple 芯片） | `lenbot-<版本>-macos.tar.gz` |
| Windows x64 | `lenbot-<版本>-windows.zip` |

在 Windows 上直接运行时，除后台任务以外的功能都可以使用。后台任务不可用，因为任务容器需要 Docker 管理。Windows 上需要后台任务时，请改用 [Docker 安装](./install-docker)或 WSL2。

## 安装

下面以 0.2.0 为例。安装目标必须是一个尚不存在的目录。

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

第一次运行时，终端会输出首次配置的链接。按[首次配置](./first-setup)填写完成后即可进入面板。之后每次执行 `run` 都会直接启动，按 Ctrl-C 停止。

`run` 还会输出一行**更新与恢复**页的链接，请记下来。面板无法打开或升级失败时，从这个链接进入恢复页。

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

安装后不要移动这个目录，因为程序环境和服务配置中记录的是绝对路径。

## 作为系统服务运行

先在前台完成首次配置，按 Ctrl-C 停止，再注册服务。

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

### Linux

注册为当前用户的 systemd 服务 `lenbot.service`。日志用 `journalctl --user -u lenbot.service` 查看。在没有图形会话的服务器上，需要先执行 `loginctl enable-linger`，用户服务才会在登出后继续运行。

### macOS

注册为当前用户的 launchd 服务 `local.lenbot`。服务本身的输出写在 `logs/service.stdout.log`。LenBot 的运行日志写在实例目录的 `logs/lenbot.jsonl`，也可以在面板的日志页查看。也可以双击 `start.command` 启动，双击 `stop.command` 停止，双击 `restart.command` 重启。

### Windows

注册为登录时启动的计划任务 `LenBot`，在后台运行。执行 `stop` 时程序会正常关闭。

### 注意事项

服务崩溃后不会自动重新启动。程序异常退出时，请先查看日志。升级后不需要重新注册服务。

## 更新

在面板的设置中检查新版本，然后通过更新页升级，详见[更新与恢复](./update)。也可以下载新版本的部署包离线升级。

```sh
~/lenbot/service stop        # 或在终端里 Ctrl-C
./install.sh upgrade "$HOME/lenbot"   # 在新版本的包目录里执行
~/lenbot/service start
```
