OpenViking 后端使用原生路径 memories/... 或 peers/<真实QQ>/memories/...。
write 和 delete 按返回值说明实际刷新结果；history 读取当前路径最近100条原生快照，不等于每次修改都有历史；未创建的快照不能补查。当前没有 forget 能力，普通删除不清除服务快照、归档和备份，修改原因不会自动保存为服务端历史。
