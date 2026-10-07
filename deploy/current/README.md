# 可选服务与配置

部署方式的选择见[部署](../README.md)。本文说明如何接入聊天以外的各项服务，以及如何把 LenBot 作为系统服务运行。所有设置都在面板中修改，最终写入实例根目录的 `lenbot.config.json`。

## OneBot

LenBot 本身不登录 QQ，通过 OneBot v11 收发 QQ 消息。为此需要一个已经登录 QQ 并开启了 OneBot WebSocket 的实现。连接方式有两种，选择其一。

- **正向 WebSocket**。LenBot 连接 OneBot 的地址，例如 `ws://127.0.0.1:3001`。
- **反向 WebSocket**。LenBot 监听一个端口，等待 OneBot 连接。

两侧都配置了访问令牌时，令牌必须一致。连接设置在面板的「设置 → 连接」中修改，连接状态显示在首页。

运行期间断线后会自动恢复。

- 正向连接按间隔重连。间隔从 1 秒开始，每次翻倍，最长 30 秒。连接稳定 30 秒后间隔重置。重连后会重新核对账号。
- 反向连接等待 OneBot 重新连接。

启动时第一次连接就失败时，LenBot 不会自动重试。问题解决后，点击首页的「手动连接 QQ」。在首页手动断开 QQ 后，需要在右上角的账号菜单中重启，才会重新连接。

发送方式 `delivery` 有两种。`onebot` 真实发送到 QQ。`simulated` 只在本地记录，回复可以在面板中查看。

## 模型

先在模型页添加服务商，读取模型列表或手动填写模型名，再为各个用途绑定模型。

- 聊天支持四种原生协议，即 `openai-chat`（兼容聊天接口）、`openai-responses`、`anthropic` 和 `gemini`。
- 语音转写使用 `openai-audio`，向量使用 `openai-embeddings`。

各协议的地址和生成参数，以及续接范围，见[模型协议](../../developer/model-protocols.md)。服务商可以设置共用的 HTTP(S) 网络代理，留空时直接连接。

| 用途 | 作用 | 必需 |
|---|---|---|
| mind（聊天） | 判断是否回复，决定回复的内容和使用的工具 | 是 |
| vision | 看图 | 否 |
| memory | 在后台整理记忆，编写目录摘要 | 否 |
| learner | 学习说法和黑话，判断回复效果 | 否 |
| worker | 后台任务中编程助手 Pi 使用的模型 | 否 |
| asr | 语音转写 | 否 |

上下文窗口、输出上限和超时时间都按服务商的文档填写。模型拒绝请求时，LenBot 直接报错，不会自动缩小参数，也不会换用其他模型重试。

测试模型时，「只测文本」调用一次模型，「测工具调用」调用两次。两种测试都不会向群里发送消息。

更换绑定的模型后，已完成的异源工具组会转为历史资料。未完成的工具组不能跨绑定续接，必要时从面板开始新的上下文。

LenBot 只统计模型服务报告的 token 数量，不计算金额。用量和每天的 token 上限在模型页的「用量与上限」中查看和设置。服务没有报告 token 的调用会单独计数。设置了上限时，如果当天出现没有报告 token 的调用，LenBot 无法确认是否超出上限，因此也会暂停模型调用。语音转写和向量调用单独显示，不计入上限。

## 记忆与向量检索

记忆默认使用本地 Markdown 文件和全文检索，不需要额外的服务。如果要增加向量检索，请准备一个 embeddings 服务，在面板中绑定。使用仓库自带的 Ollama 配置时，执行下面的命令。

```sh
docker compose -f deploy/current/services.compose.yaml up -d embeddings
docker exec lenbot-embeddings ollama pull bge-m3:567m
```

然后在面板中填写地址 `http://127.0.0.1:11434/v1` 和模型 `bge-m3:567m`，维度为 1024。更换向量模型后，需要停止 LenBot 并重建索引，见[使用与维护](operations.md#记忆维护)。

## 语音转写

自动转写群里的语音需要一个 OpenAI 兼容的转写服务。把它绑定到 `asr` 用途，再在群设置中开启自动转写。在本地部署 whisper.cpp 的方法见 [ASR](asr.md)。

## 后台任务

后台任务把耗时较长的工作交给独立 Docker 容器中的 Pi 执行，聊天不需要等待任务完成。启用步骤如下。

1. 准备任务镜像。使用官方的 `ghcr.io/lendevs/lenbot-worker:<版本>`（版本与 LenBot 一致），或者从源码构建，命令为 `docker build -t lenbot-worker:local -f docker/next-worker/Dockerfile .`。
2. 在能力页配置任务环境。需要填写以下内容。
   - Docker 命令的绝对路径
   - Docker socket
   - 工作目录
   - 运行目录
   - 交付目录

   这三个目录必须互相分开，Docker 主机能够访问，并且容器的 `uid/gid` 有读写权限。
3. 为 `worker` 用途绑定模型。
4. 在群设置中开启任务，并在设置页的权限中指定可以发起任务的人。
5. 角色的工具许可中包含 `delegate`、`task` 和 `tool_search`。需要使用技能时，角色的技能许可中也要包含这个技能。

任务容器没有网络。模型请求和公网请求都经过宿主转发，真实密钥不会进入容器。

要把任务产物发送到 QQ，OneBot 一侧也必须能读取交付目录。请把交付目录以只读方式挂载到 OneBot 所在的容器，并把 `onebot.upload_visible_root` 设为容器中看到的路径。

部署包和 Docker 安装使用官方任务镜像时，通过面板升级会一并换成同版本的任务镜像。自行构建的镜像，以及从源码运行时使用的镜像，需要时请自己重新构建。为任务目录设置硬性容量上限的方法见[任务存储池](task-storage.md)。

## 账号浏览

账号浏览让后台任务使用主人自己已登录的浏览器。启用账号浏览需要以下组件，见[浏览器配套组件](../browser/README.md)和[文件传输](browserskill-files.md)。

- 单独运行的守护进程
- 浏览器扩展
- 文件助手
- 配对

浏览普通的公开网页不需要这些组件，任务镜像本身就支持。

## 面板访问

面板默认只监听本机地址，例如 `http://127.0.0.1:8088`。从其他机器访问时，请使用 SSH 端口转发，或者自行配置 HTTPS 反向代理。不要直接把监听地址改为公网地址。

## 作为系统服务运行

`len-bot` 是一个前台进程，按 Ctrl-C 停止。面板中的重启由进程自身完成。服务管理器只需要启动这个进程，不需要在退出后自动重新启动。进程异常退出时，请查看日志，处理问题后再启动。

**部署包**自带 `service` 命令，用于管理 systemd 用户服务或 launchd 服务，见[部署包](../package/README.md#系统服务)。

**从源码运行或手动安装**时，在 Linux 上可以使用 [systemd 模板](lenbot.service)。

```sh
sudo install -m 0644 deploy/current/lenbot.service /etc/systemd/system/lenbot.service
# 按实际情况修改 User、WorkingDirectory、ExecStart 和 ReadWritePaths
sudo systemctl daemon-reload
sudo systemctl start lenbot
sudo journalctl -u lenbot -n 100 --no-pager
```

模板中的工作目录是实例目录，`ExecStart` 指向实例中的 `.venv/bin/len-bot`。`ReadWritePaths` 必须包含实例目录，以及配置中位于实例以外的任务目录。模板设置了 `Restart=no`，也不会开机自动启动，需要时请自行执行 `enable`。

在 macOS 上可以双击 [start.command](start.command)，它在仓库根目录执行 `.venv/bin/len-bot`。

**Docker** 见 [Docker 部署](docker.md)。

## 本机配套服务

[services.compose.yaml](services.compose.yaml) 是向量服务（Ollama）的配置，[start-asr.sh](start-asr.sh) 用于启动本地转写服务。它们是独立的服务，各自有自己的配置。LenBot 只通过地址连接这些服务，不负责安装或启动。日常使用时，先启动配套服务，再启动 LenBot。停止时顺序相反。
