# 账号浏览的配套文件服务

BrowserSkill 在专用环境独立部署。LenBot 保存服务扩展补丁与构建方式，不把上游仓库或浏览器配置复制进宿主。

## 对应版本

- 上游：[Tencent/BrowserSkill](https://github.com/Tencent/BrowserSkill)，基线 `147727a0e2ded65a7d0f364beea8cc2ebcdec8af`。
- 本地扩展分支：`codex/remote-files`，提交 `fa718074bc2f795cb5d3f726977dc6da6f34d41a`。
- 对应补丁：[browserskill-remote-files.patch](browserskill-remote-files.patch)。包含 daemon、扩展、Native Messaging 文件助手及协议 schema。
- 本轮构建使用 Rust 1.99.0、Node 26.8.2、pnpm 10.17.0；Rust 与前端依赖使用各自锁文件。

这一服务扩展与 LenBot 任务文件工具、会话面板已接通。安装此服务不会自动改变宿主配置，根配置仍明确指定 daemon 的 socket、home、binary 与实际设备 ID。

发行组件提交与工具版本集中在 [`components.json`](../components.json)，自动打包路径见[发行说明](../releasing.md)。

已构建包的实际目录和安装操作见[账号浏览成品说明](../browser/README.md)；下面是源码构建入口，不要求成品用户再次安装 Rust／Node。

## 构建

在单独的构建目录展开上游基线，再应用补丁；`LENBOT_SOURCE` 指向当前 LenBot 源码，`BSK_SOURCE` 指向上游 Git 仓库，`BUILD` 为新建空目录。

```sh
git -C "$BSK_SOURCE" archive 147727a0e2ded65a7d0f364beea8cc2ebcdec8af | tar -x -C "$BUILD"
git -C "$BUILD" apply "$LENBOT_SOURCE/deploy/current/browserskill-remote-files.patch"
cd "$BUILD"
rustup toolchain install 1.99.0 --profile minimal
cargo +1.99.0 build --locked --release -p bsk -p bsk-file-host
pnpm install --frozen-lockfile
pnpm --filter @browser-skill/extension build
```

产物是 `target/release/bsk`、`target/release/bsk-file-host` 和 `apps/extension/dist/chrome-mv3/`。分别部署 daemon 和浏览器端文件助手，扩展加载对应目录；构建机器的系统与 CPU 决定二进制目标。本轮发行提供 macOS／Linux，Windows 原生助手产物随后续原生支持安排。

## 浏览器电脑

将助手放到最终程序目录，加载对应扩展并取得扩展 ID，然后执行：

```sh
/path/to/bsk-file-host install --extension-id <扩展ID>
```

这条命令为当前用户的 Google Chrome 写入 `com.browserskill.files` Native Messaging 注册，绑定实际可执行文件路径与该扩展 ID。Chromium、Chrome for Testing 或非默认用户资料目录通过 `--manifest-directory` 指定对应 `NativeMessagingHosts` 目录。移动助手或更换扩展 ID 后重新执行注册命令。

服务端继续用原有 server 模式和配对连接；同机模式仍用本地路径，server 模式明确使用字节传输。缺少助手时原错误结束文件动作，不换模式或再次点击。

## LenBot 任务工具

账号任务的 `account_browser` 支持：

- `upload`：`params` 指定实际 `ref` 或 `selector`；顶层 `files` 列出当前任务的 `{"scope":"inputs","path":"brief.pdf"}` 或 `{"scope":"workspace","path":"out/report.csv"}`。可在 `params` 指定 `tab_id`、`timeout_ms` 和 `mode`（`input`／`drop`）。
- `download`：`params` 指定下载控件，宿主取回完成字节并保存为 `out/browser/download-*/实际文件名`。
- `screenshot`：顶层 `save=true` 将截图保留为 `out/browser/screenshot-*/page.png`；图像工作模型还收到图片，纯文本模型收到文件信息。

文件结果包含当前任务资源引用、实际大小和 MIME、操作来源页面。任务详情提供浏览器会话、按需连接查询、人工接手、浏览器文件与终态遗留会话关闭入口；配对和全局设置仍在能力页。文件选择、页面提交、任务落盘、登记与 QQ 发送分别表达。

## 文件语义与生命周期

上传依次经过任务输入、daemon 暂存、扩展连接和助手暂存，再附加到真实控件。附加不等于网页提交；助手保留上传文件到会话结束，CLI 可先释放 daemon 副本。

下载沿 Chrome 原生捕获；助手只导入该次 `BrowserSkill/tr_*` 目录中的完成文件。字节以 512 KiB 分块回传，完成后清理助手副本，调用方通过原 `transfer.read` 取得 daemon 副本。正常会话关闭还会释放上传和未完成传输；Native Messaging 输入关闭时，助手清理剩余暂存。文件内容不进入工具结果文本。

当前已核对真实 daemon 的远程授权、WebSocket／IPC／Native Messaging 分块字节与清理，以及助手的跨会话归属、完成状态和下载目录边界。LenBot 已接通任务上传、下载、截图落盘、资源登记与会话展示；对应浏览器页面操作与发行产物按集中阶段完成。

BrowserSkill 与其文件助手按原 MIT 许可独立提供。发行时附该组件的对应源码、补丁、许可证与实际依赖声明；版本产物不包含配对凭据、浏览器资料或业务文件。
