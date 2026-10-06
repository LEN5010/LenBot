# Docker 宿主与任务

宿主镜像包含程序、面板、Git、SSH、uv 和 `/usr/bin/docker` 客户端，不运行 Docker daemon。普通聊天只用[基础配方](host.compose.yaml)；启用工作时再合并[任务挂载](host.tasks.compose.yaml)。所有业务参数仍只来自容器工作目录内的 `lenbot.config.json`，不设置 `DOCKER_HOST` 或通过环境变量改模型、场景及任务参数。

发行构建直接安装同次 wheel 和运行依赖清单，命令见[同版构建](../releasing.md)。默认 `docker build -f deploy/current/Dockerfile .` 保留从源码构建的开发入口；运行镜像与插件可写环境相同。

## 实例与程序分开

默认身份 `10000:10000`，工作目录 `/srv/lenbot`。实例使用原生命名卷，Python 环境使用与镜像发行版本对应的另一命名卷。新卷从镜像填充已安装程序和目录属主；重建容器复用同版环境，插件依赖不会丢失。

复制两份 Compose 文件到实例的部署目录，修改宿主镜像和 Python 卷名。示例 `r1` 只是当前版本的卷名，不是版本号。先创建卷：

```sh
docker volume create lenbot-data
docker volume create lenbot-python-r1
```

### 直接初始化新卷

不必在主机先装 Python 或 wheel。复制包中的 `first-setup.example.json` 为本机 `first-setup.json`，限制文件权限，再人工填写实际 QQ／群号、模型地址／名称／窗口、密钥、角色与面板账户：

```sh
cp /path/to/release/deploy/current/first-setup.example.json ./first-setup.json
chmod 600 first-setup.json
# 编辑 first-setup.json 后，在新卷内离线生成根配置及角色：
docker compose -f host.compose.yaml run --rm --no-deps -T \
  --entrypoint /opt/lenbot/.venv/bin/python lenbot \
  -m len_bot.next.maintenance.initialize < first-setup.json
```

此命令只解析初始化资料并生成卷内的 `lenbot.config.json` 和新角色，不监听端口、不调用模型、不连接 QQ。根配置或同名角色目录已存在时直接报错，不覆盖旧实例。输入 JSON 是首次配置资料，不是运行参数；后续启动不再读取它，填过的文件含明文凭据，按本机凭据文件管理。

Docker 示例使用 `panel_host: "0.0.0.0"` 和 `panel_port: 11307`，这两个值写入唯一根配置；Compose 只发布到主机回环。普通聊天填写一个 mind 绑定，直接组织回复。模型、OneBot、记忆等地址按容器网络填写，容器内 `127.0.0.1` 不是 Docker 主机。

示例反向 OneBot 监听 8080；基础配方没有替你公开该端口。让 OneBot 加入同一容器网络并连接 `ws://lenbot:8080`，或按实际桥接环境添加端口映射。示例监听与模拟发送配置不会替你登录 QQ，也不自动获得真实群连接。

### 导入已通过向导准备的新实例

仍可在主机用 wheel 向导创建后导入。先按容器位置调整新实例的根配置：

```sh
tar -C /absolute/path/to/new-instance -cf - lenbot.config.json personas | \
  docker run --rm -i --network none \
    --mount type=volume,source=lenbot-data,target=/srv/lenbot \
    --entrypoint tar lenbot-current:local --no-same-owner -xf - -C /srv/lenbot
```

这条导入路径只用于新卷。已有业务数据按离线升级维护，不用初始化资料覆盖。

普通聊天不用 Docker socket 或任务目录：

```sh
docker compose -f host.compose.yaml up -d
docker compose -f host.compose.yaml logs -f lenbot
docker compose -f host.compose.yaml stop
```

私有 Git 采用运行身份自己的 Git／SSH 配置，HOME 位于实例卷。依赖安装写入实际运行的 Python 卷；入口不自动安装、迁移或重试启动。面板明确重启仍由薄启动器完成。

## 工作任务：同一个 daemon、同一个路径

绑定目录由 Docker daemon 解释，而不是由调用它的宿主容器解释。故 `source`、宿主容器 `target` 和根配置路径必须一致；不能把主机 `/data/tasks` 挂到宿主 `/srv/tasks`，再让 daemon 按 `/srv/tasks` 创建任务。[Docker bind mount 说明](https://docs.docker.com/engine/storage/bind-mounts/)。

Linux 示例在 daemon 主机创建新目录，属主与宿主及任务 UID/GID 一致：

```sh
sudo install -d -o 10000 -g 10000 -m 0700 \
  /srv/lenbot/task-workspaces /srv/lenbot/task-runtime /srv/lenbot/deliveries
```

已有目录按实际权限和数据归属处理，不递归改变旧实例属主。根配置 `worker` 使用以下路径字段，其余模型、资源和权限字段沿完整配置填写：

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

这些是 `worker` 的部分字段，不是可直接替换完整根配置的样例。另需匹配的 `worker.image`、工作模型、场景任务开关及角色工具／技能许可。显式配置存储池时，将两个任务目录改为真实挂载下的路径，先挂池、再启动宿主；独立交付仍在池外。

Linux Engine 的 socket `source` 使用 daemon 主机上的实际 Unix socket，通常是 `/var/run/docker.sock`；rootless Engine 按其实际路径填写。`target` 保持 `/var/run/docker.sock`。从候选容器查看 socket 的组号，写到配方 `group_add`：

```sh
docker run --rm --network none \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  --entrypoint stat lenbot-current:local -c '%g' /var/run/docker.sock
```

这只读取组号，不调用 daemon。不要改 socket 权限，也不必让宿主以 root 运行；socket 只交给宿主，不挂进任务容器。任务仍为非 root、只读根文件系统、`network=none`，模型与公共出口沿现有宿主管道。

配置和挂载就绪后：

```sh
docker compose -f host.compose.yaml -f host.tasks.compose.yaml config
docker compose -f host.compose.yaml -f host.tasks.compose.yaml up -d
docker compose -f host.compose.yaml -f host.tasks.compose.yaml stop
```

内置、共享、场景和插件技能在每次执行开始时复制到任务运行目录的 `control/skills`，然后按原技能容器路径只读挂载。无需把宿主的 Python 卷或插件源码整棵挂给任务，也无需 daemon 访问镜像内的安装路径。原任务续接重新取当前已选技能，Pi 会话和工作文件保持；临时清理可移除这些副本。

### macOS Docker Desktop

也可以让 macOS 原生宿主使用 Desktop 执行任务。如果宿主本身运行在 Docker 中，复制的两份配方将 **所有** `/srv/lenbot` 改为可共享的固定位置，例如 `/Users/Shared/lenbot`，包括工作目录、HOME、实例卷目标和各任务路径；根配置使用相同位置。业务库依旧在命名卷中，不改为主机目录绑定。

在主机该位置创建三份任务目录并赋予运行身份读写权限，在 Desktop 设置中允许共享。Desktop 的 socket `source` 使用 Linux 虚拟机内的 `/var/run/docker.sock`，容器 `target` 与 `worker.docker_host` 保持上例。不要把 context 返回的 macOS 客户端 socket（如 `~/.docker/run/docker.sock`）当成文件共享目录绑定；本机该方式会返回 `operation not supported`。组号以挂入容器后的 `stat` 结果为准，不拿 macOS 的组号猜测 Linux 虚拟机权限。

Docker Desktop 会处理宿主共享目录与 Linux 虚拟机的映射；应用仍使用同一组显式路径，不新增路径翻译或自动降级逻辑。[Docker Desktop 的绑定目录](https://docs.docker.com/engine/storage/bind-mounts/)。

### QQ 文件交付

需要上传文件时，把 `delivery_root` 同时只读挂到 OneBot 容器，并将 `onebot.upload_visible_root` 设为 OneBot 容器看到的位置。以 `/lenbot-files` 为例，OneBot 的挂载是主机 `/srv/lenbot/deliveries` → `/lenbot-files:ro`。面板登记成功不表示平台收到文件，实际上传回执另行记录。

## 停机升级

停止宿主并按[离线维护](operations.md#升级与备份)备份实例卷和外置任务／交付目录。新镜像采用新的 Python 卷名，不把旧卷挂到新镜像后当作升级；旧卷会遮住新程序。

修改复制的基础配方中镜像和 Python 卷名，创建新卷，再在同一实例根用新环境执行版本要求的迁移及插件依赖恢复：

```sh
docker volume create lenbot-python-r2
docker compose -f host.compose.yaml run --rm --no-deps \
  --entrypoint /opt/lenbot/.venv/bin/python lenbot -m len_bot.next.maintenance.migrate_config
docker compose -f host.compose.yaml run --rm --no-deps \
  --entrypoint /opt/lenbot/.venv/bin/python lenbot -m len_bot.next.maintenance.migrate
docker compose -f host.compose.yaml run --rm --no-deps \
  --entrypoint /opt/lenbot/.venv/bin/python lenbot -m len_bot.next.maintenance.migrate_memory_jobs
docker compose -f host.compose.yaml run --rm --no-deps \
  --entrypoint /opt/lenbot/.venv/bin/python lenbot -m len_bot.next.maintenance.plugin_dependencies
```

有工作目录、存储池或其他外置挂载时，离线命令也合并对应挂载配方。迁移模块按本版说明选用；依赖恢复包含已配置的停用插件，不执行插件代码、不更新 Git ref。确认完成后再明确启动。

旧 Python 卷由操作者确认后删除，实例数据卷不随版本删除。基础配方使用 external 卷，`compose down` 不负责清理业务数据；任务环境释放继续由面板按任务身份操作。
