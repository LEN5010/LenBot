# OpenViking 完整遗忘扩展

LenBot 的原生 `forget` 使用 `POST /api/v1/content/forget`。固定上游版本没有这个接口，需要用本目录的 `openviking-forget.patch` 构建服务；宿主不直接操作服务存储，也不把普通删除当成完整遗忘。

## 范围

- 针对当前场景选定的记忆文件，清除正文、向量、相关派生摘要和全部已存快照版本，包括没有分支引用的旧提交。
- 清理归档 `memory_diff.json` 中该文件的历史正文；所选原话按真实 `source_message_ids` 从当前及历史归档副本中移除。同一个归档里的其他消息与其他记忆条目保留。
- 宿主保存所选原话的抽取排除，原聊天与历史模型请求仍保留。外部备份不在范围内；其他文件独立保存的事实不自动删除。此操作也不是永久禁止这个路径将来写入新记忆。
- 受影响快照的 ID 会改变，其他文件的版本内容和时间保留；查看历史要重新读取列表，不能继续使用旧快照 ID。
- 同一用户有正在执行的服务任务时，本次遗忘报错。写入或清理失败会返回已发生的部分与原错，不自动重试。没有新增审批、审查模型或任务事务。

## 构建与升级

配套提交和补丁路径集中在 [`components.json`](../components.json)，同版镜像与对应源码由[发行流程](../releasing.md)生成。

使用部署说明指定的上游提交 `a09a9d20a8e07d08973aee177802d00e08df29e6`，在独立检出中应用补丁：

```sh
git -C /path/to/openviking-source apply /path/to/LenBot/deploy/current/openviking-forget.patch
docker build -f /path/to/LenBot/deploy/current/Dockerfile.openviking \
  -t lenbot-openviking:memory-forget /path/to/openviking-source
```

补丁包含服务接口和原生快照清理，不只替换 Python 路由。停止宿主及记忆服务，备份两边的配置、数据库和服务数据后，明确把服务镜像改为新标签并启动。没有数据格式迁移；原有普通删除、快照查看与恢复继续使用原接口。不要在旧服务上将 `forget` 的错误当作已删除。

## 接口

使用该场景的 user-role key，只能操作自己的用户记忆文件：

```json
{
  "uri": "viking://user/group-80003/peers/70035/memories/lenbot/events.md",
  "source_message_ids": ["original-message-id"]
}
```

`source_message_ids` 必须明确给出，可以是空数组。LenBot 从所选聊天原记录取得这些 ID，不要求模型编造服务内部 ID。

响应报告清理范围、改写归档、命中的源消息，以及快照提交改写和旧对象删除数量；原话排除由宿主同时保存。清理是按文件和所选来源进行的，不是对某个概念做全盘语义删除。

扩展已完成源码构建及本地存储的删除、保留项、权限和后续抽取核对；S3 路径仅完成编译。Compose 使用 `lenbot-openviking:memory-forget`，实例升级须遵循上文停机与数据维护步骤。
