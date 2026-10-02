# 部署与启动

唯一宿主入口为 `len-bot`，运行参数只来自实例根 `lenbot.config.json`。安装、迁移、启动分开执行；服务模板不自动重启。角色管理与离线转换见[使用与维护](operations.md)。

## macOS 安装与日常启动

需要 Python 3.13、uv、Node.js 22；使用容器服务或独立任务时还需 Docker Desktop。源码根首次安装：

```sh
./scripts/install.sh
# 仅在需要独立任务时构建，根 worker.image 要与所选标签一致
docker build -t lenbot-worker:local -f docker/next-worker/Dockerfile .
```

已安装下述服务的本地实例，日常按顺序启动，不重新安装或迁移：

```sh
docker compose -f deploy/current/services.compose.yaml up -d
./deploy/current/start-asr.sh   # 单独终端；已有 ASR 进程时不重复启动
# 另一个终端，从实例根启动 Bot
uv run --no-sync len-bot
```

只用聊天不要求 Docker、OpenViking 或 ASR；只启动根配置实际采用的服务。停止时先 Ctrl-C 停 Bot、再停 ASR，最后 `docker compose -f deploy/current/services.compose.yaml stop`，不删除服务卷。

| 服务 | 本机部署入口 | 私有数据／配置 |
|---|---|---|
| LenBot 面板 | 根 `panel`；本机为 `http://127.0.0.1:11307` | `lenbot.config.json` |
| OpenViking | `http://127.0.0.1:1933`，API 服务 | `state/services/openviking/` 中 `ov.conf`、`templates/`、`data/` |
| Ollama 向量 | `http://127.0.0.1:11434/v1`；`bge-m3:567m`、1024 维 | `state/services/embeddings/` |
| ASR | `http://127.0.0.1:18171/v1`；`large-v3-turbo-q5_0` | [安装与配置](asr.md) |
| SnowLuma | 独立 QQ 登录／OneBot 服务 | SnowLuma 自己的配置和登录状态 |

服务客户端不等于服务本身：Compose、ASR 启动脚本与 OpenViking 镜像使用现成实现，不添加协议代理。它们自己的配置不覆盖 LenBot 根参数；所有服务均不自动重启。

## 配置与连接

- 没有根配置时，`len-bot` 仅显示本机首次向导；保存后退出，不自动连接 QQ。已有配置时，同一命令直接启动业务宿主，不能当作无副作用校验。
- 运行中在面板保存；需要重启的修改由用户明确重启生效，手工编辑前先停机。升级不覆盖模型、人格、人工样例或群名单。
- `delivery: "onebot"` 真实发送，`"simulated"` 只模拟发送；两者的模型调用都可能计费。
- SnowLuma 须登录且实际监听 OneBot 端口，WebUI／VNC 在线不足以证明连接就绪。首次连接失败后面板仍可用，首页可明确手动连接；按钮只用本次启动快照，不热读保存值。已结束的业务运行须重启，不由按钮重建。
- 面板默认回环监听，远端用 SSH 转发；自建 HTTPS 入口按实际代理和 cookie 配置，不因失败放宽监听或会话限制。
- `react` 需要角色 `stickers/index.yaml` 和真实原件；知识工具需要实际文档。插件／MCP／账号浏览同样必须有实际代码或服务，不能只开空开关。

## 可选外部服务

普通聊天不要求Docker、记忆或ASR服务。按需要选择：

- 本地记忆直接使用宿主文件后端；OpenViking使用独立服务与场景用户，VLM／embedding在服务自己的配置中设置，分类见[记忆模板](memory-templates.md)。
- 向量服务通过配置的embeddings接口接入；当前Compose示例使用Ollama与bge-m3，不由宿主安装或自动换模型。
- 本地语音转写安装只见[ASR说明](asr.md)。
- 外部服务使用自身配置，不能覆盖LenBot根运行参数。示例Compose不会自动重启服务；用户自行选择是否启用。

首次构建原生记忆镜像时，把固定提交 `a09a9d20a8e07d08973aee177802d00e08df29e6` 的OpenViking源码放入独立目录：

```sh
docker build -f "$PWD/deploy/current/Dockerfile.openviking" \
  -t lenbot-openviking:a09a9d2 /path/to/openviking-source
docker compose -f deploy/current/services.compose.yaml up -d embeddings
docker exec lenbot-embeddings ollama pull bge-m3:567m
```

Ollama首次拉取须等待其服务就绪。OpenViking服务密钥与每场景用户填写在对应配置，创建模板后再启动，不把服务启动当作聊天召回效果。

## 独立任务与浏览器

根配置必须同时给出实际 `worker`、`models.roles.worker`、场景 `tasks.enabled`、主人或授权账号，以及角色允许的 `delegate/task/tool_search`。采用内置技能还需实际技能目录。

- `worker.docker_binary` 用 Docker CLI 绝对路径，`docker_host` 用 `docker context inspect` 得到的本机 Unix socket；运行身份必须有访问权限。
- `workspace_root`、`runtime_root`、交付根分开，均为 Docker 主机可见的实际路径。容器 `uid/gid` 必须能读写这些目录；Mac 常用 `id -u`／`id -g`，不照抄 Linux 镜像账号。
- SnowLuma 在容器中时，将交付根只读挂入，例如 `file_assets` → `/lenbot-files`，并设置 `onebot.upload_visible_root` 为容器内路径。文件登记、本地可读、QQ 上传成功是不同结果。
- 任务保持 `network=none`、独立工作区与宿主管道；公共浏览走任务镜像 Chromium 和宿主明确出口。独立账号浏览另需专用守护进程、扩展与账号配对，不支持远程文件上传下载。
- 更新宿主不会自动更新任务镜像。共享资料、环境放弃和归档规则见[任务维护](operations.md#任务资料与环境)。任务目录用量不是硬磁盘配额。

## Linux 服务部署

源码置于 `/opt/lenbot/source`，实例置于 `/opt/lenbot/instance`。全新主机建立身份与目录，已有属主不递归重写：

```sh
sudo useradd --system --user-group --home-dir /opt/lenbot/instance --no-create-home lenbot
sudo install -d -o lenbot -g lenbot -m 0700 /opt/lenbot/instance
cd /opt/lenbot/source
./scripts/install.sh
cd /opt/lenbot/instance
sudo -u lenbot /opt/lenbot/source/.venv/bin/len-bot
```

最后一条在无配置时运行首次向导，已有配置时启动 Bot。实例数据与引用的外部目录不放进发布源码；服务模板只允许写实例根，使用外部目录须明确调整 `ReadWritePaths`。升级先按[离线维护](operations.md#升级与文件锁)处理，不在启动中迁移。

明确配置后安装 systemd 模板；`daemon-reload` 不启动或开机自启：

```sh
sudo install -m 0644 /opt/lenbot/source/deploy/current/lenbot.service /etc/systemd/system/lenbot.service
sudo systemctl daemon-reload
sudo systemctl start lenbot
sudo journalctl -u lenbot -n 100 --no-pager
```

用 `sudo systemctl stop lenbot` 停止，不与手工进程并行运行。真实发送配置下，启动会连接 OneBot 并恢复已有安排／后台工作，不是纯面板启动。

## 分发

宿主镜像可用 `docker build -f deploy/current/Dockerfile -t lenbot-current:local .` 构建。独立任务还需匹配的任务镜像与Docker连接；普通聊天不用装任务环境。打包步骤只见[开发指南](../../CONTRIBUTING.md#构建与提交)，发行能力限制只见根[README](../../README.md#限制与验收)。
