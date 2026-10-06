# 构建候选与发布版本

面向版本维护者。普通安装见[成品部署包](package/README.md)，程序运行参数仍来自实例根配置；以下参数仅控制构建与发布。

## 同一版本的产物

版本号取根 `pyproject.toml`；公开标签必须为 `v<该版本号>`。工作流读取同一提交，不自动改版本、创建标签或挑选其他分支。

| 产物 | 构建来源 |
|---|---|
| wheel、sdist、Linux／macOS 部署包 | `scripts/build_release.py`，两平台包采用同一 wheel |
| 宿主镜像 | `deploy/current/Dockerfile` 的 wheel 构建路径，直接安装上述同一 wheel 与依赖清单 |
| 任务镜像 | `docker/next-worker/Dockerfile`，同提交的桥接和浏览器文件协议 |
| BrowserSkill 四平台包与源码包 | 固定上游提交及远程文件补丁，分别包含 bsk、文件助手、扩展及许可材料 |

宿主／任务镜像采用 Linux amd64 和 arm64 原生 runner 构建。浏览器组件使用 Linux amd64／arm64、macOS Intel／arm64 runner；Linux 二进制在 Ubuntu 24.04 构建，运行系统需要对应的 glibc，不将其声明为任意 Linux 发行版通用静态包。Python 平台包的依赖在目标机由 uv 安装，不包含一个预装的跨平台 Python 环境。[官方 runner 范围](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。

`components.json` 是服务上游、工具版本和浏览器 Rust 目标三元组来源。工作流按平台选择明确 target，打包只读取 `target/<triple>/release` 下的对应二进制，不从宿主默认目录取文件后仅改平台标签。配套源码由 `prepare_component.py` 从固定提交取回后应用补丁，不打包维护者的本机检出、登录资料或服务目录。版本号是 LenBot 配套发行号，不冒充上游自己的软件版本。

## 候选构建：默认不发布

在工作流已进入远端后，对选定分支或标签手动运行：

```sh
gh workflow run release.yml --ref <分支或标签> -f publish=false
```

`publish=false` 不登录镜像仓库、不创建 Release：包文件保存在 `release-packages`、浏览器二进制在 `browser-*`，各架构镜像在 `image-*-*` artifact 的 `image.tar` 中。下载后 `docker load -i image.tar` 即可加载实际候选镜像。artifact 名称区分组件与架构，不依赖加载时猜测。[Docker 构建产物跨作业保存](https://docs.docker.com/build/ci/github-actions/share-image-jobs/)。

同次构建同时导出根 `uv.lock` 的运行依赖清单 `requirements.txt`。平台部署包与发行宿主镜像共同安装这份清单，不在安装时重新挑选允许范围内的最新依赖；插件依赖仍由显式安装／恢复动作处理。

本机构建程序包仍使用：

```sh
uv run --no-sync python scripts/build_release.py /tmp/lenbot-release
```

它不执行 GitHub 工作流或构建全部镜像。用这次产物构建宿主镜像：

```sh
docker build --build-arg PACKAGE_SOURCE=wheel \
  --build-context release=/tmp/lenbot-release/artifacts \
  -f deploy/current/Dockerfile -t lenbot-current:candidate .
```

`release` 是明确传入的 [Docker 命名构建上下文](https://docs.docker.com/build/concepts/context/#named-contexts)，只读取其中的 wheel 和 `requirements.txt`。默认未传该构建参数时仍从源码构建，用于本地开发；两种路径明确选择，不因失败自动切换。

独立准备浏览器组件源码：

```sh
uv run --no-sync python scripts/prepare_component.py browserskill /tmp/browserskill-release \
  --source-archive /tmp/browserskill-source.tar.gz
```

每次使用新目录和输出文件；失败保留原错，不自动换 revision 或改锁文件重试。

## 预发布与正式版

完成本版集中使用后，明确选择版本、更新 `pyproject.toml` 与 uv 锁文件、整理 `deploy/release-notes.md`，提交后再创建对应标签。推送 `v*` 标签会执行完整构建并发布**预发布版本**；这就是实际发布动作，不是候选检查。

当前版本暂为 `0.1.0`，运行修复分支不创建标签或触发构建。CI 与发行工作流共用 `build_release.py` 构建面板、源码包、wheel 与 Linux／macOS 部署包，产物保存在 `release-packages`；CI 仍保留测试步骤。升级顺序包括新的本地记忆索引迁移，见[升级与备份](current/operations.md#升级与备份)。

工作流向 GHCR 上传 `ghcr.io/<owner>/<repo>:<version>`和 `<repo>-worker:<version>`，先有架构标签，再组合对应版本的多架构清单，不更新 `latest`。所有构建作业成功后才创建 Release 并附包、源码、组件清单与镜像位置。标签必须已存在；发布命令使用 `--verify-tag`，不让工具隐式在默认分支创建标签。[GitHub Release 命令](https://cli.github.com/manual/gh_release_create)。

手动发布只对同版已存在标签使用：

```sh
gh workflow run release.yml --ref v<版本> -f publish=true -f prerelease=true
```

已有预发布根据后续本机结果转正式版时，不重建或覆盖标签，明确执行 `gh release edit v<版本> --prerelease=false`。若确实要第一次直接创建正式版，手动入口可选 `prerelease=false`；本轮仍按先预发布、再正式版推进。

GitHub 仓库需要 Actions 的包写入与 Release 写入权限；GHCR 包的公开可见性按仓库实际设置确认，不把上传成功自动等同于未登录用户可下载。中途失败可能已上传部分架构镜像，但不会回滚或伪造整体完成；检查失败作业后明确重跑。工作流本身不能替代实际安装、页面使用、QQ 回执或模型行为观察。

## 许可与当前验证范围

程序与服务各保留原许可。发行附 patched 服务源码、锁文件和实际可取得的依赖声明；BrowserSkill 包保留 Cargo 依赖许可原件、pnpm 许可元数据及扩展直接安装树的许可资料，不能据此声称已逐项核对所有二进制的全部第三方来源。原生系统包和浏览器发行物按镜像内对应声明处理。

本机已核对浏览器固定基线可以应用本版补丁并生成源码包；工作流通过静态语法检查。macOS ARM64／Intel 与 Ubuntu 24.04 amd64／arm64 四组浏览器配套包已从同一 patched 源码完成 CLI、助手和扩展构建及打包，并核对包内二进制架构。macOS Intel 采用交叉编译，Linux amd64 通过本机 Docker 跨架构构建；全平台远端构建、镜像上传与公开 Release 尚未执行。未安装扩展或注册助手，平台矩阵不等同于运行通过。
