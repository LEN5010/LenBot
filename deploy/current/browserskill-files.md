# 账号浏览的配套文件服务

BrowserSkill 在专用环境中独立部署。LenBot 仓库只保存服务的扩展补丁和构建方法，不把上游仓库或浏览器配置复制进宿主。

## 对应版本

- 上游为 [Tencent/BrowserSkill](https://github.com/Tencent/BrowserSkill)，基线提交 `147727a0e2ded65a7d0f364beea8cc2ebcdec8af`。
- 本地扩展分支为 `codex/remote-files`，提交 `fa718074bc2f795cb5d3f726977dc6da6f34d41a`。
- 对应的补丁是 [browserskill-remote-files.patch](browserskill-remote-files.patch)。补丁包含以下部分。
  - daemon
  - 浏览器扩展
  - Native Messaging 文件助手
  - 协议 schema
- 本轮构建使用 Rust 1.99.0、Node 26.8.2 和 pnpm 10.17.0。Rust 依赖和前端依赖分别使用各自的锁文件。

这项服务扩展已经与 LenBot 的任务文件工具和会话面板接通。安装这项服务不会自动修改宿主配置。根配置中仍然需要明确指定 daemon 的 socket 和 home，以及 binary 和实际的设备 ID。

各发行组件的提交和工具版本集中记录在 [`components.json`](../components.json) 中，自动打包的流程见[发行说明](../releasing.md)。

已构建的成品包的目录结构和安装步骤见[账号浏览成品说明](../browser/README.md)。下文介绍如何从源码构建，使用成品包的用户不需要再安装 Rust 或 Node。

## 构建

在单独的构建目录中展开上游基线，再应用补丁。`LENBOT_SOURCE` 指向当前的 LenBot 源码，`BSK_SOURCE` 指向上游 Git 仓库，`BUILD` 是一个新建的空目录。

```sh
git -C "$BSK_SOURCE" archive 147727a0e2ded65a7d0f364beea8cc2ebcdec8af | tar -x -C "$BUILD"
git -C "$BUILD" apply "$LENBOT_SOURCE/deploy/current/browserskill-remote-files.patch"
cd "$BUILD"
rustup toolchain install 1.99.0 --profile minimal
cargo +1.99.0 build --locked --release -p bsk -p bsk-file-host
pnpm install --frozen-lockfile
pnpm --filter @browser-skill/extension build
```

构建产物有三项。

| 产物 | 用途 |
|---|---|
| `target/release/bsk` | daemon |
| `target/release/bsk-file-host` | 浏览器一侧的文件助手 |
| `apps/extension/dist/chrome-mv3/` | 浏览器扩展，在浏览器中加载这个目录 |

daemon 和文件助手需要分别部署。二进制文件的目标平台由构建机器的系统和 CPU 决定。本轮发行提供 macOS 和 Linux 版本。Windows 原生文件助手会随后续的 Windows 原生支持一起安排。

## 浏览器所在的电脑

把文件助手放到最终的程序目录，在浏览器中加载对应的扩展并获取扩展 ID，然后执行下面的命令。

```sh
/path/to/bsk-file-host install --extension-id <扩展ID>
```

这条命令为当前用户的 Google Chrome 写入 `com.browserskill.files` 的 Native Messaging 注册信息，并绑定可执行文件的实际路径和这个扩展 ID。使用 Chromium、Chrome for Testing 或非默认的用户资料目录时，通过 `--manifest-directory` 指定对应的 `NativeMessagingHosts` 目录。移动文件助手或更换扩展 ID 后，需要重新执行注册命令。

服务端继续使用原有的 server 模式和配对方式连接。同机模式仍然使用本地路径，server 模式明确使用字节传输。缺少文件助手时，文件操作以原始错误结束，不会切换模式，也不会再次点击。

## LenBot 任务工具

账号任务的 `account_browser` 支持以下操作。

- `upload`。在 `params` 中指定实际的 `ref` 或 `selector`。顶层的 `files` 列出当前任务中的文件，例如 `{"scope":"inputs","path":"brief.pdf"}` 或 `{"scope":"workspace","path":"out/report.csv"}`。还可以在 `params` 中指定 `tab_id` 和 `timeout_ms`，以及 `mode`（`input`／`drop`）。
- `download`。在 `params` 中指定下载控件。宿主取回下载完成的文件，保存为 `out/browser/download-*/实际文件名`。
- `screenshot`。顶层设置 `save=true` 时，截图保存为 `out/browser/screenshot-*/page.png`。支持图像的模型还会收到图片，纯文本模型收到文件信息。

文件操作的结果包含当前任务的资源引用和实际大小，以及 MIME 类型和操作所在的页面。

任务详情中提供以下入口。

- 浏览器会话
- 按需查询连接状态
- 人工接手
- 浏览器文件
- 关闭任务结束后遗留的会话

配对和全局设置仍然在能力页。文件选择和页面提交在结果中分开表示。保存到任务和登记也分开表示，发送到 QQ 同样单独表示。

## 文件的含义与生命周期

上传的文件依次经过任务输入和 daemon 暂存，再经过扩展连接和文件助手暂存，最后附加到页面上的真实控件。附加文件并不等于提交了网页。文件助手会保留上传的文件，直到会话结束。CLI 可以提前释放 daemon 中的副本。

下载沿用 Chrome 原生的下载捕获。文件助手只导入本次下载对应的 `BrowserSkill/tr_*` 目录中已完成的文件。文件内容以 512 KiB 为单位分块回传，传完后清理文件助手中的副本，调用方通过原有的 `transfer.read` 获取 daemon 中的副本。会话正常关闭时，还会释放上传的文件和未完成的传输。Native Messaging 的输入关闭时，文件助手会清理剩余的暂存文件。文件内容不会出现在工具结果的文本中。

目前已经在真实的 daemon 上核对过以下内容。

- 远程授权
- WebSocket、IPC 和 Native Messaging 三条通道上的分块传输与清理
- 文件助手的跨会话归属、完成状态和下载目录边界

LenBot 一侧已经接通以下功能。

- 任务的上传和下载
- 截图保存
- 资源登记
- 会话展示

浏览器页面上的对应操作和发行产物，按集中阶段完成。

BrowserSkill 及其文件助手按原有的 MIT 许可单独提供。发行时会附带这个组件的对应源码和补丁，以及许可证和实际的依赖声明。发行产物不包含配对凭据和浏览器资料，也不包含业务文件。
