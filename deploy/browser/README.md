# BrowserSkill 配套程序包

这是 LenBot 账号浏览功能的配套包，宿主程序需要另外安装。请按**运行机器**的系统和架构选择产物，系统为 Linux 或 macOS，架构为 amd64 或 arm64。远程部署时，服务器和浏览器所在的电脑可以使用不同架构的包，但应使用同一个发行版本。

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

请把包解压到最终的程序位置后，再注册文件助手。注册内容指向可执行文件的实际路径，因此不要在临时解压目录中注册后再移动程序。

## 浏览器所在的电脑

1. 使用一个专用的 Chrome 用户配置，在扩展管理页开启开发者模式，加载解压后的 `extension/` 目录，记下扩展 ID。
2. 在包目录中执行下面的命令。

```sh
./bin/bsk-file-host install --extension-id <实际扩展ID>
```

注册使用当前用户的 Chrome Native Messaging 目录，不需要管理员权限。使用 Chromium、Chrome for Testing 或其他用户资料目录时，用 `--manifest-directory /实际/NativeMessagingHosts` 指定对应的位置。注册不会安装扩展，不会打开网页，也不会自动连接 daemon。

同机浏览时，可以使用本机的 daemon。远程浏览时，电脑上只需要扩展和文件助手，通过扩展中的配对链接连接服务器。人工登录和验证码仍然在专用浏览器中完成，账号操作也一样。浏览器的用户资料不会复制到 LenBot 实例中。

## daemon 所在的机器

CLI 和 daemon 使用同一个操作系统用户和同一个服务 home 目录。下面是同机模式的启动示例。

```sh
BSK_HOME=/absolute/path/to/browser-home ./bin/bsk daemon start --mode local
```

跨机器连接需要以下三项。

- server 模式
- 可信的 TLS 入口
- 设备配对

请按包内 `source-docs/remote-extension-connection.md` 的说明，选择原生 TLS 或已有的反向代理。同机回环模式能够运行，并不代表远程浏览器已经接通。

启动 server 后，用同一个 home 目录生成配对链接。

```sh
BSK_HOME=/absolute/path/to/browser-home BSK_AUTO_START=0 ./bin/bsk daemon pair
```

`BSK_HOME` 和 `BSK_AUTO_START` 是独立的 BrowserSkill 服务的参数，不会覆盖 LenBot 的运行配置。LenBot 根配置中的 `account_browser` 按下面的方式填写。

- `binary` 指向这个包中的 `bin/bsk`。
- `home` 与这个 daemon 使用的目录一致。
- `socket` 填写 daemon 实际创建的本地 IPC 路径。
- `device_id` 选择实际配对的设备。

请不要按示例猜测 socket 路径或设备 ID。

LenBot 和 daemon 需要共享本地 IPC。如果 LenBot 运行在容器中，必须为容器提供实际的 socket 和 home 目录，以及同一平台的可执行文件。macOS 的 bsk 二进制文件不能直接在 Linux 容器中执行。部署位置按实际系统选择，连接方式不会自动切换。

## 文件与升级

远程文件通过配对连接和本机的文件助手传输，服务器和浏览器所在的电脑不需要共享磁盘。上传成功只表示文件已经附加到页面控件上，提交网页是另一个动作。下载和截图的保存会分别报告，LenBot 的交付登记和上传到 QQ 也会分别报告。文件助手的职责和临时文件的清理方式见包内的 `source-docs/file-host.md`。

升级前，请结束相关的任务并停止对应的 daemon。保留原来的服务 home 目录和配对数据，然后换成同一版本的 daemon、扩展和文件助手。扩展的加载目录或文件助手的位置发生变化后，需要为对应的扩展 ID 重新注册。宿主不会自动更新这项独立服务。请不要运行上游的自动更新命令，否则会覆盖这个版本的远程文件扩展。

对应的打过补丁的源码随本次 Release 另外提供，文件名为 `browserskill-<LenBot发行号>-source.tar.gz`。包中的 `component.json` 标明了上游版本，包名中的 LenBot 发行号不能代替上游自己的版本号。Linux 二进制文件在 Ubuntu 24.04 上构建，需要相应版本的 glibc。Windows 原生文件助手不在本轮发行范围内。
