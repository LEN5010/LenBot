本地后端使用相对 Markdown 路径，例如 people/<真实平台账号>/profile.md、events/日期-主题.md。
write 保存修改前后正文与原因；history 查看历史；delete 保留历史，forget 移除当前文件、索引和该路径可访问的正文版本。
forget 需要显式选择 exclude_records：它们是 recall_chat 返回的真实原话 record，选择哪些原话相关由你结合内容判断。所选原话此后不再进入后台记忆抽取；[] 表示只清目标文件及其记忆版本，不排除旧输入。未选消息和未来再次讲述仍可被学习。
