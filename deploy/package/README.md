# LenBot 成品部署包

本包包含带面板的 wheel、安装入口和部署资料，不包含私人配置或角色。安装需要 **uv** 和网络；uv 准备 Python 3.13，并按包内从发行锁文件导出的 `requirements.txt` 安装 Python 依赖，不需要 Node/npm。插件 Git 安装另需系统 Git。Linux／macOS 使用各自标注的包；Windows 使用 WSL2 Linux 或 Docker，不提供原生 Windows 启动器。

## 新安装

解压后执行，目标是尚不存在的新目录（不要指定源码目录或旧实例）：

```sh
./install.sh install "$HOME/lenbot"
"$HOME/lenbot/run"
```

首次 `run` 没有配置时打开本机向导，保存后退出。已有配置时 `run` 会启动实际宿主，按根配置连接服务；安装命令本身不启动。先用模拟发送和面板试聊，再按实际 OneBot 环境选择真实发送。

安装目录：

```text
lenbot/
  instance/                  根配置、角色、数据库、插件与业务文件
  releases/<version>/.venv/  当前发行及可写插件依赖环境
  current -> releases/<version>
  run                        前台启动入口；Ctrl-C 停止
  service                    原生服务的显式操作入口
  logs/                      macOS 服务输出
```

`run` 总是在 `instance/` 工作，只读该处 `lenbot.config.json`。不是从环境变量或启动参数注入业务配置。不要在安装后搬动目录，虚拟环境与服务模板包含绝对路径。

## 原生服务

先在前台完成首次配置，退出前台进程后再注册：

```sh
"$HOME/lenbot/service" install
"$HOME/lenbot/service" start
"$HOME/lenbot/service" status
"$HOME/lenbot/service" stop
"$HOME/lenbot/service" restart
```

Linux 使用当前用户的 systemd 服务 `lenbot.service`；macOS 使用当前登录用户的 launchd 服务 `local.lenbot`。模板只注册，不开机自启、不自动拉起崩溃进程，不创建另一套 PID 追踪或守护程序。每个用户的这套快捷入口用于一个实例；多实例按[部署说明](../current/README.md)自行命名原生服务。运行与安装使用同一普通账号。

Linux 需要可用的用户 systemd 会话；无桌面服务器可采用[系统服务模板](../current/lenbot.service)，将工作目录改成实际 `instance/`、ExecStart 改成实际 `run`，写权限同时包含实际 `instance/` 和 `releases/`。该模板另有固定服务账号，安装环境必须由该账号读写。

macOS 另有 `start.command`／`stop.command`／`restart.command`，注册服务后可双击。`stop` 发 SIGTERM，请用 `status` 确认进程退出后再升级；安装器也使用实例锁拒绝仍在运行的实例。macOS 日志在 `logs/host.log` 和 `host.stderr.log`，Linux 用 `journalctl --user -u lenbot.service`。

## 升级

取得**另一版本**的部署包。先停止宿主与独立试聊，按[离线维护](../current/operations.md#升级与文件锁)备份实例、外置任务目录与记忆文件；旧程序目录不等于数据备份。

```sh
"$HOME/lenbot/service" stop
# 完成停机备份后，从新包目录执行：
./install.sh upgrade "$HOME/lenbot"
# 完成后仍保持停止，由你明确启动：
"$HOME/lenbot/service" start
```

升级安装新版本环境、执行当前业务库／记忆处理库离线迁移、恢复已配置插件依赖，成功后切换 `current`；根配置、人工角色和插件 Git ref 保持。独立任务镜像与账号浏览组件按本版组件说明单独升级。

尚未完成首次配置的实例只升级程序，不自动生成配置；已初始化但未首次启动、因而没有业务库时，迁移明确报告无库可迁移，不创建空库。已存在的坏库或不支持格式仍报原错并停止升级，不用空库替代。

原生服务调用固定的 `run`，程序升级无需再次注册。模板有变化时，Linux 再 `service install`；macOS 先 `launchctl bootout gui/$(id -u) /实际部署目录/local.lenbot.plist`，再 `service install`。这些操作不会代替业务配置保存。

安装失败立即报原错，不自动回退、改版本或继续启动。失败目录保留在 `releases/<version>`，修复前查看实际安装／迁移结果；清除仅本次未采用的程序目录后可重新明确执行。迁移可能已经完成部分步骤，旧版本不能直接当作可用回退版本。正常升级不删除旧程序或任何业务数据，同版本安装不覆盖已有版本目录。

## 可选能力

QQ、任务镜像、本地记忆、账号浏览和 ASR 的配置见 [deploy/current](../current/README.md)。普通聊天不要求全装，Docker 宿主另用 [Docker 配方](../current/docker.md)。本包生成不代表镜像已上传或版本已公开发布。

## 已核对范围

macOS 本机已从成品包完成全新安装、launchd 注册不启动、显式启停、服务重启和面板重启；独立合成实例经过停机备份、版本环境升级、插件恢复与 current 切换后可重新启动，配置、人工角色、停用插件源码和独立数据保持。该次数据库已是格式 36，未把无须转换的升级宣称为新迁移验证。

Linux ARM64 已完成非 root 新安装及模板生成，原生 systemd 启停和完整升级仍待核对。以上操作没有连接真实 QQ，也不证明全平台模型／记忆／浏览器功能通过。
