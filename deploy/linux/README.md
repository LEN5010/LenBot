# Linux 部署与恢复

本目录提供 Linux 部署材料。起停约束和当次状态见[运行手册](../../docs/operations.md)与[当前任务](../../docs/iteration.md)。模板不能证明目标 Linux、账号、模型或实群已验收；生产启动、现场配置变更和真实发送分别服从当次授权。

## 服务与卷

LenBot 用 Compose 运行；只有选用 Gateway 后端的执行能力才另需同一 Linux 主机上的独立 systemd 服务，管理执行镜像。普通文字聊天不要求 Gateway 或 worker 镜像。OneBot 与可选 Core 沿用运营者已核对版本的独立部署，本目录不安装或登录它们。

| 对象 | 主机位置 | 进程中位置／权限 |
|---|---|---|
| LenBot 根配置 | `/srv/lenbot/control/lenbot.config.json` | `/srv/lenbot/lenbot.config.json`；0600；挂整个父目录供原子保存 |
| LenBot 数据 | `/var/lib/lenbot` | 同路径；数据库、media、file_assets、plugins；UID/GID 10000 可写 |
| 本地插件代码 | `/srv/lenbot/local_plugins` | `/srv/lenbot/local_plugins`；只读 |
| Gateway 代码 | `/opt/lenbot-gateway/release` | 独立 uv 虚拟环境，与 LenBot 同一发布提交 |
| Gateway 配置 | `/etc/lenbot-gateway/gateway.config.json` | root 读取，0600 |
| Gateway 状态 | `/var/lib/lenbot-gateway` | journal、controls、workspaces、历史产物和出口日志 |
| OneBot 文件导出 | `/var/lib/lenbot/file_assets` | OneBot 内 `/lenbot-files`，只读 |

LenBot 镜像只有安装后的代码及依赖，无根配置、Docker CLI/socket 或任务卷。工作目录 `/srv/lenbot` 是部署根目录，ConfigStore 仍只读写该目录的 `lenbot.config.json`。必须挂父目录：单文件 bind mount 无法承接原有临时文件原子替换。运行中通过面板保存，手工编辑前先停机。

Gateway 模板以 root 管理 Docker 和 worker GID，是受信任的容器管理服务；其权限不属于 LenBot 或 worker。模板针对 rootful Docker；rootless、Podman、userns-remap 需要先核对真实 socket、UID 映射与控制目录权限，不能直接宣称兼容。Gateway 自有配置仅描述服务端镜像、网络、卷与服务身份，不得覆盖 LenBot 业务授权、模型或预算；每次执行的 control 文件才是本次输入的最小只读投影。

## 构建与初始化

先记录实际发行版、架构、内核、CPU/RAM/磁盘、Docker/Compose 与 UID/GID。模板中的资源上限、子网和 UID 是待核对的部署选择，不是目标机器实测值；冲突时离线调整同批材料。

从确定的源码提交构建。前端产物不进 Git，LenBot Dockerfile 的 Node 阶段通过 package-lock.json 安装并构建，再复制到 Python 安装阶段；镜像不使用本地 dist。`.dockerignore` 只允许发布源码、锁文件与容器配方进入上下文。

```sh
# 源码根目录；仅构建，不启动
docker compose -f deploy/linux/compose.yaml build lenbot
# 以下仅为本次明确选用的可选执行能力构建
docker build -f containers/workspace/Dockerfile -t lenbot-workspace:local .
docker build -f containers/browser/Dockerfile -t lenbot-browser:local .
docker build -f containers/media/Dockerfile -t lenbot-media:local .
```

LenBot 使用 uv 0.12.13、项目 uv.lock，构建时不安装 dev 依赖；构建需要访问镜像与依赖源。worker 沿各自配方。基础镜像仍使用可变版本标签，不是不可变镜像锁；精确镜像与首个支持组合待确认。记录实际镜像 ID/标签与构建环境，保留旧发布镜像，不用自动 pull 或滚动更新改变已核对版本。

新部署由运营者准备目录；已有部署先停机备份，不递归重写旧数据身份：

```sh
sudo install -d -o 10000 -g 10000 -m 0700 /srv/lenbot/control /var/lib/lenbot
sudo install -d -o root -g root -m 0755 /srv/lenbot/local_plugins
sudo install -d -o 10000 -g 10000 -m 0750 /var/lib/lenbot/file_assets
# 仅在选用 Gateway 时准备
sudo install -d -o root -g root -m 0700 /etc/lenbot-gateway /var/lib/lenbot-gateway
```

将已核对根配置放入 control，属主 10000:10000、0600。首次初始化才人工填写样例；升级使用原配置，不覆盖模型、人格、人工样例、Shadow、账号或群名单。旧数据库和全部引用文件迁移时保留业务 ID，不用空库冒充迁移。

| 根配置字段 | 此模板要求 |
|---|---|
| `runtime.db_path` | `/var/lib/lenbot/len_bot.db` |
| `runtime.dashboard_host/port` | 容器内 `0.0.0.0:11307`，Compose 只发布主机回环 |
| `plugin_directories` | 使用上表外部插件时为 `["/srv/lenbot/local_plugins"]`；无外部插件可为空 |
| `runtime.onebot_*` | 容器可达的真实 OneBot 地址与原连接方式；容器 localhost 不是宿主 |
| `plugins.workspace.config` | Gateway 部署选择 `worker: null` 与明确 `gateway`，不在 LenBot 安装 Docker |
| Gateway 客户端 | `base_url: http://172.31.8.1:8790`、同一 service token、已登记 `image_ref: python` 和 `network_policy: offline` |

其余字段沿原配置。cookie 要求 HTTPS 时继续使用既有 HTTPS 入口，不自动关闭 secure；回环面板端口可经 SSH 转发。模板不发布反向 WS 端口，沿用反向 WS 时通过私有控制网络连接实际容器监听地址，核对 OneBot 的目的地与 bind。

## 网络与 Gateway 启动

先确认子网未占用，建立控制网络：

```sh
docker network create --driver bridge --subnet 172.31.8.0/24 --gateway 172.31.8.1 --opt com.docker.network.bridge.name=br-lenbot-ctl lenbot-control
```

LenBot 加入该网络，Gateway API 绑定网桥地址。OneBot 按真实部署选择加入同一私网或使用已有可达私网地址，不自动修改其现有连接。worker 永不加入控制网络。

以下 Gateway 与执行网步骤只用于已选用相应执行能力的部署；普通聊天跳过，直接取得当次授权后启动 LenBot。

只需离线 Python 时，人工填写 `gateway.offline.example.json`、替换 token 后保存到 `/etc/lenbot-gateway/gateway.config.json`。它没有公共策略，不会开放联网工作。需要公共 Python、浏览器或媒体时，按根目录[gateway.config.example.json](../../gateway.config.example.json)添加镜像与 public 策略，API host 改为控制网桥地址，并准备独立执行网：

```sh
docker network create --internal --subnet 172.31.9.0/24 --gateway 172.31.9.1 --opt com.docker.network.bridge.name=br-lenbot-eg lenbot-egress
```

代理监听 `172.31.9.1:8799`。**internal 网络不证明只能到代理**：需按目标 Docker 防火墙实现限制执行网到宿主仅此端口，阻断到控制网、其他 worker、私网和直接公网的旁路，并核对 Docker DNS、IPv6 与重启后的规则。环境代理、网桥名和 deployment_verified 开关都不能代替这些事实。本目录不提供覆盖未知现有防火墙的通用安装脚本；规则未明确时保持 `deployment_verified: false`，对应联网能力不放行。目的域与资源域按真实业务填写，不用任意通配替代缺失证据。

浏览器还需安装[对应版本 seccomp](../../containers/browser/README.md)；媒体按[媒体容器说明](../../containers/media/README.md)核对。缺镜像、seccomp 或已核验出网策略时保持对应插件未启用，不切回宿主执行。

把同批 Gateway 源码安装到 `/opt/lenbot-gateway/release`，在该目录执行 `uv sync --locked --no-dev`。服务固定使用该虚拟环境，不在每次启动时联网安装。token 只在网关配置和 LenBot 客户端配置中，worker 只得绑定本次执行的代理凭据。

```sh
sudo install -m 0644 deploy/linux/lenbot-gateway.service /etc/systemd/system/lenbot-gateway.service
sudo systemctl daemon-reload
# 仅在取得当次 Gateway 启动授权后执行
sudo systemctl start lenbot-gateway
```

普通聊天部署及已就绪的 Gateway 部署都使用下面的 LenBot 启动入口；取得当次启动授权后执行：

```sh
docker compose -f deploy/linux/compose.yaml up -d --no-build --pull never lenbot
```

启动后的连接、对话资格与真实回执按[首条回复路径](../../docs/operations.md#从面板可用到首条真实回复)分别确认，不用容器 running 替代业务验收。

模板 `Restart=no` / `restart: "no"`，不自动恢复实发；开机启动另按明确部署策略配置。Gateway 日志用 `journalctl -u lenbot-gateway`；LenBot 用 `docker compose -f deploy/linux/compose.yaml logs --since 30m lenbot`。日志不替代 action、file_id 与 OneBot 回执；分享前移除凭据、私人原话和签名地址。

## OneBot 文件与可选项

OneBot 仅将 file_assets 只读挂至 `/lenbot-files`。文件 0440、目录 0750；非 root OneBot 用户需组 10000 的读取权限，不开放整个数据目录解决权限。不同用户命名空间须核对真实映射。文件协议当前只接 NapCat `upload_group_file_data_file_id`；实际版本、路径映射与 `data.file_id` 均确认后才置 `onebot_file_upload.deployment_verified: true`，文本发送成功不证明上传成功。

Core、转写、B 站账号/允许收藏夹缺失均单列“未配置/未放行”，不阻塞已确认的普通聊天。Core 首版只接带 echo 的群文字/at/图片帧；实际版本无 echo 时不能声称接通。B 站动作另需 grant 和额度，Cookie 可读不代表可写。转写保留原绑定和实际计量协议，费用未核实不填价格。

## 停机、备份与恢复

先通过原面板阻止新的自主工作入场，按当次授权暂停新外发，记录未结束工作、在途调用、预占、待发文件和 unknown 操作。等待执行结束，或从原取消入口取得真实停止回执。然后先停 LenBot，再停 Gateway：

```sh
docker compose -f deploy/linux/compose.yaml stop lenbot
sudo systemctl stop lenbot-gateway
```

90 秒只是管理器的退出等待上限，不是所有容器已停止的证据。termination_unconfirmed 保留原执行与卷，不复用、不清理、不重置预算。确认数据库句柄关闭后，配置、代码版本、部署文件和完整数据目录普通复制到全新备份目录，SQLite 另做普通 .backup；不生成指纹/校验清单。

以下路径由运营者填写。目标已存在时换新目录，不覆盖历史备份：

```sh
set -e
release_backup=/srv/lenbot/ops/backups/本次唯一发布目录
sudo mkdir -p /srv/lenbot/ops/backups
sudo mkdir -m 0700 "$release_backup"
sudo cp -a /srv/lenbot/control "$release_backup/control"
sudo cp -a /srv/lenbot/local_plugins "$release_backup/local_plugins"
sudo cp -a /var/lib/lenbot "$release_backup/lenbot-data"
sudo cp -a /etc/lenbot-gateway "$release_backup/gateway-config"
sudo cp -a /var/lib/lenbot-gateway "$release_backup/gateway-data"
sudo sqlite3 /var/lib/lenbot/len_bot.db ".backup '$release_backup/lenbot.snapshot.db'"
sudo sqlite3 /var/lib/lenbot-gateway/gateway.db ".backup '$release_backup/gateway.snapshot.db'"
```

未部署 Gateway 时跳过对应命令。匹配源码归档/提交、部署文件、实际镜像与人工记录同批保存。备份含敏感数据，不放仓库、不送模型。

恢复先保留故障现场、停服务，确认不会丢失备份后新增消息/回执，再普通复制同批目录至原位置，用 SQLite 快照恢复数据库。不要混用新库、旧 WAL/SHM 和旧媒体；完整离线替换目录并保留被替换目录，不在线覆盖。恢复原 UID/GID、权限、配置和匹配代码，核对后取得当次启动授权。有新事实时先做明确的离线兼容处理，不能直接回滚；数据库恢复也不能撤销平台动作。

重启查询原 execution/job/action：未知结果不重放；旧页面失效但已存资料保留；预算、deadline、版本和待交付资产沿原记录。停止未知不清零、模型未知不重购、上传未知不换通道。

## 发布记录

用[现场记录模板](release-evidence.template.md)，在本地运维目录记录一个获准聊天群与一个获准播报群的输入、调用、工作、资产、表达与真实回执。阶段状态只归纳到 docs/iteration.md，不提交私人原话和凭据。未观察模块写未确认，可选缺失单列；编译、Compose 解析和镜像构建都不等于实群通过。

语法依据：[Compose 服务定义](https://docs.docker.com/reference/compose-file/services/)、[uv 镜像构建](https://docs.astral.sh/uv/guides/integration/docker/)。目标执行网另核对[Docker DNS 行为](https://docs.docker.com/engine/network/#dns-services)；文档不是实机证据。
