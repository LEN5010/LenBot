# Docker 部署

本文面向在 Docker 中运行 LenBot 的维护者。入门步骤见文档站的 [Docker 安装](https://lendevs.github.io/LenBot/guide/install-docker)。本文补充以下内容。

- Compose 配置的结构
- 离线完成首次配置的方法
- 后台任务的挂载
- 升级细节

| 镜像 | 作用 |
|---|---|
| `ghcr.io/lendevs/lenbot` | 宿主镜像，包含程序和面板，以及 Git、SSH、uv 和 `docker` 客户端。镜像中不运行 Docker daemon |
| `ghcr.io/lendevs/lenbot-updater` | 更新器，负责生成 Compose 配置和切换版本，并在升级失败时恢复 |
| `ghcr.io/lendevs/lenbot-worker` | 后台任务容器，启用任务时才需要 |

Docker Hub 上的镜像名为同名的 `docker.io/lendevs/...`。三种镜像都提供 Linux amd64 和 arm64 版本。所有业务参数只来自实例中的 `lenbot.config.json`，不能通过环境变量修改模型和场景，也不能修改任务参数。

## 初始化部署目录

部署目录存放 Compose 配置和更新器的状态，实例数据存放在命名卷中。用更新器镜像初始化一个新目录。这一步只创建文件和卷，不启动服务。

```sh
mkdir -p ~/lenbot && cd ~/lenbot
docker run --rm \
  --mount type=bind,source="$PWD",target=/deployment \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  --entrypoint python ghcr.io/lendevs/lenbot-updater:0.2.0 \
  /opt/lenbot-updater/init.py --version 0.2.0
```

| 选项 | 默认值 | 说明 |
|---|---|---|
| `--registry` | `ghcr` | 改为 `dockerhub` 时从 Docker Hub 拉取镜像 |
| `--project` | `lenbot` | 容器、卷和 Compose 项目名的前缀。一台机器运行多个实例时，每个实例使用不同的前缀 |
| `--panel-port` | 11307 | 面板发布到本机回环地址的端口 |
| `--update-port` | 11308 | 更新页发布到本机回环地址的端口 |
| `--host-directory` | 自动读取 | Docker daemon 看到的部署目录绝对路径，通常不需要填写 |

初始化后生成以下文件。

| 文件 | 内容 |
|---|---|
| `host.compose.yaml` | 宿主容器的配置，包括工作目录 `/srv/lenbot`、运行身份 `10000:10000`、实例卷和程序环境卷 |
| `host.version.compose.yaml` | 当前使用的镜像和程序环境卷。切换版本时由更新器改写，请不要手动修改 |
| `host.updater.compose.yaml` | 更新器容器，挂载部署目录和 Docker socket |
| `deployment.json`、`current.json` | 更新器读取的部署信息和当前版本 |
| `backups/` | 升级前的快照 |

同时会创建两个卷。`<项目>-data` 存放实例，`<项目>-python-<版本>` 存放这个版本的程序环境。新卷会从镜像中填充程序，并设置目录的属主。插件依赖安装在程序环境卷中，重建容器不会丢失。

后面的命令都使用这一组配置文件，因此先设置一个别名。

```sh
alias lb='docker compose -p lenbot -f host.compose.yaml -f host.version.compose.yaml -f host.updater.compose.yaml'
lb up -d
lb logs lenbot        # 首次配置链接、运行日志
lb stop
```

## 首次配置

默认方式是网页向导。第一次执行 `up -d` 后，宿主日志中会出现首次配置链接 `http://127.0.0.1:11307/#token=...`。在这台机器上打开链接并填写，保存后 LenBot 直接启动，页面进入面板。

也可以不打开网页，离线生成配置。

1. 把包中的 `deploy/current/first-setup.example.json` 复制为 `first-setup.json`，并限制文件权限。
2. 填写以下实际的值。
   - QQ 号和群号
   - 模型地址和模型名
   - 密钥
   - 角色和面板账户
3. 在新卷中生成根配置和角色。

```sh
chmod 600 first-setup.json
lb run --rm --no-deps -T --entrypoint /opt/lenbot/.venv/bin/python lenbot \
  -m len_bot.next.maintenance.initialize < first-setup.json
```

这条命令不监听端口，不调用模型，也不连接 QQ。根配置或同名角色已经存在时会报错，不会覆盖旧实例。`first-setup.json` 中有明文密钥，使用后请按凭据文件的标准保管，或者直接删除。之后的启动不再读取这个文件。

示例中的 `panel_host: "0.0.0.0"` 和 `panel_port: 11307` 是容器内的监听地址，Compose 只把端口发布到主机的回环地址。从其他机器访问面板时，请使用 SSH 端口转发，或者自行配置 HTTPS 反向代理。

## 网络和 OneBot

在容器中，`127.0.0.1` 指向容器本身。模型服务、OneBot 和向量服务的地址都要按容器网络填写。可以让这些服务和 LenBot 加入同一个 Docker 网络，用容器名访问；也可以填写 Docker 主机在网络中的地址。

示例配置中的反向 OneBot 监听 8080 端口，但 Compose 配置没有公开这个端口。可以让 OneBot 加入同一个网络并连接 `ws://lenbot:8080`，也可以在 `host.compose.yaml` 中按实际网络添加端口映射。

访问私有 Git 仓库时，使用运行身份自己的 Git 和 SSH 配置。HOME 目录位于实例卷中（`/srv/lenbot`）。

## 后台任务：同一个 daemon、同一个路径

后台任务由宿主通过 Docker socket 请求 daemon 启动任务容器。daemon 按**主机路径**解释绑定目录，因此以下三处路径必须完全一致。

- 挂载的 `source`
- 宿主容器中的 `target`
- 根配置中的路径

例如，不能把主机的 `/data/tasks` 挂载到宿主容器的 `/srv/tasks`，再让 daemon 按 `/srv/tasks` 创建任务。参考 [Docker bind mount 说明](https://docs.docker.com/engine/storage/bind-mounts/)。

把仓库中的 [`host.tasks.compose.yaml`](host.tasks.compose.yaml) 复制到部署目录。在 Linux 上，先在 daemon 所在的主机上创建目录，属主与宿主和任务使用的 UID/GID 一致。

```sh
sudo install -d -o 10000 -g 10000 -m 0700 \
  /srv/lenbot/task-workspaces /srv/lenbot/task-runtime /srv/lenbot/deliveries
```

已有的目录请按实际权限和数据归属处理，不要递归修改旧实例的属主。

下面是根配置 `worker` 中与路径相关的字段。其余字段涉及模型和资源，以及权限，请按完整配置填写，也可以在面板的能力页配置。

```json
{
  "docker_binary": "/usr/bin/docker",
  "docker_host": "unix:///var/run/docker.sock",
  "workspace_root": "/srv/lenbot/task-workspaces",
  "runtime_root": "/srv/lenbot/task-runtime",
  "delivery_root": "/srv/lenbot/deliveries",
  "skills_directory": "/srv/lenbot/skills",
  "uid": 10000,
  "gid": 10000
}
```

socket 挂载的 `source` 使用 daemon 主机上实际的 Unix socket，通常是 `/var/run/docker.sock`。使用 rootless Engine 时按实际路径填写。`target` 保持 `/var/run/docker.sock`。用下面的命令查出 socket 在容器中的组号，写入 `host.tasks.compose.yaml` 的 `group_add`。

```sh
docker run --rm --network none \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  --entrypoint stat ghcr.io/lendevs/lenbot:0.2.0 -c '%g' /var/run/docker.sock
```

不要修改 socket 的权限，也不需要让宿主以 root 身份运行。socket 只交给宿主，不挂载进任务容器。任务容器以非 root 身份运行，根文件系统只读，没有网络。模型请求和公网访问都经过宿主转发。

启用任务后，所有命令都要带上这份配置。

```sh
alias lb='docker compose -p lenbot -f host.compose.yaml -f host.version.compose.yaml -f host.updater.compose.yaml -f host.tasks.compose.yaml'
lb config >/dev/null && lb up -d
```

更新器切换版本时，会沿用当前宿主容器的挂载和 `group_add`，任务挂载不会丢失。

每次任务开始时，技能会被复制到任务运行目录的 `control/skills`，再按原路径以只读方式挂载。不需要把宿主的程序环境卷或插件源码挂载给任务。续接任务时会重新获取当前选中的技能，会话和工作文件保持不变。

### macOS Docker Desktop

宿主也运行在 Docker Desktop 中时，需要做以下调整。

1. 把 Compose 配置和根配置中**所有**位于 `/srv/lenbot` 下的任务路径，改为可以共享的固定位置，例如 `/Users/Shared/lenbot/...`。
2. 在主机上创建这些目录，并授予运行身份读写权限。
3. 在 Docker Desktop 的设置中允许共享这些目录。

实例数据仍然保存在命名卷中。

socket 挂载的 `source` 使用 Docker Desktop Linux 虚拟机中的 `/var/run/docker.sock`。不要使用 `docker context` 显示的 macOS 客户端 socket（例如 `~/.docker/run/docker.sock`），它作为绑定目录时会报 `operation not supported`。组号以挂载进容器后 `stat` 的结果为准。

### QQ 文件交付

需要发送文件时，把 `delivery_root` 也以只读方式挂载到 OneBot 容器，并把根配置中的 `onebot.upload_visible_root` 设为 OneBot 容器中看到的路径。例如 OneBot 把主机的 `/srv/lenbot/deliveries` 挂载为 `/lenbot-files:ro`，这里就填写 `/lenbot-files`。

## 升级

在面板的设置 → 版本与更新中打开更新页，选择版本并准备，然后确认停机升级。更新器按以下顺序执行。

1. 拉取新的宿主镜像，创建新的程序环境卷和候选容器。
2. 停机后为实例卷制作完整快照。
3. 执行数据迁移。
4. 换上新容器，并改写 `host.version.compose.yaml`。

使用官方任务镜像时，还会同时拉取同版本的任务镜像，并改写 `worker.image`。升级失败，或者想回到升级前的状态时，在更新页恢复快照。详见文档站的[更新与恢复](https://lendevs.github.io/LenBot/guide/update)。

面板无法打开时，更新页的链接在更新器的日志中。

```sh
lb logs lenbot-updater
```

新版本需要更新的更新器时，更新页会给出类似下面的命令。

```sh
docker run --rm --mount type=bind,source="$PWD",target=/deployment \
  --entrypoint python ghcr.io/lendevs/lenbot-updater:<新版本> \
  /opt/lenbot-updater/init.py --version <新版本> --updater-only
docker compose -p lenbot -f host.updater.compose.yaml up -d
```

执行后回到更新页继续。确认不需要恢复后，可以手动删除旧版本的程序环境卷和容器。实例卷不会随版本删除，`compose down` 也不会删除外部卷。

## 从源码构建镜像

```sh
docker build -f deploy/current/Dockerfile -t lenbot-current:local .
```

不传构建参数时从源码构建，用于本地开发。发行构建安装同一个 wheel 和同一份依赖清单，命令见[构建与发布](../releasing.md)。
