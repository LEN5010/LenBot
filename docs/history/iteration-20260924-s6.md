# 2026-09-24 本地 wheel 内容核对

面向安装与发布维护者。本页记录打包产物的实际构建和目录核对，任务状态只见[产品路线](../plan/README.md)，当前交接见[当前任务](../iteration.md)。这不是目标 Linux 安装、源码授权或发行验收。

分支 `feat/s0-product-contract`，基线 `a54a370`；此前前端源码已构建为本机 `src/len_bot/web/static/dist`，产物不进 Git。本批未改打包配置，使用当前源码在仓库根目录执行：

```sh
uv --cache-dir /private/tmp/lenbot-uv-cache build --wheel --offline --out-dir /private/tmp/lenbot-wheel-review
```

命令退出 0，得到 `/private/tmp/lenbot-wheel-review/len_bot-0.1.0-py3-none-any.whl`，压缩文件 14,198,875 字节；用 `unzip -Z -1` 与 `unzip -l` 只读核对共 363 个目录／文件条目，解包总量 29,921,246 字节。包内有 `len_bot/web/static/dist/index.html`、当前 `SettingsView` 构建资源、`group_summary` 与 `workspace` 插件、`len-bot = len_bot:main` 命令入口；没有前端源码目录、`node_modules`、真实根配置、数据库或文件资产目录。包仍带 `asoul_calendar/resources/font.ttf`（25,631,744 字节）与 `cards/bilibili/logo.png`（53,782 字节），其再分发授权须由维护者决定；此次构建未改变素材归属。

`py3-none-any` 只说明本 wheel 的标签，**不证明**所需依赖在 Linux/amd64 和 Linux/arm64 的可用性、空环境安装、字体装载、群报告、Gateway 产物或 SnowLuma 上传。wheel 依赖事先构建的前端产物，不能拿本次包含页面当作从原始 Git 检出即能得到同一包。没有安装 wheel、启动服务、读取真实配置／业务库、调用模型／平台或实发；未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务。没有本批业务运行失败原文。
