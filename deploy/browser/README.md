# BrowserSkill 配套程序包

这是 LenBot 的账号浏览配套包，不是宿主安装包。选择与**运行机器**匹配的 Linux／macOS、amd64／arm64 产物；远程部署时服务器和浏览器电脑可以使用不同架构的包，但应采用同一发行版本。

## 包内内容

```text
bin/bsk                    CLI 与 daemon
bin/bsk-file-host          浏览器电脑上的 Native Messaging 文件助手
extension/                 已构建 Chrome MV3 扩展目录
component.json             对应 LenBot 发行号、上游 revision 和补丁信息
browserskill-remote-files.patch
LICENSE                    上游 MIT 许可
licenses/                  本次可取得的依赖许可原件
cargo-metadata.json        Rust 依赖元数据
npm-licenses.json           前端依赖许可元数据
source-docs/               上游远程连接与文件助手说明
```

包解压到最终程序位置后再注册文件助手。不要在临时解压目录注册后又移动程序；注册内容指向实际可执行路径。

## 浏览器电脑

1. 使用专门的 Chrome 用户配置，在扩展管理页开启开发者模式，加载解压后的 `extension/` 目录，并记下扩展 ID。
2. 从包目录执行：

```sh
./bin/bsk-file-host install --extension-id <实际扩展ID>
```

注册使用当前用户的 Chrome Native Messaging 目录，不需要管理员权限。Chromium、Chrome for Testing 或其他资料目录用 `--manifest-directory /实际/NativeMessagingHosts` 指定对应位置。注册不会安装扩展、打开网页或自动连接 daemon。

同机浏览时可使用本机 daemon；远程浏览时，电脑只需扩展和助手，通过扩展中的配对链接连接服务器。人工登录、验证码和账号操作仍在专用浏览器中完成，不把浏览器用户资料拷进 LenBot 实例。

## daemon 所在机器

CLI 与 daemon 使用同一个 OS 用户和服务 home。以下是同机模式的明确启动示例：

```sh
BSK_HOME=/absolute/path/to/browser-home ./bin/bsk daemon start --mode local
```

跨机器连接需要 server 模式、可信 TLS 入口和设备配对，按包内 `source-docs/remote-extension-connection.md`选择原生 TLS 或已有反向代理。不要把同机回环模式当作已接通远程浏览器。

启动 server 后，用相同 home 生成配对链接：

```sh
BSK_HOME=/absolute/path/to/browser-home BSK_AUTO_START=0 ./bin/bsk daemon pair
```

`BSK_HOME`、`BSK_AUTO_START` 是独立 BrowserSkill 服务的参数，不覆盖 LenBot 运行配置。LenBot 根配置中的 `account_browser.binary` 指向这个包的 `bin/bsk`，`home` 与该 daemon 一致，`socket` 填它实际创建的本地 IPC 路径，`device_id` 选择实际配对设备。不要按示例猜 socket 或设备 ID。

LenBot 与 daemon 需要共享本地 IPC。若 LenBot 在容器中，必须给容器提供实际 socket、home 和同平台可执行文件；macOS 的 bsk 二进制不能直接在 Linux 容器中执行。部署位置按实际系统选择，不自动换连接方式。

## 文件与升级

远程文件通过配对连接和本机助手传输，服务器与浏览器电脑不用共享磁盘。上传只表示文件已附加到控件，网页提交是另一动作；下载／截图落盘、LenBot 交付登记与 QQ 上传也分别报告。助手职责和临时文件清理见包内 `source-docs/file-host.md`。

升级前结束相关任务并停止对应 daemon，保留原服务 home 与配对数据，换成同版的 daemon、扩展和助手。扩展加载目录或助手位置改变后重新注册对应扩展 ID；宿主不自动更新独立服务。不要运行上游自动更新命令覆盖本版远程文件扩展。

对应 patched 源码随本次 Release 另提供 `browserskill-<LenBot发行号>-source.tar.gz`。包中 `component.json` 表明上游版本，包名中的 LenBot 发行号不代替上游自己的版本号。Linux 二进制在 Ubuntu 24.04 构建，需要相应 glibc；Windows 原生助手不在本轮发行范围。
