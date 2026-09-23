# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 `feat/s0-product-contract`，基线 `f43a1ee`，开始时工作区干净；上一批 S1-02 请求材料清单按任务卡源码范围转待复核，实际调用和页面尚未人工验收。
- S5-01／首个已选 Linux 容器群报告与文件交付方向：主镜像和可选工作空间 worker 的基础镜像仍为可变标签，同一源码稍后构建可能取得不同底层系统。仅固定这两条本轮相关 Dockerfile 的官方多架构索引；不改模型、业务配置、镜像 tag 操作流程或生产环境。

## 本批交付与核对

`deploy/linux/Dockerfile` 的 Node 22 与 Python 3.13 三个阶段，以及 `containers/workspace/Dockerfile`，从现有标签改为对应 `@sha256` 多架构索引。2026-09-23 只读查询官方镜像仓库得到 node:22-slim 索引 `sha256:48e4b67d85f87bd551df43704e24d252f56cc5f8e9718841aace50f19948f0f9`、python:3.13-slim 索引 `sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0`。多架构索引不等于固定目标机器平台；当前对应 Python 基础系统为 trixie，旧系统差异与升级须按实际目标核对。browser／media worker 本批不改，apt 仓库与 workspace 的 pip 包也未锁定，不宣称全链字节一致。

本机 Docker Desktop `desktop-linux` 的 Linux/arm64 builder 在当前工作树完成两个**构建而非启动**：`docker compose -f deploy/linux/compose.yaml build lenbot` 退出 0，镜像 `sha256:68b3b4424d8ab9e1661e20baf21e4a633c2b03a57bad523716ea0703dae40f87`；`docker build -f containers/workspace/Dockerfile -t lenbot-workspace:local .` 退出 0，镜像 `sha256:310275b27284538a6d816a90b432dd8075b803913d2f52b461d9beb553a276d3`。主镜像内 `npm ci`、491 模块前端构建和 `uv sync --locked --no-dev --no-editable` 均由配方完成；worker 安装本次取得的字体及绘图包。没有运行镜像、读取运行配置、触发工作或上传文件；本机 arm64 构建不代替目标 Linux/amd64、OneBot、模型及群回执验收。

- 首次沙箱只读镜像查询失败原文：`Head "https://registry-1.docker.io/v2/library/node/manifests/22-slim": dial tcp: lookup registry-1.docker.io: no such host`；获准只读访问后取得上列索引。首次沙箱探查 Docker 服务失败原文：`permission denied while trying to connect to the docker API at unix:///Users/len5010/.docker/run/docker.sock`；获准仅构建后两个构建均成功。这些是访问边界，不是业务运行失败。
- 更新[Linux 配方](../deploy/linux/README.md)、[运行手册](operations.md)、[支持边界](support.md)和路线的镜像可变性说明。`git diff --check` 退出 0；构建产物与本机镜像均不入 Git。未运行测试、夹具、断言探针、自动截图、回放、故障注入、覆盖率、服务、模型／平台调用或实发；未读取真实配置和业务库。

## 待决定与接续

1. S5-01 仍实施中：精确首个支持组合、目标 Linux 架构／运行时、OneBot 文件协议和版本、空环境同版安装与报告／文件真实回执待明确或人工正常验收。基础索引固定不决定发布版本，也不授权替换线上镜像。
2. S2 仍仅 `source_window_only`，原生交换及必要回复片段的保存边界待维护者答复；基础摘要与压缩交接未完成。S1 源码已交付的部分仍需同版复核，不由容器构建追认。
3. 数据分类保留期限、代码／素材授权、精确支持版本、S6 远端 CI／升级和 S7 外部使用者闭环仍待相应决定与证据；无生产或发布授权。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
