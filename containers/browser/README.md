# 独立浏览器 worker

本目录提供独立浏览器的固定容器入口。它复用 Worker Gateway 的执行、期限、取消和产物接口，逐条命令另记在同一执行日志的 `execution_commands`。本目录存在不代表出口与浏览器部署已验收；当前放行状态见 [当前任务](../../docs/iteration.md)。

在获准的 Linux 构建环境，从项目根目录正常构建：

```sh
docker build -f containers/browser/Dockerfile -t lenbot-browser:local .
```

镜像只带浏览器代码、类型协议、Playwright 1.62.0 和 Pydantic 2.13.5，不复制根配置、数据库、账号凭据、登录态或宿主容器接口。镜像内的包查找与浏览器安装路径是构建布局，不是 LenBot 运行配置覆盖入口。

按 [Playwright 官方 Docker 说明](https://playwright.dev/python/docs/docker) 为 Chromium 启用非 root 用户及 seccomp 用户命名空间权限。由运营者将 [1.62.0 的 seccomp 文件](https://github.com/microsoft/playwright/blob/v1.62.0/utils/docker/seccomp_profile.json) 安装到 Gateway 主机的 `/etc/lenbot-gateway/browser-seccomp.json`，路径写入部署镜像条目的 `browser_seccomp_profile`。不要填 `unconfined`。Gateway 固定使用只读根、cap drop、no-new-privileges、独立 IPC、256 MiB shm、`--init`、资源上限和只读控制目录；不增加 SYS_ADMIN 或 host IPC。

`gateway.config.example.json` 包含 browser 镜像引用与默认未核验的 public 策略。实际部署必须确认内部容器网络只能到达出口代理指定地址和端口，不能绕行宿主、DNS、私网或其他容器。确认后才可把该部署的 `deployment_verified` 改为 true；单改此字段不构成验收。需要分别人工观察域名/资源域转发、重定向、CONNECT 计字节、超限、取消、Gateway 与宿主重启和未知终止占用；不用自动探针或截图代替证据。

LenBot 根配置选择 `plugins.workspace.config.gateway` 后，browser_agent 使用自己的 `image_ref`（默认 browser）和 `network_policy`（默认 public），通过同一 Gateway 地址/token 请求独立 worker。插件初始化不会建立宿主 Chromium。浏览器会话最长为 Gateway `execution_timeout_seconds`、1800 秒及原工作剩余期限的较小值；运营者需明确配置适合页面阅读的期限。修改 workspace 后端后重新加载 browser_agent。

每个工作修订只建立一个浏览器执行，多个页面由它持有；page_ref、snapshot_revision 仅在本会话内有效。`open/snapshot/interact/capture` 的请求与结果持久保存，命令运行前记 running；失联只查同一 command_id，不重放点击。任一进程重启后原页面失效，需要人工继续工作形成新修订；已保存 R 正文仍可续读。正文在 DOM 采集时受 max_snapshot_chars 限制，记录 collection_truncated；截图按高度、字节与产物数量限制，经 Gateway 产物接口取回后才登记媒体，pixels_loaded 保持 false。

升级前按 [运行手册](../../docs/operations.md) 停机、普通备份。宿主和 Gateway 的执行日志都增加命令表；不能用旧代码重放新版本的执行或删除未知结果记录。取消只在 Gateway 确认容器消失/停止后释放占用。
