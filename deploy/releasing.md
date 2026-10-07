# 构建候选与发布版本

面向版本维护者。普通安装见[成品部署包](package/README.md)和 [Docker](current/docker.md)；程序运行参数来自实例根配置，以下内容只管构建与发布。

## 版本规则

- 版本号只写在根 `pyproject.toml`，标签为 `v<版本>`。形如 `0.2.0` 是正式版，带 `a`／`b`／`rc` 后缀（如 `0.2.0rc1`）是预发布。
- 发布过的版本和标签不覆盖、不移动。发布出了问题就发下一个补丁版本。
- `0.x` 阶段：补丁版本（`0.2.1`）保持公开接口和数据格式兼容；有破坏性变化时升次版本（`0.3.0`）。`1.0` 之后破坏性变化升主版本。
- 公开接口包括插件接口、根配置、OneBot 接入，以及部署包和 Docker 配方给用户的操作命令。

### 兼容编号

各编号写在源码常量里，构建时记入发行清单 `release-manifest.json`，更新器据此判断能否换版。

| 编号 | 当前 | 位置 |
|---|---|---|
| 插件接口 | 1 | `plugin.py` 的 `INTERFACE` |
| 根配置格式 | 2 | `config.py` 的 `CONFIG_VERSION` |
| 业务数据库 | 3 | `storage/store.py` 的 `FORMAT_VERSION` |
| 记忆处理库 | 6 | `memory/jobs.py` 的 `FORMAT_VERSION` |
| 本地记忆索引 | 3 | `memory/local.py` 的 `FORMAT_VERSION` |
| 更新器协议 | 1 | `deploy/updater/common.py` 的 `PROTOCOL` |

- **插件接口**：同一代里只做兼容扩展；改了已有签名或语义就升代。插件的 `requires_lenbot` 写首个提供所需能力的宿主版本。`0.2.0` 是首个公开基线，开发期的宿主都叫 `0.1.0`，插件写 `>=0.2,<1` 就能把它们排除在外。
- **数据格式**：每次变化加一步迁移，新版本保留它支持范围内的整条升级链。迁移只在停机时执行（面板更新或离线 `install.py upgrade`），之前先做完整快照。旧程序不读新格式，回退只能恢复升级前的快照。
- **新安装**直接建最新结构。新版本加的设置如果没法从旧配置推导，就由用户在面板或配置里填写，迁移不替用户猜。
- **更新器协议**：更新器不替换自己。发行清单的 `updater_protocol` 和已安装的更新器不一致时，面板不换版，恢复页直接给出命令：部署包用新版部署包执行离线 `install.sh upgrade`，它会同时更新程序和更新器；Docker 用新版更新器镜像执行 `init.py --updater-only`，再 `docker compose up -d` 换上新更新器。

### 版本说明与发行清单

- `changelogs/v<版本>.md` 是给人看的版本说明，原样成为 Release 正文；一个版本一个文件，这个目录就是变更记录。第一行必须是 `# LenBot <版本>`，不留「（发布时填写」占位。内容写清升级要求、插件和数据兼容、各平台实测结果与已知问题。
- `release-manifest.json` 给程序读：版本、提交、兼容编号、每个附件的大小和 SHA-256，以及镜像引用（发布时写入不可变摘要）。更新器只认这份清单。
- 清单里的 `revision` 取当前提交；本机构建时如果发行相关源码有未提交的改动，会标成 `<提交>-dirty`。发布作业遇到 `-dirty` 直接拒绝。

## 同一版本的产物

| 产物 | 构建来源 |
|---|---|
| wheel、sdist、Linux／macOS 部署包（tar.gz）、Windows x64 部署包（zip）、发行清单 | `scripts/build_release.py`，三个平台的包装的是同一个 wheel 和同一份依赖清单 |
| 宿主镜像 | `deploy/current/Dockerfile` 的 wheel 构建路径，安装上面同一个 wheel 和依赖清单 |
| 任务镜像 | `docker/next-worker/Dockerfile` |
| 更新器镜像 | `docker/updater/Dockerfile`，Docker 安装里负责换版和恢复 |
| BrowserSkill 四平台包与源码包 | 固定上游提交加远程文件补丁，见 `deploy/components.json` |

三种镜像都在 Linux amd64 和 arm64 原生 runner 上构建，发布时同时推到 GHCR（`ghcr.io/lendevs/lenbot`、`-worker`、`-updater`）和 Docker Hub（`docker.io/lendevs/` 下同名）。浏览器组件在 Linux amd64／arm64、macOS Intel／arm64 上构建；Linux 二进制在 Ubuntu 24.04 构建，需要对应的 glibc。部署包不带 Python 环境，依赖在目标机由 uv 按锁定清单安装。[官方 runner 范围](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。

`components.json` 是服务上游、工具版本和浏览器 Rust 目标三元组的来源。配套源码由 `prepare_component.py` 从固定提交取回再打补丁，不打包维护者本机的检出、登录资料或服务目录。

## 候选构建

工作流推到远端后，对选定分支手动运行：

```sh
gh workflow run release.yml --ref <分支>
```

默认 `publish=false`：不登录镜像仓库，不创建 Release。产物留在这次运行的 artifact 里：`release-packages` 是程序包和发行清单，`browser-*` 是浏览器组件，`image-<组件>-<架构>` 里的 `image.tar` 可以用 `docker load -i image.tar` 加载。[Docker 构建产物跨作业保存](https://docs.docker.com/build/ci/github-actions/share-image-jobs/)。

镜像构建之前先跑 `install-smoke.yml`；推送到 master 或向 master 开 PR 时，CI 也会跑同一组检查：

- 部署包：Linux amd64／arm64、macOS Intel／ARM64、Windows x64 各装一遍，走完首次配置、登录面板、正常停止；再用两个实际打包的版本走一遍面板升级、恢复，以及新版启动失败后的恢复。
- Docker：amd64 和 arm64 各用包内配方初始化、登录、停止；再在临时本地 registry 上走一遍镜像换版和恢复，结束后删掉这次的容器、卷和网络。

构建程序包之前还要通过 `check.yml`（编译、ruff、pytest）和 `official-plugins.yml`：后者按随附插件目录固定的提交检出每个官方插件，用这次的宿主跑插件自己的测试和打包检查。CI 和发行工作流调用的是同一组可复用工作流，检查内容不会两边不一致。

这些检查全程模拟发送，不接 OneBot、不调用模型，也不覆盖原生服务注册和真实任务。

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

工作目录每次用新的。`release` 是明确传入的 [Docker 命名构建上下文](https://docs.docker.com/build/concepts/context/#named-contexts)，只读取其中的 wheel 和 `requirements.txt`；不传 `PACKAGE_SOURCE=wheel` 时从源码构建，用于本地开发。Docker 双版本检查的完整命令见 `install-smoke.yml` 的 `docker` 作业。

单独准备浏览器组件源码：

```sh
uv run --no-sync python scripts/prepare_component.py browserskill /tmp/browserskill-release \
  --source-archive /tmp/browserskill-source.tar.gz
```

## 发布

### 一次性准备

- 仓库变量 `DOCKERHUB_NAMESPACE` 设为 `lendevs`，Secrets 配 `DOCKERHUB_USERNAME` 和 `DOCKERHUB_TOKEN`（Docker Hub 的访问令牌，需要写权限）。凭据不进仓库。
- Actions 需要包写入和 Release 写入权限。GHCR 包第一次上传后默认私有，要在包设置里改成公开，再用未登录的机器 `docker pull` 确认。

### 步骤

1. 写好 `changelogs/v<版本>.md`，在干净的工作区运行 `uv run --no-sync python scripts/prepare_release.py <版本>`。它改版本号、重新锁定依赖、检查版本说明，最后打印提交和打标签的命令，自己不提交、不打标签、不推送。
2. 检查改动后提交，创建标签 `v<版本>` 并推送。推送标签就是实际发布：完整构建、安装检查、上传镜像、创建 Release。

工作流在构建前检查：标签必须和版本号一致；版本说明存在且不是草稿；Docker Hub 命名空间已设置；这个版本的 Release 还不存在。任何一项不满足就停下，不上传任何东西。

预发布版本的 Release 标为预发布，不更新 `latest`。正式版在 Release 完整创建后，才把两个 registry 上三种镜像的 `latest` 指向本版。

### 中途失败

- **上传之前失败**（构建、测试、安装检查）：什么都没发出去。偶发失败可以在同一次运行里 Re-run failed jobs；需要改代码就发下一个补丁版本，已推送的标签不移动。
- **部分镜像已上传后失败**：只能在**同一次运行**里 Re-run failed jobs。成功作业的产物会沿用，已上传的同一镜像会保留。另开一次运行会重新构建镜像，摘要和已上传的不同，推送脚本会拒绝。
- **Release 已创建、`latest` 更新失败**：同样 Re-run failed jobs，已上传的附件内容一致就保留，然后重做别名；也可以手动对两个 registry 执行 `docker buildx imagetools create -t <镜像>:latest <镜像>:<版本>`。
- **已发布的版本发现问题**：发下一个补丁版本，不改已发布的标签、附件和镜像。

上传成功不等于未登录用户能下载，也不能代替实际安装、面板使用、QQ 收发和模型行为的观察。

## 许可

程序与服务各保留原许可。发行附 patched 服务源码、锁文件和实际可取得的依赖声明；BrowserSkill 包保留 Cargo 依赖许可原件、pnpm 许可元数据及扩展直接安装树的许可资料，不能据此声称已逐项核对所有二进制的全部第三方来源。原生系统包和浏览器发行物按镜像内对应声明处理。
