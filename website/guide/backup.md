# 备份

## 升级时的自动快照

部署包和 Docker 在每次升级前都会停机做一份完整快照，放在安装目录的 `backups/` 下，升级失败或升级后不满意都可以从更新页恢复。快照不会自动删除，确认新版本没问题后，可以自己删掉旧快照。

## 手动备份

停掉 LenBot 后，备份这些：

- **实例目录**：部署包是 `instance/`；源码运行是仓库根目录；Docker 是数据卷 `lenbot-data`。里面有配置、数据库、角色、记忆、插件和插件数据。
- **实例外的目录**：配置里指向实例外的角色目录和插件目录，以及后台任务的工作、运行和交付目录。

SQLite 的 `-wal`、`-shm` 文件也是数据，要和数据库一起备份。运行中直接复制数据库可能得到不一致的副本，一定要先停机。

`lenbot.config.json` 里有模型、OneBot 和面板的密钥，备份文件按密钥的标准保管。

Docker 卷可以用一个临时容器打包：

```sh
docker run --rm --mount type=volume,source=lenbot-data,target=/data,readonly \
  -v "$PWD":/backup alpine tar -czf /backup/lenbot-data.tar.gz -C /data .
```

## 恢复

把备份放回原位置，用和备份时同一个版本的程序启动。新版本的数据旧程序读不了；要回退版本，用升级前的快照恢复。
