# 当前核心部署：Linux 原生宿主

此路径对应当前 `len-bot` 入口、唯一根配置和 `next` 数据格式，不使用旧 `runtime.*` 配置或旧 Gateway。服务模板不自动重启、不初始化生产配置、不升级数据库。安装、迁移和启动分别执行，构建不代表首次对话已经通过。

## 前置与目录

- Python 3.13、uv、Node.js 22 或更新版本。语音转写使用实际 OneBot `get_record(out_format=wav)` 提供的 WAV，不在宿主猜测转换格式或增加 ffmpeg 配置项。
- 当前源码位于 `/opt/lenbot/source`；独立实例根位于 `/opt/lenbot/instance`，服务以 `lenbot` 用户运行。
- 数据库、记忆处理库、媒体、角色包、任务卷和根配置都属于实例数据，不放进发布源码。默认模板只允许写实例根；配置外部可写目录时，运营者须同时明确扩展服务的 `ReadWritePaths`，不能因权限失败自动换目录。

为全新主机创建运行身份和实例目录；已有身份/数据沿原属主，不递归改写：

```sh
sudo useradd --system --user-group --home-dir /opt/lenbot/instance --no-create-home lenbot
sudo install -d -o lenbot -g lenbot -m 0700 /opt/lenbot/instance
```

将已取得的当前源码放入 `/opt/lenbot/source`，在该源码目录执行：

```sh
./scripts/install.sh
```

安装脚本锁定依赖并构建面板，不启动聊天或迁移数据。服务直接执行安装好的入口，不在每次启动时运行 uv 同步、npm 或网络更新。

## 首次配置

在尚无根配置的**新实例**里，以运行身份执行一次当前入口：

```sh
cd /opt/lenbot/instance
sudo -u lenbot /opt/lenbot/source/.venv/bin/len-bot
```

这时只启动本机首次配置页，不创建 OneBot 连接。使用终端打印的精确端口与链接；远端主机经 SSH 转发该回环端口，不把初始化凭据写到公开地址。填写模型、角色、场景和连接后保存，初始化进程退出，不自动启动业务宿主。可先明确选择 `delivery: "simulated"`，模型仍可能真实计费。已有根配置时执行相同命令会进入业务宿主，不能将它当成无副作用的初始化或校验命令。

当前根字段是 `database`、`models.providers/roles`、`panel`、`onebot`、`scenes` 等；旧 `runtime.db_path`/`runtime.onebot_*` 不是本路径的配置。根 `lenbot.config.json` 与人工角色只有明确配置时创建；升级不重新初始化，不覆盖模型、人格、样例或实发群名单。

面板默认绑定主机回环；远端运营可 SSH 转发。使用自己的 HTTPS 入口时按根 `panel` 配置与实际代理方式设置，不因为访问失败自动关闭安全 cookie 或改监听范围。

## 升级与离线迁移

先停业务和相关旧后台，再按原运行手册备份**整个实例根**，以及根配置明确引用的外部目录和服务。保持旧源码、旧发布环境和镜像可恢复；不能只备份一个 SQLite 主文件而遗漏有内容的 WAL。

当前宿主、单场景入口、独立面板和离线维护命令在读取配置前持有实例根 `.lenbot-instance.lock` 的非阻塞 Unix 排他锁，Linux 和开发机 macOS 使用同一入口；占用或文件锁服务错误直接报原错并结束，不等待、重试或停止另一进程。业务/处理库迁移在开始转换前也取得全部保留试聊根的锁，活动试聊不会边运行边被转换。这个文件只有占用用途，不保存 PID/参数，存在不代表进程活跃；退出保留文件并关闭描述符，不删除/替换文件“解锁”。文件不进版本控制、分发包或镜像。

文件锁只约束参与它的当前入口，不能证明未采用该协议的旧已加载核心、其他实例、外部记忆服务或手工文件写者已经停止；这些仍按停机前置处理。不同实例根不得配置同一个业务库或把同一 QQ 场景同时交给多个入口；锁不替代完整备份、生产启动或发送授权。

新期间原话反向追加的离线入口是 `python -m len_bot.next.export_history`，参数只来自已停机新实例根 `history_export`，不修改生产路由或启动旧核心。需要移交新期间图片时，明确填写 `media_directory`（实例根内独立图片目录），只转存消息已关联的原字节，不把视觉缓存 JPEG 当原件，不联网补图或把素材采纳成公共调色盘。旧库备份、事件/素材提交与原件复制的事实分别报告；数据库事务失败时已复制文件保留，不假称跨库/文件原子回滚。恢复旧实例时必须保留这些文件及库中的实际绝对路径，不能只拿回写后的 SQLite。音视频、任务交付、后台/提醒归属仍须分别移交；此命令未实际执行，不等于完整生产回滚已验收。

已从旧库导入的一次性真人提醒可用 `python -m len_bot.next.export_reminders` 离线核对回写。根 `reminder_export` 明确填写 `target`、不覆盖的 `backup`、`scenes` 和 `pending: "restore"` 或 `"hold"`；restore仅恢复未来且未交付会话的pending，hold停止全部旧触发。已交付、取消、blocked或过期的原提醒在旧侧置cancelled以阻止重复唤醒，原新状态与停用原因保存在该旧提醒中；这不是确认已发送或声称原用户取消。原旧任务/人类请求必须存在且相符，已领取/修改的旧任务报错，不清理待发/唤醒或新侧未结束对话。新期间新建的安排、周期/自主工作没有可直接对应的旧请求身份，保留新库并逐项报告，不从时间/正文猜配。本入口不授权启动，不代表完整后台移交完成。

当前代码要求业务库格式 35、记忆处理库格式 5。升级源代码/依赖后，仅在实例已停机并备份完成时，从实例根执行：

```sh
cd /opt/lenbot/instance
sudo -u lenbot /opt/lenbot/source/.venv/bin/python -m len_bot.next.migrate
sudo -u lenbot /opt/lenbot/source/.venv/bin/python -m len_bot.next.migrate_memory_jobs
```

有保留的隔离试聊时也按迁移入口实际范围处理。旧消息、提醒或跨后端记忆的移交分别使用根 `history_import`、`reminder_import`、`memory_transfer` 和对应显式离线入口，见[项目说明](../../README.md)。它们不停止旧后台、不修改路由或授权真实切换；新期间消息反向追加也不等于完整回滚已验收。

## 明确安装和启动服务

确认实例路径和运行用户后，安装服务模板；这一步不启动或开机自启：

```sh
sudo install -m 0644 /opt/lenbot/source/deploy/current/lenbot.service /etc/systemd/system/lenbot.service
sudo systemctl daemon-reload
```

取得本次业务启动和实际发送范围授权后，再执行：

```sh
sudo systemctl start lenbot
sudo journalctl -u lenbot -n 100 --no-pager
```

仅需停止时用 `sudo systemctl stop lenbot`；失败保留原文，模板没有自动重启。不要同时保留手工启动进程和这个服务。当前配置为真实发送时，启动会连接 OneBot 并恢复已有安排/后台工作，不是纯面板启动。

## 可选任务镜像与浏览器

聊天不要求 Docker。启用工作任务前，在源码根构建当前实际任务镜像：

```sh
docker build -f docker/next-worker/Dockerfile -t lenbot-next-worker:local .
```

此命令只构建，不启动任务或宿主。构建需获取声明的基础镜像、Pi、浏览器和系统包；记录实际平台与镜像 ID，不能从配方固定版本推断字节一致或任务成功。不要用旧 `containers/workspace` 镜像替代当前 Pi 任务镜像。

运行用户须有根 `worker.docker_binary` 指定 CLI 与 `worker.docker_host` 指定本机 Unix socket 的实际权限；模板不会默认加入 docker 组或挂载 socket。容器卷使用根配置明确的 `workspace_root` 与 `runtime_root`，位于不同目录并由 Docker 主机可见；配置服务以外目录时同时明确文件权限和服务写入范围。任务容器保留原 `network=none`、独立工作区和宿主双向管道，不借部署文档改成宿主执行或直接联网。

填写并保存实际 worker/worker 模型/场景任务许可后，运营者明确重启生效。账号浏览还需独立守护进程/扩展与根配置指定的命令和专用目录，本任务镜像构建不证明其已配对；当前账号浏览文件传输限制仍保留。

新任务可明确选定本场景共享普通资料的实际文件名，宿主登记前复制到任务私有快照，执行时只读挂到 `/inputs`。单件沿当前 `worker.max_file_bytes`，选集最多16份，不加载整个共享目录；续接复用原快照，丢失/不完整/权限错误结束任务，不自动补取或放宽权限。宿主复制所得私有目录/文件保持运行身份所有权，配置的容器 UID 须实际可读；启动探针逐件读取一个字节仅确认权限，不代表 Pi 阅读或使用内容。原共享资料、旧任务家目录与配置不传入账号浏览；只有主人明确选定的输入可复制到其独立任务。

任务页可按需读取当前任务工作区、运行目录和交付副本目录的元数据用量；不调用 Docker、不打开文件正文或清理文件。硬磁盘配额尚未实现，读取到用量不表示卷已经限额；分配字节是文件系统对 inode 的报告，不是剩余可用配额或压缩/去重后的设备实际占用。目录正在变化时读数不是原子快照；失败显示原错并保留旧快照，手动重读，不自动重扫。

## 镜像、许可证与未确认项

只需要聊天控制容器时，可从源码根构建现有宿主配方 `docker build -f deploy/linux/Dockerfile -t lenbot-current:local .`；该镜像安装当前入口和新面板。**此处没有提供自动运行 Compose**：旧 `deploy/linux/README.md` 与 Compose 仍是旧部署材料，不拿其配置/网关说明运行当前核心。容器内默认没有任务 Docker CLI/socket，不能把宿主镜像构建说成完整任务部署。

宿主与当前任务镜像配方均接入项目及实际依赖许可证材料，目标位置为 `/usr/share/lenbot/licenses`；操作系统、浏览器发行物与未知来源内容仍需核对。源包保留锁文件和构建材料，用户实例和本机 `docs/` 不发布。当前路径尚未做干净主机安装、镜像构建、模型/Pi/账号浏览或真实 QQ 首次对话验收；已有源码、服务模板和构建配方不是生产发布完成证明。

每次面板构建保留本次实际 npm 包的元数据和原许可文件，包括构建期依赖；这些原文位于 `assets/licenses/frontend/`，随源码包与 wheel 分发。部署后可从 `/assets/licenses/frontend/index.json` 获取实际清单，再按其相对路径查阅原文；不将清单当成所有模块均进入运行包的证明。Sarasa 旧插件字体的独立许可与来源放在根 `licenses/` 并进入 wheel 的许可目录，不修改冻结插件或将字体重新许可为项目代码。细节见根 `THIRD_PARTY_NOTICES.md`。
