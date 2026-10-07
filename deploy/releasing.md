# 构建候选与发布版本

本文面向版本维护者。普通安装见[成品部署包](package/README.md)和 [Docker](current/docker.md)。程序的运行参数来自实例根配置，本文只说明构建和发布。

## 版本规则

- 版本号只写在根目录的 `pyproject.toml` 中，标签为 `v<版本>`。`0.2.0` 这样的版本是正式版本，带有 `a`／`b`／`rc` 后缀的（例如 `0.2.0rc1`）是预发布版本。
- 已经发布的版本和标签不覆盖，也不移动。发布出现问题时，发布下一个补丁版本。
- 在 `0.x` 阶段，补丁版本（`0.2.1`）保持公开接口和数据格式兼容。有破坏性变化时提升次版本号（`0.3.0`）。`1.0` 之后，破坏性变化提升主版本号。
- 公开接口包括以下几项。
  - 插件接口
  - 根配置
  - OneBot 接入
  - 部署包和 Docker 配置中提供给用户的操作命令

### 兼容编号

各个编号写在源码的常量中，构建时记入发行清单 `release-manifest.json`。更新器根据这些编号判断能否切换版本。

| 编号 | 当前值 | 位置 |
|---|---|---|
| 插件接口 | 1 | `plugin.py` 的 `INTERFACE` |
| 根配置格式 | 3 | `config.py` 的 `CONFIG_VERSION` |
| 业务数据库 | 3 | `storage/store.py` 的 `FORMAT_VERSION` |
| 记忆处理库 | 6 | `memory/jobs.py` 的 `FORMAT_VERSION` |
| 本地记忆索引 | 3 | `memory/local.py` 的 `FORMAT_VERSION` |
| 更新器协议 | 1 | `deploy/updater/common.py` 的 `PROTOCOL` |

#### 插件接口

同一代次内只做兼容的扩展。修改了已有的签名或语义时，提升代次。插件的 `requires_lenbot` 填写首个提供所需功能的宿主版本。`0.2.0` 是首个公开基线，开发期间的宿主版本都是 `0.1.0`，插件填写 `>=0.2,<1` 就能排除这些版本。

#### 数据格式

每次格式变化都增加一步迁移。新版本保留其支持范围内的完整升级链。迁移只在停机时执行（通过面板更新或离线执行 `install.py upgrade`），执行前先制作完整快照。旧程序不读取新格式，回退只能通过恢复升级前的快照完成。

#### 新安装

新安装直接创建最新的结构。新版本增加的设置如果无法从旧配置推导，由用户在面板或配置中填写，迁移不替用户猜测。

#### 更新器协议

更新器不会替换自己。发行清单中的 `updater_protocol` 与已安装的更新器不一致时，面板不切换版本，恢复页直接给出命令。

- 部署包用新版本的部署包执行离线的 `install.sh upgrade`，程序和更新器会一起更新。
- Docker 用新版本的更新器镜像执行 `init.py --updater-only`，再执行 `docker compose up -d` 换上新的更新器。

### 版本说明与发行清单

- `changelogs/v<版本>.md` 是给人阅读的版本说明，原样作为 Release 的正文。每个版本一个文件，这个目录就是变更记录。
  - 第一行必须是 `# LenBot <版本>`，不能保留「（发布时填写」这类占位文字。
  - 内容需要写清升级要求，以及插件和数据的兼容情况。各平台的实测结果和已知问题也要写明。
- `release-manifest.json` 供程序读取，更新器只认这份清单。清单包含以下内容。
  - 版本和提交
  - 兼容编号
  - 每个附件的大小和 SHA-256
  - 镜像引用，发布时写入不可变的摘要
- 清单中的 `revision` 取当前提交。在本机构建时，如果与发行相关的源码有未提交的改动，会标记为 `<提交>-dirty`。发布作业遇到 `-dirty` 时直接拒绝。

## 同一版本的产物

| 产物 | 构建来源 |
|---|---|
| wheel、sdist、Linux／macOS 部署包（tar.gz）、Windows x64 部署包（zip）、发行清单 | `scripts/build_release.py`。三个平台的包安装的是同一个 wheel 和同一份依赖清单 |
| 宿主镜像 | `deploy/current/Dockerfile` 的 wheel 构建路径，安装上面同一个 wheel 和依赖清单 |
| 任务镜像 | `docker/next-worker/Dockerfile` |
| 更新器镜像 | `docker/updater/Dockerfile`，在 Docker 安装中负责切换版本和恢复 |
| BrowserSkill 四平台包与源码包 | 固定的上游提交加上远程文件补丁，见 `deploy/components.json` |

三种镜像都在 Linux amd64 和 arm64 原生 runner 上构建。发布时同时推送到 GHCR（`ghcr.io/lendevs/lenbot`、`-worker`、`-updater`）和 Docker Hub（`docker.io/lendevs/` 下的同名镜像）。

浏览器组件在 Linux amd64／arm64 和 macOS Intel／arm64 上构建。Linux 二进制文件在 Ubuntu 24.04 上构建，需要对应版本的 glibc。部署包不带 Python 环境，依赖在目标机器上由 uv 按锁定的清单安装。参考[官方 runner 范围](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。

`components.json` 记录了服务的上游和工具版本，以及浏览器的 Rust 目标三元组。配套源码由 `prepare_component.py` 从固定的提交取回后再打补丁。维护者本机的检出目录和登录资料不会被打包，服务目录也不会。

## 候选构建

把工作流推送到远端后，对选定的分支手动运行。

```sh
gh workflow run release.yml --ref <分支>
```

默认 `publish=false`，不登录镜像仓库，也不创建 Release。产物保存在这次运行的 artifact 中。

- `release-packages` 是程序包和发行清单。
- `browser-*` 是浏览器组件。
- `image-<组件>-<架构>` 中的 `image.tar` 可以用 `docker load -i image.tar` 加载。参考 [Docker 构建产物跨作业保存](https://docs.docker.com/build/ci/github-actions/share-image-jobs/)。

镜像构建之前先运行 `install-smoke.yml`。推送到 master，或者向 master 发起 PR 时，CI 也会运行同一组检查。

- **部署包**。在 Linux amd64／arm64、macOS Intel／ARM64 和 Windows x64 上各安装一遍，完成首次配置，登录面板，然后正常停止。再用两个实际打包的版本，完整执行一遍面板升级和恢复，以及新版本启动失败后的恢复。
- **Docker**。在 amd64 和 arm64 上各用包内的配置完成初始化，登录后停止。再在临时的本地 registry 上完整执行一遍镜像切换和恢复，结束后删除这次创建的容器、卷和网络。

构建程序包之前，还需要通过以下两项检查。

- `check.yml`，包括编译、ruff 和 pytest。
- `official-plugins.yml`。它按随附插件目录中固定的提交检出每个官方插件，用这次构建的宿主运行插件自己的测试和打包检查。

CI 和发行工作流调用的是同一组可复用工作流，因此两边的检查内容保持一致。

这些检查全程使用模拟发送，不连接 OneBot，不调用模型，也不覆盖原生服务注册和真实任务。

### 本机构建与检查

```sh
uv run --no-sync python scripts/build_release.py /tmp/lenbot-release
docker build --build-arg PACKAGE_SOURCE=wheel --build-context release=/tmp/lenbot-release/artifacts \
  -f deploy/current/Dockerfile -t lenbot-current:candidate .
# 本平台部署包：安装到新目录，经首次配置向导保存配置，登录后停止
python3 scripts/smoke_install.py package /tmp/lenbot-release/artifacts /tmp/lenbot-smoke
# 宿主镜像：包内 Compose 配方加 first-setup.example.json，在临时卷上离线初始化
python3 scripts/smoke_install.py docker /tmp/lenbot-release/artifacts /tmp/lenbot-smoke-docker --image lenbot-current:candidate
# 原生双版本升级与恢复
uv run --no-project --python 3.13 python scripts/smoke_update.py /tmp/lenbot-release/artifacts /tmp/lenbot-update
```

每次都使用新的工作目录。`release` 是明确传入的 [Docker 命名构建上下文](https://docs.docker.com/build/concepts/context/#named-contexts)，构建只读取其中的 wheel 和 `requirements.txt`。不传 `PACKAGE_SOURCE=wheel` 时从源码构建，用于本地开发。Docker 双版本检查的完整命令见 `install-smoke.yml` 中的 `docker` 作业。

单独准备浏览器组件的源码时，执行下面的命令。

```sh
uv run --no-sync python scripts/prepare_component.py browserskill /tmp/browserskill-release \
  --source-archive /tmp/browserskill-source.tar.gz
```

## 发布

### 一次性准备

- 把仓库变量 `DOCKERHUB_NAMESPACE` 设为 `lendevs`，在 Secrets 中配置 `DOCKERHUB_USERNAME` 和 `DOCKERHUB_TOKEN`（Docker Hub 的访问令牌，需要写权限）。凭据不提交到仓库。
- Actions 需要包的写入权限和 Release 的写入权限。GHCR 的包第一次上传后默认是私有的，需要在包设置中改为公开，再在未登录的机器上用 `docker pull` 确认。

### 步骤

1. 写好 `changelogs/v<版本>.md`，在干净的工作区中运行 `uv run --no-sync python scripts/prepare_release.py <版本>`。脚本会修改版本号，重新锁定依赖，检查版本说明，最后打印提交和打标签的命令。脚本本身不提交，不打标签，也不推送。
2. 检查改动后提交，创建标签 `v<版本>` 并推送。推送标签即开始实际发布。工作流依次执行完整构建和安装检查，然后上传镜像并创建 Release。

工作流在构建前检查以下条件。任何一项不满足时，工作流停止，不上传任何内容。

- 标签与版本号一致。
- 版本说明存在，并且不是草稿。
- Docker Hub 命名空间已经设置。
- 这个版本的 Release 尚不存在。

预发布版本的 Release 会标记为预发布，不更新 `latest`。正式版本的 Release 完整创建后，才把两个 registry 上三种镜像的 `latest` 指向这个版本。

### 中途失败

- **上传之前失败**（构建、测试、安装检查）。此时没有发布任何内容。偶发的失败可以在同一次运行中使用 Re-run failed jobs。需要修改代码时，发布下一个补丁版本，已推送的标签不移动。
- **部分镜像已经上传后失败**。只能在**同一次运行**中使用 Re-run failed jobs。成功作业的产物会沿用，已上传的同一镜像会保留。另外开始一次运行会重新构建镜像，摘要与已上传的不同，推送脚本会拒绝。
- **Release 已创建，`latest` 更新失败**。同样使用 Re-run failed jobs。已上传的附件内容一致时会保留，然后重新创建别名。也可以手动对两个 registry 执行 `docker buildx imagetools create -t <镜像>:latest <镜像>:<版本>`。
- **已发布的版本发现问题**。发布下一个补丁版本，已发布的标签和附件不做修改，镜像也不修改。

上传成功不等于未登录的用户能够下载。上传成功也不能代替实际的安装和面板使用，以及对 QQ 收发和模型行为的观察。

## 许可

程序和服务各自保留原有的许可。发行时附带打过补丁的服务源码和锁文件，以及实际可以取得的依赖声明。BrowserSkill 包保留以下许可资料。

- Cargo 依赖的许可原件
- pnpm 的许可元数据
- 扩展直接安装树中的许可资料

这些资料不能作为已经逐项核对所有二进制文件全部第三方来源的依据。原生系统包和浏览器发行物按镜像中对应的声明处理。
