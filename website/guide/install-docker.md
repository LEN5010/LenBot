# Docker

Docker 安装用到三个镜像，都有 Linux amd64 和 arm64 版本：

| 镜像 | 作用 |
|---|---|
| `ghcr.io/lendevs/lenbot` | 程序和面板 |
| `ghcr.io/lendevs/lenbot-updater` | 更新器：在面板里换版本、升级失败时恢复 |
| `ghcr.io/lendevs/lenbot-worker` | 后台任务容器，用到任务时才需要 |

Docker Hub 上有同名镜像：`docker.io/lendevs/lenbot` 等。

::: warning 0.2.0 还没有发布
镜像会随 0.2.0 发布。在那之前可以用仓库里的 [Dockerfile](https://github.com/lendevs/LenBot/blob/master/deploy/current/Dockerfile) 从源码构建，步骤见[从源码构建镜像](https://github.com/lendevs/LenBot/blob/master/deploy/current/docker.md#从源码构建镜像)。
:::

## 安装

新建一个部署目录，用更新器镜像生成 Compose 配方和数据卷。这一步只写文件、建卷，不启动服务：

```sh
mkdir -p ~/lenbot && cd ~/lenbot
docker run --rm \
  --mount type=bind,source="$PWD",target=/deployment \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  --entrypoint python ghcr.io/lendevs/lenbot-updater:0.2.0 \
  /opt/lenbot-updater/init.py --version 0.2.0
```

它会在目录里写下这些文件：

| 文件 | 内容 |
|---|---|
| `host.compose.yaml` | LenBot 容器 |
| `host.version.compose.yaml` | 当前用的镜像版本，换版时由更新器改写 |
| `host.updater.compose.yaml` | 更新器容器 |
| `deployment.json`、`current.json` | 更新器读的部署信息 |
| `backups/` | 升级前的快照 |

还会建两个数据卷：`lenbot-data` 放实例，`lenbot-python-<版本>` 放程序环境。

`init.py` 的常用选项：

| 选项 | 作用 |
|---|---|
| `--registry dockerhub` | 改从 Docker Hub 拉镜像 |
| `--panel-port`、`--update-port` | 面板和更新页在本机的端口，默认 11307 和 11308 |
| `--project` | 容器和卷名的前缀，默认 `lenbot`；同一台机器跑多个实例时改它 |

## 第一次启动

```sh
docker compose -p lenbot -f host.compose.yaml -f host.version.compose.yaml -f host.updater.compose.yaml up -d
docker compose -p lenbot logs lenbot
```

日志里有首次配置的链接，形如 `http://127.0.0.1:11307/#token=...`，在这台机器上打开，按[首次配置](./first-setup)填完。面板之后也在这个地址。

面板和更新页只发布到本机回环地址。从别的机器访问，用 SSH 端口转发或自己配 HTTPS 反向代理。

三个 `-f` 每次都要带上。日常启停：

```sh
docker compose -p lenbot -f host.compose.yaml -f host.version.compose.yaml -f host.updater.compose.yaml stop
docker compose -p lenbot -f host.compose.yaml -f host.version.compose.yaml -f host.updater.compose.yaml start
```

## 网络和 OneBot

容器里的 `127.0.0.1` 是容器自己。OneBot、模型、向量服务的地址要按容器网络填写：让 OneBot 和 LenBot 加入同一个 Docker 网络并用容器名访问，或者填宿主机在 Docker 网络里的地址。

用反向 WebSocket 时，配方默认没有公开 LenBot 的 OneBot 监听端口，需要按实际网络在 `host.compose.yaml` 里加端口映射，或者让 OneBot 和 LenBot 在同一网络。

## 更新

在面板的设置里打开更新与恢复页，选版本、准备、确认升级，见[更新与恢复](./update)。更新器会拉取新镜像，换上新的程序环境卷，在停机时备份并迁移数据。

## 后台任务

后台任务要让 LenBot 通过宿主机的 Docker 启动任务容器，需要把 Docker socket 和任务目录挂给 LenBot，并在面板的能力页配置任务环境。路径和权限的细节较多，见仓库里的 [Docker 部署说明](https://github.com/lendevs/LenBot/blob/master/deploy/current/docker.md#工作任务同一个-daemon同一个路径)。
