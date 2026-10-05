# 实例任务存储池

工作区、运行目录和共享资料使用一个有硬上限的文件系统，独立交付留在池外。LenBot 读取实际容量，不用配置数字模拟配额；任务满盘时返回文件系统原错。

提供新池创建、数据入池、挂载、状态读取、ext4 扩容和部署片段生成。日常运行只读取根配置，不自动迁移或挂载。

## 创建新池

以下命令只创建新池，不改根配置、不搬已有文件、不启动宿主。以实例运行用户执行；需要磁盘管理权限的系统命令调用 sudo。`--uid/--gid` 是实例运行用户对池目录的所有权。

Linux 需要 `e2fsprogs` 和 `util-linux`。选择新的镜像文件和空挂载目录，例如：

```sh
.venv/bin/python -m len_bot.next.maintenance.storage_pool_admin create-ext4 \
  --image /opt/lenbot/instance/state/task-pool.img \
  --mount /opt/lenbot/instance/state/task-pool \
  --size-gib 32 --uid "$(id -u)" --gid "$(id -g)"
```

镜像的固定容量限制整个 ext4 文件系统；共享资料与全部任务使用这个池。创建不覆盖已有镜像。

macOS 先用 `diskutil apfs list` 查看实际容器，再添加一个独立的大小写敏感 APFS 卷；下面的容器和目录替换为实际值：

```sh
.venv/bin/python -m len_bot.next.maintenance.storage_pool_admin create-apfs \
  --container disk3 --name LenBotTasks \
  --mount /Users/你的用户名/lenbot-instance/state/task-pool \
  --size-gib 32 --uid "$(id -u)" --gid "$(id -g)"
```

命令输出 `storage_pool`、`workspace_root` 和 `runtime_root` 字段，以及读取到的实际容量。下一步由数据入池命令写入这三项；交付根和其他配置保持。APFS quota 限制当前卷，容器中其他卷占用也会影响当前可写空间。[APFS 卷与大小选项](https://support.apple.com/en-asia/guide/disk-utility/dskua9e6a110/mac)。

## 将已有工作数据移入新池

停止宿主并按[升级与文件锁](operations.md#升级与备份)保全实例，结束仍持有容器或浏览器的任务。从原实例目录执行，以现有文件所有者或具备保留其 UID/GID 权限的身份运行：

```sh
# Linux：使用刚创建并挂载的新池
.venv/bin/python -m len_bot.next.maintenance.storage_pool_admin move-ext4 \
  --image /opt/lenbot/instance/state/task-pool.img \
  --mount /opt/lenbot/instance/state/task-pool

# macOS：填写创建结果中的实际卷 UUID
.venv/bin/python -m len_bot.next.maintenance.storage_pool_admin move-apfs \
  --mount /Users/你的用户名/lenbot-instance/state/task-pool \
  --volume-uuid 创建结果中的UUID
```

命令持有实例锁，复制原工作根（含共享资料）与运行根到新池的空目标，保留内容、所有权、权限和硬链接；宿主绝对链接随根目录更新，容器内路径和 Pi 会话正文不改。然后只切换根配置中的池绑定与两个任务根，最后移除旧任务树。数据库、任务 ID、原事件、角色、模型和独立交付保持。

新池可用于首次入池，也可用于 APFS 扩容后的再次移交。旧池的镜像／卷本身不由入池命令删除；完成移交后，由运维明确卸载和移除不再使用的旧池。

复制阶段失败时原配置与源树保持。发布目录或切换配置后遇到错误，错误末尾列出已经复制的目标、配置是否切换及已删除的源树；按该结果处理残留目录，不自动重试、回退或覆盖已有目标。只读旧目录导致删除失败时，新配置和完整目标仍保留。

## 挂载、读取与扩容

根配置只记录文件系统位置：

```json
{"kind":"ext4","mount":"/opt/lenbot/instance/state/task-pool","image":"/opt/lenbot/instance/state/task-pool.img"}
```

APFS 使用 `kind: "apfs"`、`mount` 和创建输出中的真实 `volume_uuid`。挂载／扩容命令从实例根配置读取绑定，不接受另一套运行参数：

```sh
# 只读，可在宿主运行时执行
.venv/bin/python -m len_bot.next.maintenance.storage_pool_admin status
# 停止实例后，挂载已配置但当前未挂载的池
.venv/bin/python -m len_bot.next.maintenance.storage_pool_admin mount
# 停止实例后，扩展已挂载的 ext4 池
.venv/bin/python -m len_bot.next.maintenance.storage_pool_admin grow --size-gib 64
```

挂载和扩容使用实例锁。扩容只增大镜像和文件系统，不缩小；APFS 使用新建更大 quota 卷后离线移交的路径。池没有挂载或 APFS 没有 quota 时，任务不会转而写入一个不限额的普通目录。

## Docker 与服务路径

- macOS 原生 LenBot 宿主读取 APFS，Docker Desktop 执行工作任务；池目录加入 Desktop 的文件共享范围，先挂载池，再启动任务环境。
- Linux 原生／容器宿主使用 ext4。格式化、挂载与扩容在 Linux Docker 主机执行，运行宿主不需要磁盘管理权限。
- 容器宿主把池的整个挂载根按相同绝对路径 bind mount 进去，任务容器继续使用同一套绝对路径；先挂载池，再启动宿主容器，不依赖后续挂载传播。镜像包含用于读取挂载信息的 `findmnt`。[bind mount](https://docs.docker.com/engine/storage/bind-mounts/)。
- systemd 使用实例外池目录时，将该目录加入 `ReadWritePaths`，并为服务设置对应的挂载依赖。

从实例根生成对应的接线材料：

```sh
.venv/bin/python -m len_bot.next.maintenance.storage_pool_admin deployment > task-pool-deployment.json
```

Linux 输出 `fstab_line`、`systemd_dropin` 和 `compose_override`：fstab 使用 `noauto`，systemd 的 `RequiresMountsFor` 在启动实例时拉起所需挂载；drop-in 还开放该池的服务写入路径。[systemd 挂载依赖](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.unit.xml)。将 drop-in 安装为 `lenbot.service.d/task-pool.conf` 并执行 `systemctl daemon-reload`；原服务启动入口保持。

`compose_override` 是宿主服务的存储覆盖片段，与基础 Compose 配方合并，不是独立发行包。它按根配置生成实例根、整个池与 Docker socket 的同路径绑定，采用配置文件所有者身份及 socket 的实际组；禁止 Compose 隐式创建缺失的挂载源目录。[Compose bind 配置](https://docs.docker.com/reference/compose-file/services/#long-syntax-5)。宿主镜像的 Docker CLI 路径须与输出的 `docker_cli_path` 一致，镜像依赖与基础配方随发行阶段完成。

macOS 输出原生宿主工作目录、明确挂载命令和 Docker Desktop 需要共享的池路径；不把 APFS 配额读取转交给 Linux 宿主容器。

资源页区分池容量和各任务目录用量。文件内容大小、文件系统分配字节、当前可写空间是不同数值；这个版本不提供每任务独立硬配额。
