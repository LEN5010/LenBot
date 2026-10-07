# Docker 部署

面向在 Docker 里运行 LenBot 的维护者。入门步骤见文档站的 [Docker 安装](https://lendevs.github.io/LenBot/guide/install-docker)，这里补充配方结构、首次配置的离线方式、后台任务挂载和升级细节。

| 镜像 | 作用 |
|---|---|
| `ghcr.io/lendevs/lenbot` | 宿主：程序、面板、Git、SSH、uv 和 `docker` 客户端，不运行 Docker daemon |
| `ghcr.io/lendevs/lenbot-updater` | 更新器：生成配方、换版本、升级失败时恢复 |
| `ghcr.io/lendevs/lenbot-worker` | 后台任务容器，启用任务时才需要 |

Docker Hub 上是同名的 `docker.io/lendevs/...`。三种镜像都有 Linux amd64 和 arm64。所有业务参数只来自实例里的 `lenbot.config.json`，不通过环境变量改模型、场景或任务参数。

## 初始化部署目录

部署目录存放 Compose 配方和更新器的状态，实例数据在命名卷里。用更新器镜像初始化一个新目录，这一步只建文件和卷，不启动服务：

```sh
mkdir -p ~/lenbot && cd ~/lenbot
docker run --rm \
  --mount type=bind,source="$PWD",target=/deployment \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  --entrypoint python ghcr.io/lendevs/lenbot-updater:0.2.0 \
  /opt/lenbot-updater/init.py --version 0.2.0
```

| 选项 | 默认 | 说明 |
|---|---|---|
| `--registry` | `ghcr` | 改成 `dockerhub` 从 Docker Hub 拉镜像 |
| `--project` | `lenbot` | 容器、卷和 Compose 项目名的前缀；一台机器跑多个实例时各用一个 |
| `--panel-port` | 11307 | 面板发布到本机回环的端口 |
| `--update-port` | 11308 | 更新页发布到本机回环的端口 |
| `--host-directory` | 自动读取 | Docker daemon 看到的部署目录绝对路径，通常不用填 |

生成的文件：

| 文件 | 内容 |
|---|---|
| `host.compose.yaml` | 宿主容器：工作目录 `/srv/lenbot`，身份 `10000:10000`，实例卷和程序环境卷 |
| `host.version.compose.yaml` | 当前镜像和程序环境卷，换版时由更新器改写，不要手改 |
| `host.updater.compose.yaml` | 更新器容器，挂部署目录和 Docker socket |
| `deployment.json`、`current.json` | 更新器读的部署信息和当前版本 |
| `backups/` | 升级前的快照 |

卷：`<项目>-data` 放实例，`<项目>-python-<版本>` 放这个版本的程序环境。新卷从镜像填充程序和目录属主；插件依赖装在程序环境卷里，重建容器不会丢。

下面的命令都用这一组配方，先设一个别名：

```sh
alias lb='docker compose -p lenbot -f host.compose.yaml -f host.version.compose.yaml -f host.updater.compose.yaml'
lb up -d
lb logs lenbot        # 首次配置链接、运行日志
lb stop
```

## 首次配置

默认方式：第一次 `up -d` 后，宿主日志里有首次配置链接 `http://127.0.0.1:11307/#token=...`，在这台机器上打开填写，保存后直接启动并进入面板。

也可以离线生成配置，不开网页。复制包里的 `deploy/current/first-setup.example.json` 为 `first-setup.json`，限制权限后填写实际 QQ 号、群号、模型地址和名称、密钥、角色与面板账户，然后在新卷里生成根配置和角色：

```sh
chmod 600 first-setup.json
lb run --rm --no-deps -T --entrypoint /opt/lenbot/.venv/bin/python lenbot \
  -m len_bot.next.maintenance.initialize < first-setup.json
```

这条命令不监听端口、不调用模型、不连接 QQ；根配置或同名角色已存在时报错，不覆盖旧实例。`first-setup.json` 含明文密钥，用完按凭据文件保管或删除，之后的启动不再读它。

示例里的 `panel_host: "0.0.0.0"` 和 `panel_port: 11307` 是容器内的监听地址，Compose 只发布到主机回环。从别的机器访问面板，用 SSH 端口转发或自己配 HTTPS 反向代理。

## 网络和 OneBot

容器里的 `127.0.0.1` 是容器自己。模型、OneBot、向量服务的地址按容器网络填写：让它们和 LenBot 加入同一个 Docker 网络并用容器名访问，或者填 Docker 主机在网络里的地址。

示例的反向 OneBot 监听 8080，配方没有公开这个端口。让 OneBot 加入同一网络并连接 `ws://lenbot:8080`，或者在 `host.compose.yaml` 里按实际网络加端口映射。

私有 Git 用运行身份自己的 Git 和 SSH 配置，HOME 在实例卷里（`/srv/lenbot`）。

## 后台任务：同一个 daemon、同一个路径

后台任务由宿主通过 Docker socket 让 daemon 启动任务容器。绑定目录由 daemon 按**主机路径**解释，所以 `source`、宿主容器里的 `target` 和根配置里的路径必须完全一致；不能把主机 `/data/tasks` 挂到宿主的 `/srv/tasks`，再让 daemon 按 `/srv/tasks` 建任务。[Docker bind mount 说明](https://docs.docker.com/engine/storage/bind-mounts/)。

把仓库里的 [`host.tasks.compose.yaml`](host.tasks.compose.yaml) 复制到部署目录。Linux 上先在 daemon 主机建好目录，属主与宿主和任务的 UID/GID 一致：

```sh
sudo install -d -o 10000 -g 10000 -m 0700 \
  /srv/lenbot/task-workspaces /srv/lenbot/task-runtime /srv/lenbot/deliveries
```

已有目录按实际权限和数据归属处理，不要递归改旧实例的属主。根配置 `worker` 里对应的路径字段（其余模型、资源和权限字段照完整配置填写，也可以在面板的能力页配置）：

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

socket 的 `source` 用 daemon 主机上的实际 Unix socket，通常是 `/var/run/docker.sock`，rootless Engine 按实际路径填，`target` 保持 `/var/run/docker.sock`。查出 socket 在容器里的组号，写到 `host.tasks.compose.yaml` 的 `group_add`：

```sh
docker run --rm --network none \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  --entrypoint stat ghcr.io/lendevs/lenbot:0.2.0 -c '%g' /var/run/docker.sock
```

不要改 socket 权限，也不必让宿主以 root 运行。socket 只交给宿主，不挂进任务容器；任务容器是非 root、只读根文件系统、无网络，模型和公共出口走宿主管道。

启用任务后，所有命令都要带上这份配方：

```sh
alias lb='docker compose -p lenbot -f host.compose.yaml -f host.version.compose.yaml -f host.updater.compose.yaml -f host.tasks.compose.yaml'
lb config >/dev/null && lb up -d
```

更新器换版时会沿用当前宿主容器的挂载和 `group_add`，任务挂载不会丢。

技能在每次任务开始时复制到任务运行目录的 `control/skills`，再按原路径只读挂载，不需要把宿主的程序环境卷或插件源码挂给任务。续接任务会重新取当前选中的技能，会话和工作文件保持不变。

### macOS Docker Desktop

宿主也跑在 Docker Desktop 里时，把配方和根配置里**所有** `/srv/lenbot` 下的任务路径改成可共享的固定位置，例如 `/Users/Shared/lenbot/...`，在主机建好目录、赋予运行身份读写权限，并在 Desktop 设置里允许共享。实例数据仍在命名卷里。

socket 的 `source` 用 Desktop Linux 虚拟机里的 `/var/run/docker.sock`，不要用 `docker context` 显示的 macOS 客户端 socket（如 `~/.docker/run/docker.sock`），后者作为绑定目录会报 `operation not supported`。组号以挂进容器后的 `stat` 结果为准。

### QQ 文件交付

需要发文件时，把 `delivery_root` 也只读挂到 OneBot 容器，并把根配置的 `onebot.upload_visible_root` 设成 OneBot 容器里看到的路径。例如 OneBot 挂载主机 `/srv/lenbot/deliveries` 到 `/lenbot-files:ro`，就填 `/lenbot-files`。

## 升级

在面板 → 设置 → 版本与更新里打开更新页：选版本、准备、确认停机升级。更新器拉取新的宿主镜像，建新的程序环境卷和候选容器，停机后对实例卷做完整快照，执行数据迁移，换上新容器并改写 `host.version.compose.yaml`。用官方任务镜像时会同时拉取同版本的任务镜像并改写 `worker.image`。失败或想回到升级前时，在更新页恢复快照。详见文档站的[更新与恢复](https://lendevs.github.io/LenBot/guide/update)。

面板打不开时，更新页链接在更新器日志里：

```sh
lb logs lenbot-updater
```

新版本要求更新更新器时，更新页会给出命令，形如：

```sh
docker run --rm --mount type=bind,source="$PWD",target=/deployment \
  --entrypoint python ghcr.io/lendevs/lenbot-updater:<新版本> \
  /opt/lenbot-updater/init.py --version <新版本> --updater-only
docker compose -p lenbot -f host.updater.compose.yaml up -d
```

然后回到更新页继续。旧版本的程序环境卷和容器在确认不需要恢复后可以手动删除；实例卷不随版本删除，`compose down` 也不会删外部卷。

## 从源码构建镜像

```sh
docker build -f deploy/current/Dockerfile -t lenbot-current:local .
```

不传构建参数时从源码构建，用于本地开发。发行构建安装同一个 wheel 和依赖清单，命令见[构建与发布](../releasing.md)。
