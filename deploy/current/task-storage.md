# 实例任务存储池

工作区、运行目录和共享资料可以放在一个有硬性容量上限的文件系统中，独立交付的文件留在存储池之外。LenBot 读取文件系统的实际容量，不用配置中的数字模拟配额。任务写满存储池时，返回文件系统的原始错误。

存储池工具提供以下功能。

- 创建新的存储池
- 把已有数据移入存储池
- 挂载
- 读取状态
- ext4 扩容
- 生成部署配置片段

日常运行时只读取根配置，不会自动迁移数据或挂载存储池。

## 创建新池

以下命令只创建新的存储池。它们不修改根配置，不移动已有文件，也不启动宿主。请以实例的运行用户执行，需要磁盘管理权限的系统命令会通过 sudo 调用。`--uid/--gid` 指定实例运行用户对存储池目录的所有权。

在 Linux 上需要 `e2fsprogs` 和 `util-linux`。选择一个新的镜像文件和一个空的挂载目录，例如：

```sh
.venv/bin/python -m len_bot.next.maintenance.storage_pool_admin create-ext4 \
  --image /opt/lenbot/instance/state/task-pool.img \
  --mount /opt/lenbot/instance/state/task-pool \
  --size-gib 32 --uid "$(id -u)" --gid "$(id -g)"
```

镜像的固定容量就是整个 ext4 文件系统的上限，共享资料和所有任务都使用这个存储池。创建时不会覆盖已有的镜像。

在 macOS 上，先用 `diskutil apfs list` 查看实际的 APFS 容器，再添加一个独立的、区分大小写的 APFS 卷。把下面命令中的容器和目录替换为实际的值。

```sh
.venv/bin/python -m len_bot.next.maintenance.storage_pool_admin create-apfs \
  --container disk3 --name LenBotTasks \
  --mount /Users/你的用户名/lenbot-instance/state/task-pool \
  --size-gib 32 --uid "$(id -u)" --gid "$(id -g)"
```

命令会输出 `storage_pool`、`workspace_root` 和 `runtime_root` 三个字段，以及读取到的实际容量。下一步的数据入池命令会把这三项写入配置，交付目录和其他配置保持不变。APFS quota 只限制当前卷，但同一容器中其他卷占用的空间也会影响当前卷的可写空间。参考 [APFS 卷与大小选项](https://support.apple.com/en-asia/guide/disk-utility/dskua9e6a110/mac)。

## 将已有工作数据移入新池

开始前请完成以下准备。

1. 停止宿主。
2. 按[升级与备份](operations.md#升级与备份)中的说明保护好实例数据。
3. 结束仍然占用容器或浏览器的任务。

在原实例目录中执行下面的命令。执行身份必须是现有文件的所有者，或者有权限保留这些文件 UID/GID 的身份。

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

命令执行期间持有实例锁，按以下顺序操作。

1. 把原来的工作根目录（包括共享资料）和运行根目录复制到新池中的空目标目录。文件内容和所有权会保留，权限和硬链接也会保留。宿主中的绝对链接随根目录更新，容器内的路径和 Pi 会话的正文不变。
2. 在根配置中切换存储池绑定和两个任务根目录，不改动其他配置。
3. 移除旧的任务目录树。

数据库、任务 ID 和原始事件保持不变，角色、模型和独立交付也保持不变。

新池可以用于首次入池，也可以用于 APFS 扩容后的再次移交。入池命令不会删除旧池的镜像或卷。移交完成后，需要由运维人员明确卸载并移除不再使用的旧池。

复制阶段失败时，原配置和源目录树保持不变。如果在发布目录或切换配置之后出错，错误信息末尾会列出三项结果，请据此处理残留的目录。

- 已经复制的目标
- 配置是否已经切换
- 已经删除的源目录树

命令不会自动重试或回退，也不会覆盖已有的目标。如果因为旧目录只读而删除失败，新配置和完整的目标目录仍然保留。

## 挂载、读取与扩容

根配置只记录文件系统的位置。

```json
{"kind":"ext4","mount":"/opt/lenbot/instance/state/task-pool","image":"/opt/lenbot/instance/state/task-pool.img"}
```

APFS 使用 `kind: "apfs"` 和 `mount`，以及创建时输出的真实 `volume_uuid`。挂载和扩容命令从实例的根配置读取绑定，不接受另外一套运行参数。

```sh
# 只读，可在宿主运行时执行
.venv/bin/python -m len_bot.next.maintenance.storage_pool_admin status
# 停止实例后，挂载已配置但当前未挂载的池
.venv/bin/python -m len_bot.next.maintenance.storage_pool_admin mount
# 停止实例后，扩展已挂载的 ext4 池
.venv/bin/python -m len_bot.next.maintenance.storage_pool_admin grow --size-gib 64
```

挂载和扩容会持有实例锁。扩容只增大镜像和文件系统，不支持缩小。APFS 的扩容方法是新建一个 quota 更大的卷，然后离线移交数据。存储池没有挂载，或者 APFS 卷没有设置 quota 时，任务不会改为写入一个没有容量限制的普通目录。

## Docker 与服务路径

- **macOS**。原生运行的 LenBot 宿主读取 APFS，由 Docker Desktop 执行工作任务。请把存储池目录加入 Docker Desktop 的文件共享范围，先挂载存储池，再启动任务环境。
- **Linux**。原生运行或在容器中运行的宿主都使用 ext4。格式化、挂载和扩容在 Linux Docker 主机上执行，运行宿主的身份不需要磁盘管理权限。
- **容器中运行的宿主**。把存储池的整个挂载根目录以相同的绝对路径 bind mount 进宿主容器，任务容器继续使用同一套绝对路径。先挂载存储池，再启动宿主容器，不依赖之后的挂载传播。镜像中包含用于读取挂载信息的 `findmnt`。参考 [bind mount](https://docs.docker.com/engine/storage/bind-mounts/)。
- **systemd**。存储池目录在实例以外时，把这个目录加入 `ReadWritePaths`，并为服务设置对应的挂载依赖。

在实例根目录执行下面的命令，生成对应的部署配置片段。

```sh
.venv/bin/python -m len_bot.next.maintenance.storage_pool_admin deployment > task-pool-deployment.json
```

在 Linux 上，命令输出 `fstab_line`、`systemd_dropin` 和 `compose_override` 三项。

- fstab 中使用 `noauto`。启动实例时，systemd 的 `RequiresMountsFor` 会挂载所需的文件系统。参考 [systemd 挂载依赖](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.unit.xml)。
- drop-in 还会为服务开放这个存储池的写入路径。把它安装为 `lenbot.service.d/task-pool.conf`，然后执行 `systemctl daemon-reload`。原来服务的启动入口保持不变。
- `compose_override` 是宿主服务的存储覆盖片段，需要与基础 Compose 配置合并使用，本身不能作为独立的发行包。它按根配置生成三项同路径绑定，分别是实例根目录、整个存储池和 Docker socket。运行身份使用配置文件所有者的身份，以及 socket 的实际所属组。它禁止 Compose 自动创建缺失的挂载源目录。参考 [Compose bind 配置](https://docs.docker.com/reference/compose-file/services/#long-syntax-5)。宿主镜像中 Docker CLI 的路径必须与输出的 `docker_cli_path` 一致。镜像依赖和基础配置随发行阶段完成。

在 macOS 上，命令输出原生宿主的工作目录和明确的挂载命令，以及 Docker Desktop 需要共享的存储池路径。APFS 配额的读取不交给 Linux 宿主容器。

资源页分别显示存储池的容量和各个任务目录的用量。文件内容的大小、文件系统分配的字节数和当前可写空间是三个不同的数值。这个版本不提供每个任务单独的硬性配额。
