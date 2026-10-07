# LenBot 部署包

这个包中有带面板的程序和安装脚本，以及更新器。包中不带 Python，也不包含任何私人配置或角色。

安装时需要 [uv](https://docs.astral.sh/uv/getting-started/installation/) 和网络连接。uv 会准备 Python 3.13，并按包中锁定的 `requirements.txt` 安装依赖，不需要 Node.js。通过 Git 安装插件时，系统中还需要有 Git。

完整说明见文档站的[部署包](https://lendevs.github.io/LenBot/guide/install-package)和[更新与恢复](https://lendevs.github.io/LenBot/guide/update)。

在 Windows 上直接运行时，聊天和面板可以使用，插件和记忆也可以使用。后台任务需要由 Docker 管理任务容器，需要后台任务时，请安装 Docker 版，或者在 WSL2 中安装 Linux 包。

## 安装

安装目标必须是一个尚不存在的新目录。在解压出的包目录中执行下面的命令。

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

第一次运行时还没有配置，终端会打印首次配置的链接。在向导中连接 OneBot 并读取 Bot 账号，填写主人，再测试聊天模型。保存后 LenBot 直接启动，页面进入面板。

`run` 还会打印一行**更新与恢复**页的链接。面板无法打开或升级失败时，从这个链接进入恢复页。在终端中按 Ctrl-C 停止。

安装目录的结构如下。

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

程序只读取 `instance/lenbot.config.json`，不从环境变量或启动参数读取业务配置。安装后请不要移动这个目录，因为程序环境和服务配置中记录的是绝对路径。

## 系统服务

先在前台完成首次配置并停止 LenBot，再注册服务。

```sh
"$HOME/lenbot/service" install
"$HOME/lenbot/service" start
"$HOME/lenbot/service" status
"$HOME/lenbot/service" stop
```

```powershell
powershell -ExecutionPolicy Bypass -File "$HOME\lenbot\service.ps1" install   # 还有 start、stop、status、uninstall
```

### Linux

注册为当前用户的 systemd 服务 `lenbot.service`，日志用 `journalctl --user -u lenbot.service` 查看。在没有图形会话的服务器上，需要先执行 `loginctl enable-linger`，用户服务才会在登出后继续运行。

### macOS

注册为当前用户的 launchd 服务 `local.lenbot`。服务本身的输出写在 `logs/service.stdout.log` 和 `logs/service.stderr.log`，宿主的运行日志写在实例目录的 `logs/lenbot.jsonl`。也可以双击 `start.command` 启动，双击 `stop.command` 停止，双击 `restart.command` 重启。

### Windows

注册为登录时启动的计划任务 `LenBot`，在后台运行。执行 `stop` 时，会通知更新器先停止 LenBot，然后退出。

### 注意事项

服务不会开机自动启动（Windows 上是登录时启动），崩溃后也不会自动重新启动。停止服务时，更新器会等待正在进行的更新步骤结束，再停止 LenBot。升级后不需要重新注册服务。

## 升级

推荐在面板的设置 → 版本与更新中升级。先准备更新，这一步期间 LenBot 照常运行，然后确认停机升级。更新器会先制作完整快照，升级失败时可以在更新页恢复。

也可以用新版本的部署包离线升级。先停止 LenBot，然后在**新版本**的包目录中执行下面的命令。

```sh
./install.sh upgrade "$HOME/lenbot"          # Windows：.\install.ps1 upgrade "$HOME\lenbot"
```

离线升级执行的步骤与面板升级相同。

1. 检查已启用插件的兼容性。
2. 为实例制作快照。
3. 执行数据迁移。
4. 切换版本，同时换上新版本的更新器。

完成后不会自动启动，需要手动启动 LenBot。快照存放在 `backups/` 中，之后可以在更新页恢复到升级前。如果上一次升级失败后还没有恢复，离线升级会拒绝执行。请先执行 `run`，打开更新页完成恢复。

迁移只能从旧格式升级到新格式，旧版本的程序无法读取迁移后的数据。要回到旧版本，只能恢复升级前的快照。恢复后，升级之后产生的聊天记录和修改都会丢失。

## 可选服务

以下服务都是可选的，普通聊天不需要。配置方法见文档站的[可选服务](https://lendevs.github.io/LenBot/guide/optional-services)。

- 向量记忆
- 语音转写
- 后台任务
- 账号浏览
