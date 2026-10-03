# 实例任务存储池

工作区、运行目录和共享资料使用一个有硬上限的文件系统，独立交付留在池外。LenBot 读取实际容量，不用配置数字模拟配额；任务满盘时返回文件系统原错。

当前已提供创建、挂载、状态读取和 ext4 扩容。已有数据的离线移交入口仍在开发，原实例的目录配置保持。

## 创建新池

以下命令只创建新池，不改根配置、不搬已有文件、不启动宿主。以实例运行用户执行；需要磁盘管理权限的系统命令调用 sudo。`--uid/--gid` 是实例运行用户对池目录的所有权。

Linux 需要 `e2fsprogs` 和 `util-linux`。选择新的镜像文件和空挂载目录，例如：

```sh
.venv/bin/python -m len_bot.next.storage_pool_admin create-ext4 \
  --image /opt/lenbot/instance/state/task-pool.img \
  --mount /opt/lenbot/instance/state/task-pool \
  --size-gib 32 --uid "$(id -u)" --gid "$(id -g)"
```

镜像的固定容量限制整个 ext4 文件系统；共享资料与全部任务使用这个池。创建不覆盖已有镜像。

macOS 先用 `diskutil apfs list` 查看实际容器，再添加一个独立的大小写敏感 APFS 卷；下面的容器和目录替换为实际值：

```sh
.venv/bin/python -m len_bot.next.storage_pool_admin create-apfs \
  --container disk3 --name LenBotTasks \
  --mount /Users/你的用户名/lenbot-instance/state/task-pool \
  --size-gib 32 --uid "$(id -u)" --gid "$(id -g)"
```

命令输出 `storage_pool`、`workspace_root` 和 `runtime_root` 字段，以及读取到的实际容量。全新空实例可在停机时把这三项放入 `worker`；交付根和其他配置保持。APFS quota 限制当前卷，容器中其他卷占用也会影响当前可写空间。[APFS 卷与大小选项](https://support.apple.com/en-asia/guide/disk-utility/dskua9e6a110/mac)。

## 挂载、读取与扩容

根配置只记录文件系统位置：

```json
{"kind":"ext4","mount":"/opt/lenbot/instance/state/task-pool","image":"/opt/lenbot/instance/state/task-pool.img"}
```

APFS 使用 `kind: "apfs"`、`mount` 和创建输出中的真实 `volume_uuid`。挂载／扩容命令从实例根配置读取绑定，不接受另一套运行参数：

```sh
# 只读，可在宿主运行时执行
.venv/bin/python -m len_bot.next.storage_pool_admin status
# 停止实例后，挂载已配置但当前未挂载的池
.venv/bin/python -m len_bot.next.storage_pool_admin mount
# 停止实例后，扩展已挂载的 ext4 池
.venv/bin/python -m len_bot.next.storage_pool_admin grow --size-gib 64
```

挂载和扩容使用实例锁。扩容只增大镜像和文件系统，不缩小；APFS 使用新建更大 quota 卷后离线移交的路径。池没有挂载或 APFS 没有 quota 时，任务不会转而写入一个不限额的普通目录。

## Docker 与服务路径

- macOS 原生 LenBot 宿主读取 APFS，Docker Desktop 执行工作任务；池目录加入 Desktop 的文件共享范围，先挂载池，再启动任务环境。
- Linux 原生／容器宿主使用 ext4。格式化、挂载与扩容在 Linux Docker 主机执行，运行宿主不需要磁盘管理权限。
- 容器宿主把池的整个挂载根按相同绝对路径 bind mount 进去，任务容器继续使用同一套绝对路径；先挂载池，再启动宿主容器，不依赖后续挂载传播。镜像包含用于读取挂载信息的 `findmnt`。[bind mount](https://docs.docker.com/engine/storage/bind-mounts/)。
- systemd 使用实例外池目录时，将该目录加入 `ReadWritePaths`，并为服务设置对应的挂载依赖。

资源页区分池容量和各任务目录用量。文件内容大小、文件系统分配字节、当前可写空间是不同数值；这个版本不提供每任务独立硬配额。
