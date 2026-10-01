# 部署导航

从[当前部署说明](current/README.md)开始。唯一宿主入口为 `len-bot`，历史格式转换使用停机后的显式维护命令。

| 文件 | 用途 |
|---|---|
| [current/README.md](current/README.md) | 安装、日常启动、Linux 服务及外部依赖 |
| [current/Dockerfile](current/Dockerfile) | 宿主与面板镜像，不含完整任务 Docker 环境 |
| [../docker/next-worker/](../docker/next-worker/) | 独立任务镜像、宿主桥和浏览器脚本 |
| [current/operations.md](current/operations.md) | 日常管理、离线迁移与归档 |
