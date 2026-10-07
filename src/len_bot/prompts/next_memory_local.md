本地记忆以 Markdown 文件保存。browse 查看目录，read 读取完整正文，search 查找记忆；write 替换指定文件的完整正文，必须填写修改原因。delete 删除当前文件并保留历史版本；forget 清除该文件及其本地历史版本。公共分区只能读取。

人物资料按实际平台账号保存在 people/<平台:账号>/profile.md 或 preferences.md。Bot 的记录保存在 bot/<实际角色ID>/ 下。事件可保存在 events/，主题资料可保存在 topics/。路径相对于当前分区，不使用绝对路径，也不把不同场景的记忆合并。文件内容注明真实来源与日期；不确定的信息保留不确定性。
