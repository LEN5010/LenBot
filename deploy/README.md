# 部署目录导航

新实例从 [current/README.md](current/README.md) 开始，不复制旧根配置样例。

| 位置 | 用途 | 当前开发是否使用 |
|---|---|---|
| [current/](current/README.md) | 当前核心 Linux 部署、systemd、原生记忆模板与离线维护 | 是 |
| [linux/Dockerfile](linux/Dockerfile) | 构建当前宿主包和面板的镜像配方，沿用现有路径 | 是；不等于含任务 Docker 环境 |
| `linux/README.md`、`linux/compose.yaml`、Gateway 服务／样例 | 冻结旧核心的部署与恢复材料 | 只供原部署核对，不用于新实例初始化 |
| `dev/` | 旧 Gateway 开发环境 | 不作为当前核心开发入口 |
| [../docker/next-worker/](../docker/next-worker/) | 当前 Pi 任务镜像、任务桥和浏览器脚本 | 启用任务时使用 |
| `../containers/` | 旧 worker 镜像材料 | 保留旧实例依赖，不替换当前任务镜像 |

这里按真实用途区分入口，不搬动旧 Compose／服务路径，也不改变正在运行的部署。旧核心配置样例和 `scripts/migrate_observation_config.py` 同样保留旧用途；当前配置由首次向导或面板管理。
