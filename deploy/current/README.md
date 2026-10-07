# 可选服务与配置

部署方式的选择见[部署](../README.md)。这里说明聊天之外各项服务怎么接，以及怎样把 LenBot 作为系统服务运行。所有设置都在面板里改，最终都写进实例根目录的 `lenbot.config.json`。

## OneBot

LenBot 通过 OneBot v11 收发 QQ 消息，自己不登录 QQ。需要一个已经登录 QQ、开着 OneBot WebSocket 的实现，连接方式二选一：

- **正向 WebSocket**：LenBot 去连 OneBot 的地址，例如 `ws://127.0.0.1:3001`。
- **反向 WebSocket**：LenBot 监听一个端口，等 OneBot 连进来。

两边配置了访问令牌时要一致。连接在面板的「设置 → 连接」里改，首页能看到连接状态。在首页手动断开 QQ 后，要在右上角账号菜单里重启才会重新连上。

发送方式 `delivery` 有两种：`onebot` 真实发到 QQ，`simulated` 只在本地记录。两种情况模型调用都会计费。

## 模型

模型页先添加服务商，读取模型列表或手动填写，再给用途绑定模型。聊天支持 `openai-chat`（兼容聊天）、`openai-responses`、`anthropic` 和 `gemini` 四种原生协议；语音转写与向量分别使用 `openai-audio` 和 `openai-embeddings`。地址、生成参数与续接范围见[模型协议](../../developer/model-protocols.md)。服务商可设置共用的 HTTP(S) 网络代理，留空直连。

| 用途 | 做什么 | 必需 |
|---|---|---|
| mind | 群聊主脑，判断、说话、调工具 | 是 |
| vision | 看图 | 否 |
| memory | 后台整理记忆、写目录摘要 | 否 |
| learner | 学说法、黑话，判断回复效果 | 否 |
| worker | 后台任务里的 Pi | 否 |
| asr | 语音转写 | 否 |

上下文窗口、输出上限、超时都按服务商文档填写。模型拒绝请求时，LenBot 直接报错，不会自动缩小参数或换模型重试。文本连接测试会调用一次模型，工具续接测试调用两次，都可能计费，但不会发送群消息。测试成功只证明对应测试内容，不能证明模型的全部能力。换绑时已完成的异源工具组转成历史资料；未完成的工具组不能跨绑定续接，必要时从面板开始新上下文。

LenBot 只统计模型服务报告的 token，不计算金额。用量和每天的 token 上限在模型页的用量与上限里查看和设置；服务没有报告 token 的调用单独计数。设了上限时，当天出现没报告 token 的调用也会暂停模型调用，因为无法确认是否超限。语音转写和向量调用单独显示，不计入上限。

## 记忆与向量检索

记忆默认用本地 Markdown 和全文检索，不需要额外服务。想加向量检索，准备一个 embeddings 服务，在面板里绑定。用仓库自带的 Ollama 配方：

```sh
docker compose -f deploy/current/services.compose.yaml up -d embeddings
docker exec lenbot-embeddings ollama pull bge-m3:567m
```

然后在面板填 `http://127.0.0.1:11434/v1`、模型 `bge-m3:567m`、1024 维。换向量模型后需要停机重建索引，见[使用与维护](operations.md#记忆维护)。

## 语音转写

自动转写群里的语音需要一个 OpenAI 兼容的转写服务，绑定到 `asr` 用途，再在群设置里打开。本地部署 whisper.cpp 的方法见 [ASR](asr.md)。

## 后台任务

后台任务把长工作交给独立 Docker 容器里的 Pi 执行，聊天不用等它做完。要启用，需要：

1. 准备任务镜像：用官方的 `ghcr.io/lendevs/lenbot-worker:<版本>`（版本与 LenBot 一致），或从源码构建 `docker build -t lenbot-worker:local -f docker/next-worker/Dockerfile .`
2. 在能力页配置任务环境：Docker 命令的绝对路径、Docker socket、工作目录、运行目录和交付目录。三个目录要分开，Docker 主机能访问，容器的 `uid/gid` 能读写。
3. 绑定 `worker` 用途的模型。
4. 在群设置里开启任务，并在设置页的权限里给出谁能发起任务。
5. 角色的工具许可里包含 `delegate`、`task` 和 `tool_search`；要用技能时，角色的技能许可也要包含它。

任务容器没有网络，模型和公网请求都经过宿主转发，真实密钥不进容器。

要把任务产物发到 QQ，OneBot 那边也要能读到交付目录：把交付目录只读挂进 OneBot 所在的容器，并把 `onebot.upload_visible_root` 设成容器里看到的路径。

部署包和 Docker 安装用官方任务镜像时，面板升级会一起换成同版本的任务镜像；自己构建的镜像和源码运行需要时自己重建。给任务目录加硬上限见[任务存储池](task-storage.md)。

## 账号浏览

账号浏览让任务使用主人自己登录的浏览器，需要单独的守护进程、浏览器扩展、文件助手和配对，见[浏览器配套组件](../browser/README.md)和[文件传输](browserskill-files.md)。普通的公开网页浏览在任务镜像里就能用，不需要这些。

## 面板访问

面板默认只监听本机地址，例如 `http://127.0.0.1:8088`。从别的机器访问时用 SSH 端口转发，或者自己配置 HTTPS 反向代理，不要直接把监听地址改成公网。

## 作为系统服务运行

`len-bot` 是一个前台进程：Ctrl-C 停止，面板里的重启由它自己完成。服务管理器只需要启动它，不需要自动拉起；异常退出时去看日志，处理后再启动。

**部署包**自带 `service` 命令，管理 systemd 用户服务或 launchd 服务，见[部署包](../package/README.md#系统服务)。

**源码或手动安装**在 Linux 上可以用 [systemd 模板](lenbot.service)：

```sh
sudo install -m 0644 deploy/current/lenbot.service /etc/systemd/system/lenbot.service
# 按实际情况修改 User、WorkingDirectory、ExecStart 和 ReadWritePaths
sudo systemctl daemon-reload
sudo systemctl start lenbot
sudo journalctl -u lenbot -n 100 --no-pager
```

模板里的工作目录是实例目录，`ExecStart` 指向实例里的 `.venv/bin/len-bot`，`ReadWritePaths` 要包含实例目录和配置里实例外的任务目录。模板设为 `Restart=no`，也不会开机自启，需要时自己 `enable`。

macOS 上可以双击 [start.command](start.command)，它在仓库根目录执行 `.venv/bin/len-bot`。

**Docker** 见 [Docker 部署](docker.md)。

## 本机配套服务

[services.compose.yaml](services.compose.yaml) 是向量服务（Ollama）的配方，[start-asr.sh](start-asr.sh) 启动本地转写。它们是独立服务，各有自己的配置，LenBot 只通过地址连接，不会替你安装或启动。日常顺序：先启动配套服务，再启动 LenBot；停止时反过来。
