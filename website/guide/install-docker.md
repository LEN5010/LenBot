# Docker

Docker 安装用到三个镜像，每个都有 Linux amd64 和 arm64 版本。

| 镜像 | 用途 |
|---|---|
| `ghcr.io/lendevs/lenbot` | 程序和面板 |
| `ghcr.io/lendevs/lenbot-updater` | 更新器，负责在面板里切换版本，以及升级失败时恢复 |
| `ghcr.io/lendevs/lenbot-worker` | 后台任务容器，使用后台任务时才需要 |

Docker Hub 上也有同名镜像，例如 `docker.io/lendevs/lenbot`。

::: warning 0.2.0 尚未发布
镜像会随 0.2.0 发布。在此之前，可以用仓库里的 [Dockerfile](https://github.com/lendevs/LenBot/blob/master/deploy/current/Dockerfile) 从源码构建，步骤见[从源码构建镜像](https://github.com/lendevs/LenBot/blob/master/deploy/current/docker.md#从源码构建镜像)。
:::

## 安装

新建一个部署目录，用更新器镜像生成 Compose 文件和数据卷。这一步只写入文件并创建数据卷，不启动服务。

```sh
mkdir -p ~/lenbot && cd ~/lenbot
docker run --rm \
  --mount type=bind,source="$PWD",target=/deployment \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  --entrypoint python ghcr.io/lendevs/lenbot-updater:0.2.0 \
  /opt/lenbot-updater/init.py --version 0.2.0
```

执行后，目录里会出现下面这些文件。

| 文件 | 内容 |
|---|---|
| `host.compose.yaml` | LenBot 容器 |
| `host.version.compose.yaml` | 当前使用的镜像版本，切换版本时由更新器改写 |
| `host.updater.compose.yaml` | 更新器容器 |
| `deployment.json`、`current.json` | 更新器读取的部署信息 |
| `backups/` | 升级前的快照 |

同时会创建两个数据卷。`lenbot-data` 存放实例数据，`lenbot-python-<版本>` 存放程序环境。

`init.py` 的常用选项如下。

| 选项 | 作用 |
|---|---|
| `--registry dockerhub` | 改从 Docker Hub 拉取镜像 |
| `--panel-port`、`--update-port` | 面板和更新页在本机的端口，默认 11307 和 11308 |
| `--project` | 容器和数据卷名称的前缀，默认 `lenbot`。同一台机器运行多个实例时需要修改 |

## 第一次启动

```sh
docker compose -p lenbot -f host.compose.yaml -f host.version.compose.yaml -f host.updater.compose.yaml up -d
docker compose -p lenbot logs lenbot
```

日志里有首次配置的链接，格式为 `http://127.0.0.1:11307/#token=...`。在这台机器上打开它，按[首次配置](./first-setup)填写。之后面板也使用这个地址。

面板和更新页只发布到本机回环地址。从其他机器访问时，请使用 SSH 端口转发，或者自行配置 HTTPS 反向代理。

每条 `docker compose` 命令都要带上这三个 `-f`。日常停止和启动的命令如下。

```sh
docker compose -p lenbot -f host.compose.yaml -f host.version.compose.yaml -f host.updater.compose.yaml stop
docker compose -p lenbot -f host.compose.yaml -f host.version.compose.yaml -f host.updater.compose.yaml start
```

## 网络和 OneBot

容器里的 `127.0.0.1` 指的是容器本身。OneBot 和模型服务的地址都要按容器网络填写，向量服务也一样。可以让 OneBot 和 LenBot 加入同一个 Docker 网络，用容器名访问；也可以填写宿主机在 Docker 网络中的地址。

使用反向 WebSocket 时，默认的 Compose 文件没有公开 LenBot 监听 OneBot 的端口。请按实际网络在 `host.compose.yaml` 中添加端口映射，或者让 OneBot 和 LenBot 处于同一个网络。

## 更新

在面板的设置中打开更新与恢复页，按页面步骤选择版本，完成准备后确认升级，详见[更新与恢复](./update)。更新器会拉取新镜像并换上新的程序环境卷，然后在停机期间备份和迁移数据。

## 后台任务

后台任务需要 LenBot 通过宿主机的 Docker 启动任务容器。为此要把 Docker socket 和任务目录挂载给 LenBot，并在面板的能力页配置任务环境。路径和权限的细节较多，见仓库里的 [Docker 部署说明](https://github.com/lendevs/LenBot/blob/master/deploy/current/docker.md#后台任务同一个-daemon同一个路径)。
