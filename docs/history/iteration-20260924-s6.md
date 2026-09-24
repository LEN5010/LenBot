# 2026-09-24 本地 wheel 内容核对

面向安装与发布维护者。本页记录打包产物的实际构建和目录核对，任务状态只见[产品路线](../plan/README.md)，当前交接见[当前任务](../iteration.md)。这不是目标 Linux 安装、源码授权或发行验收。

分支 `feat/s0-product-contract`，基线 `a54a370`；此前前端源码已构建为本机 `src/len_bot/web/static/dist`，产物不进 Git。本批未改打包配置，使用当前源码在仓库根目录执行：

```sh
uv --cache-dir /private/tmp/lenbot-uv-cache build --wheel --offline --out-dir /private/tmp/lenbot-wheel-review
```

命令退出 0，得到 `/private/tmp/lenbot-wheel-review/len_bot-0.1.0-py3-none-any.whl`，压缩文件 14,198,875 字节；用 `unzip -Z -1` 与 `unzip -l` 只读核对共 363 个目录／文件条目，解包总量 29,921,246 字节。包内有 `len_bot/web/static/dist/index.html`、当前 `SettingsView` 构建资源、`group_summary` 与 `workspace` 插件、`len-bot = len_bot:main` 命令入口；没有前端源码目录、`node_modules`、真实根配置、数据库或文件资产目录。包仍带 `asoul_calendar/resources/font.ttf`（25,631,744 字节）与 `cards/bilibili/logo.png`（53,782 字节），其再分发授权须由维护者决定；此次构建未改变素材归属。

`py3-none-any` 只说明本 wheel 的标签，**不证明**所需依赖在 Linux/amd64 和 Linux/arm64 的可用性、空环境安装、字体装载、群报告、Gateway 产物或 SnowLuma 上传。wheel 依赖事先构建的前端产物，不能拿本次包含页面当作从原始 Git 检出即能得到同一包。没有安装 wheel、启动服务、读取真实配置／业务库、调用模型／平台或实发；未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务。没有本批业务运行失败原文。

## 源码包保留前端重建材料

基线 `b133da4`。继续构建原配置的 sdist 后发现 `source-exclude` 排除了整个 `web/frontend`：包内虽有已构建页面，但缺 `src/`、`package-lock.json` 和 Vite 配置，无法从该**源码包**重新生成页面。仅将该排除路径收窄为 `web/frontend/node_modules`，保留现有 `wheel-exclude`；不用复制第二套前端或改运行入口。

修改后 `uv build --sdist --offline` 退出 0，包内核对可见 `web/frontend/src/views/SettingsView.vue`、`package.json`、`package-lock.json`、`vite.config.js` 与已构建 `web/static/dist/index.html`，没有本机 `node_modules`。直接从源码离线构建 wheel 退出 0，包内只有已构建页面而无前端源码／工具链。首次尝试离线从 sdist 构建 wheel 退出 2，原文为 `uv-build was not found in the cache`；这是构建后端缺缓存，不是源码包缺失。随后允许该构建命令取得声明的后端，从同一 sdist 构建 wheel 退出 0，包内再次核对 `group_summary/plugin.py` 和 `web/static/dist/index.html`，没有前端源码／工具链。没有安装产物、运行目标架构或开展业务操作；`git diff --check` 退出 0。素材授权、同版空环境安装和真实平台回执仍未确认。

## 发行包简述去占位

基线 `411040e`。只读解包确认 wheel 的 `METADATA` 仍写 `Summary: Add your description here`，与已有[首页事实描述](../../README.md)不符。本批仅将 `pyproject.toml` 的简述改为当前可核对的 QQ 群聊、工具调用、后台工作与运营面板，不改版本号、许可证、依赖或支持承诺。离线 wheel 构建退出 0，解包 `METADATA` 显示新简述；`git diff --check` 退出 0。没有安装、运行、实发或执行仓库禁止的验证任务。公开定位、授权与发行决定仍归维护者。

## 授权底稿的当前产物注记

基线 `a5ad204`。[授权事实底稿](../plan/s0-04-06-license-and-support.md)固定在 2026-09-21 的核对提交，仍写当时的简述占位和整目录前端排除；为避免读者把它们误作当前源码，本批只在底稿开头加一条带日期的后续产物注记，保留原表格与行号。注记引用本页已观察到的 wheel 字体／logo、sdist 前端材料和新的包简述，不推断代码或素材可再分发，不修改授权、打包规则或发行范围。`git diff --check` 退出 0；无新构建、业务运行、测试或数据操作。

## 候选包同提交构建顺序

基线 `5597ec2`。原版本化流程只要求产物来自候选提交，却未明确 Git 中不保存的前端页面必须先于 sdist/wheel 构建；从无 `web/static/dist` 的干净检出直接打包可能得到缺页面的包。本批在原[贡献流程](../../CONTRIBUTING.md#从开发提交到版本发布)中补同提交前端锁文件安装、面板构建、全新目录的源码包／轮子构建及包内容复核顺序，不新增发布脚本或 CI 检查。当前工作区已有本机面板产物，以 `uv build --sdist --wheel --offline --out-dir /private/tmp/lenbot-candidate-build-review` 实际构建，命令退出 0，产生 `len_bot-0.1.0.tar.gz` 和 `len_bot-0.1.0-py3-none-any.whl`。这不是从全新检出安装依赖、不是候选提交或获准发行；未创建标签、推送或发布，也未运行测试或业务服务。`git diff --check` 退出 0。

## CI 发行包构建入口

基线 `4eeec68`。原 CI 的 Python 编译与前端构建止于 `npm run build`，没有执行前述已确认顺序的 sdist／wheel 构建；本批只在面板构建之后追加 `uv build --sdist --wheel --out-dir dist`，不增加上传、发布、安装或测试步骤。贡献流程同步说明 CI 范围。本批仅做源码与流程核对，尚未触发远端 CI；前一批的本机组合打包记录不能代替新 CI 步骤的运行结果。未读取真实配置／数据、启动服务、调用模型／平台或实发，也未新增、修改或运行仓库禁止的验证任务。

## 构建后端固定

基线 `484c016`。现有 CI 固定 uv 命令版本 0.12.13，`pyproject.toml` 却允许 `uv_build>=0.12.5,<0.13.0` 在构建时选择不同后端；本机已用缓存的包元数据是 uv-build 0.12.18。本批仅将构建后端要求固定为该精确版本，不改运行依赖、主包版本、源码包含规则或构建入口。

在仓库根目录执行 `uv --cache-dir /private/tmp/lenbot-uv-cache build --sdist --wheel --offline --out-dir /private/tmp/lenbot-backend-pin-review` 退出 0，得到 `len_bot-0.1.0.tar.gz` 与 `len_bot-0.1.0-py3-none-any.whl`。只读核对 sdist 的 `pyproject.toml` 已包含 `uv_build==0.12.18`；wheel 仍有已构建面板入口、日历字体和 B 站 logo，未见前端源码目录。`git diff --check` 退出 0。此构建复用工作区已有前端产物，未在本批从干净检出运行 Node 22、安装 wheel 或触发远端 CI；目标双架构安装、素材授权及发布仍待确认。未运行测试、服务、模型、平台或实发，没有本批业务运行失败原文。

### 固定后端后的主镜像双架构构建

基线 `c5443a6`。在本机 Docker Desktop `desktop-linux` 上，使用该提交分别执行 `docker buildx build --platform linux/arm64 --load -f deploy/linux/Dockerfile -t lenbot:arm64-backend-pin .` 与相同命令的 `linux/amd64`／`lenbot:amd64-backend-pin`，均退出 0。两份日志均显示 Node 22 阶段重新执行 `npm run build`（491 模块）和 Python 3.13 阶段执行 `uv sync --locked --no-dev --no-editable` 并构建当前包；`npm ci` 层使用缓存。`docker image inspect` 只读显示：arm64 镜像 `sha256:b6151a75ee07be965b78c9044d7a0b52e447c573757152bc08515a59b4098c60` 为 `linux/arm64`，amd64 镜像 `sha256:6edd4b3c6aa458134799f38a0b5b623f4cb48086f2d631a0b76c2f01b9ee41ca` 为 `linux/amd64`。这些是本机镜像，不是目标主机安装或运行；amd64 是交叉构建，未启动容器、运行群报告、连接 SnowLuma 或上传文件。workspace worker 未因本批主包构建后端变化重建，仍沿此前的双架构构建记录；权限、素材授权、候选版本和现场回执待确认。
