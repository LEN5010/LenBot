# macOS 开发机 Gateway

这份配方只服务本机联调：LenBot 以 `uv run len-bot` 在 macOS 上运行，Gateway 以 Docker 容器运行并独占 Docker socket；LenBot 容器本身不取得 socket。镜像安装 `docker-cli`（不是 `dockerd`）去调用宿主 Docker。SnowLuma 保持现有容器和端口，不加入 Gateway 的执行网络。

Gateway 同时加入控制网和一个只供出口代理使用的内部网。Python worker 加入内部出口网，浏览器和媒体只能把请求交给 Gateway 的出口代理；它们不能使用控制网。`deployment_verified` 仍须在本机完成正常浏览器/媒体工作并核对 Gateway 的执行记录后才可以改为 true，本文件不把镜像构建写成隔离验收。

## 启动顺序

1. 先按运行手册停机并做普通备份；当前开发机的备份目录由本轮记录保存。
2. 构建 `lenbot:release`、`lenbot-browser:local`、`lenbot-media:local` 和本目录的 `lenbot-gateway:dev`。
3. 复制 `gateway.config.example.json` 到本机运维目录，替换独立 token、路径和 seccomp 文件；不要把它放进仓库。`database_path` 与 `workspaces_root` 必须是 **本机绝对路径**，并且 compose 把该数据目录挂到容器内的 **同一绝对路径**。Gateway 通过宿主 Docker socket 启动 worker 时，`-v` 用的是这条路径；如果写成容器内部的 `/var/lib/lenbot-gateway`，宿主会挂空目录，worker 里就没有 `task.py`。
4. 使用 `compose.yaml` 启动 Gateway。把根配置的 workspace 后端切换为 `gateway`，`base_url` 使用 `http://127.0.0.1:8790`，并重启 LenBot。不能同时保留 worker 和 gateway。
5. 先做离线 Python，再做浏览器和媒体；每项都查看同一 execution_id 的实际状态、终止记录和产物。未完成网络隔离核对前，public 策略会拒绝启动。

此配方不启动、登录或发送 OneBot；SnowLuma 的 WebSocket 仍由根配置单独连接。Gateway 的 socket 权限和内部网络是本机 Docker Desktop 的部署条件，不能替代 Linux 目标机的出口与浏览器隔离验收。
