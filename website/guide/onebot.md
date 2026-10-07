# 接入 QQ（OneBot）

LenBot 不登录 QQ。它连接一个**已经登录 QQ、开着 OneBot v11 WebSocket** 的程序，通过它收消息、发消息。常见的实现有 NapCat、LLOneBot 等，安装和登录方法看它们各自的文档。

## 两种连接方式

| 方式 | 谁连谁 | 填什么 |
|---|---|---|
| 正向 WebSocket | LenBot 去连 OneBot | OneBot 的地址，例如 `ws://127.0.0.1:3001` |
| 反向 WebSocket | OneBot 来连 LenBot | LenBot 的监听地址和端口，在 OneBot 里填 `ws://<LenBot 地址>:<端口>` |

访问令牌在两边填写同一个；反向 WebSocket 监听非本机地址（例如 Docker 内的 `0.0.0.0`）时必须填写。首次配置时点**测试连接**，LenBot 会从 OneBot 读出 Bot 自己的 QQ 号，这一步不会往群里发消息。

之后可以在面板的设置页改连接方式；首页能看到连接状态，断了可以手动重连。

## 发送方式

- **模拟发送**：照常收消息、想回复，但不发到 QQ，回复只记在本地。适合刚装好时试效果。
- **真的发到 QQ**：确认角色和设置都没问题后再切换。

两种方式下模型调用都会计费。

## Docker 里的网络

LenBot 在容器里时，容器内的 `127.0.0.1` 指的是容器自己，不是宿主机。让 OneBot 和 LenBot 加入同一个 Docker 网络，用容器名互相访问；或者按实际网络环境填宿主机地址。

## 发文件

后台任务的产物要发到 QQ 时，OneBot 那边也要能读到 LenBot 的交付目录：把交付目录只读挂给 OneBot，再把 `onebot.upload_visible_root` 设成 OneBot 看到的路径。见[后台任务](./tasks#发到-qq)。
