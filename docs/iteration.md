# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 `feat/s0-product-contract`，基线 `81df83a`，开始时工作区干净；上一批固定首选容器路径的基础镜像索引并完成本机 Linux/arm64 构建，未运行镜像或验收实际群聊。
- S5-01／可选工作空间 worker：Dockerfile 原本在构建时安装未限定版本的 `numpy`、`pandas`、`matplotlib` 和 `pillow`，连间接包也随时间漂移。保留当前 Python 基础镜像与执行入口，用同一份明确的依赖文件固定实际解析版本；不改主服务、执行协议、真实配置或生产镜像。

## 本批交付与核对

新增 `containers/workspace/requirements.in` 记录四项直接依赖，`requirements.txt` 固定 Python 3.13 通用解析的 13 项依赖（其中 `tzdata` 仅按平台标记适用）；Dockerfile 改为从这份清单安装，不再在构建时重新选择包版本。锁定的是发行版本，不是 wheel 文件哈希；`fonts-noto-cjk` 的 apt 仓库、pip 源内容及其他可选 worker 仍未固定，不能称全链字节一致。

本机 `uv 0.12.13` 编译依赖清单时，首个沙箱调用因默认缓存目录权限失败：`failed to open file /Users/len5010/.cache/uv/sdists-v9/.git: Operation not permitted (os error 1)`；换用临时缓存后因沙箱 DNS 失败：`Failed to fetch: https://pypi.org/simple/pillow/`，下层原文为 `failed to lookup address information: nodename nor servname provided, or not known`。获准只读取依赖源元数据后，`uv pip compile --python-version 3.13 --universal` 退出 0，解析 13 项。它们是构建访问边界，不是工作执行失败。

本机 Docker Desktop `desktop-linux` 的 Linux/arm64 builder 执行 `docker build -f containers/workspace/Dockerfile -t lenbot-workspace:local .`，退出 0，镜像 `sha256:32e0dfeeeba820e47532a10d22eb54ccd7ae32bca5613011625784e6adc1aa69`。Linux/amd64 交叉构建 `docker buildx build --platform linux/amd64 --load -f containers/workspace/Dockerfile -t lenbot-workspace:amd64 .` 也退出 0，镜像 `sha256:fbe931d1ac859c86f4711c0495c3b138e11e9632c25167b41f6db4e59f638d3b`。两次构建日志均显示对应 12 项 Linux 依赖安装成功，`tzdata` 的非 Linux 标记未安装；没有运行 worker 或 Python 工作。

同一工作树的 Linux/amd64 主服务 `docker buildx build --platform linux/amd64 --load -f deploy/linux/Dockerfile -t lenbot:amd64 .` 退出 0，镜像 `sha256:e986a6ade22f218af5c9e59372cad8f27b8f6f59ff76c6c5a489e629274e7478`，其中 `npm ci`、491 模块前端构建与 `uv sync --locked --no-dev --no-editable` 完成；上批 Linux/arm64 主镜像 `sha256:68b3b4424d8ab9e1661e20baf21e4a633c2b03a57bad523716ea0703dae40f87` 没有因本批 worker 依赖变更而重建。`docker image inspect` 只读确认四个本机标签分别为预期的 linux/arm64／linux/amd64。amd64 是本机交叉构建，不是目标 amd64 主机正常运行验收。

维护者本批确定首个方向为本机 SnowLuma、目标兼容 Linux/amd64 与 Linux/arm64，并明确不按 SnowLuma 具体发行版本设公开支持名单，按所选 OneBot 协议和文件动作核对。根配置既有 `onebot_file_upload.version` 仍须填写当前连接报告的实际版本并复核只读挂载；它是部署身份事实，不是公开兼容版本名单。本批未读取运行连接或真实配置，也没有取得实际 SnowLuma 版本和文件回执；不因构建成功宣称协议兼容已验收。

- 同步[Linux 配方](../deploy/linux/README.md)、[运行手册](operations.md)、[支持边界](support.md)、[发布记录模板](../deploy/linux/release-evidence.template.md)和路线状态，修正路线末尾 S1-02 的旧状态文案；没有因依赖固定把 S5-01 标为待复核或已验收。
- `git diff --check` 退出 0；不将本机镜像、缓存或构建产物入 Git。未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；未启动服务、调用模型／平台、读取真实配置／业务库或实发。

## 待决定与接续

1. S5-01 首选路径已选本机 SnowLuma 及 Linux/amd64、Linux/arm64 双架构，四个本机构建完成。目标系统与运行时、实际连接的 OneBot 协议／动作、模型职责、字体／素材来源、空环境同版安装和真实报告／文件回执仍待按授权确认；不以 SnowLuma 版本名单替代协议及现场回执。
2. S2 仍仅 `source_window_only`；原生交换及必要回复片段的保存边界待维护者答复，摘要基线与压缩交接未完成。S1 已交付源码仍待同版复核。
3. 数据分类保留期限、代码／素材授权、公开支持承诺、S6 候选与外部插件迁移及 S7 外部使用者闭环仍待相应决定或证据；无生产或发布授权。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
