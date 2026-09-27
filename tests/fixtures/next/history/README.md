# 离线消息结构脱敏夹具

来源：`.backups/pre-start-20260919-025753/len_bot.db`，仅读此命名快照。
`legacy-redacted.json`（测试按原 7 列 DDL 装入临时 SQLite）从真实已保存事件筛选结构；测试SQLite不是完整旧库。所有正文、昵称、账号、群号、消息/事件/行动 ID、URI、时间与未知嵌套字符串已替换；保留事件/段类型、字段、null、布尔值、发送状态以及样本间实际存在的回复互引。合成值不是平台抓包、原历史文字或模型行为证据。

固定 schema 字段名保留；仅替换值和敏感动态键。样本内重复入站一对保留两个不同 Event.id，其余实际字段完全相同。

本快照的消息收件 `segments=null`、`sender.nickname=null`、simulated 发送样本均为 **0**；不编造这三类。`ACTION_SHADOWED` 是影子记录，不是平台已发送。发送事件保存的是旧动作语义段，不是最终 OneBot wire。

所选标签：
- `duplicate_platform_first`: 1
- `duplicate_platform_second`: 1
- `failed_not_sent`: 1
- `failed_rejected`: 1
- `failed_unknown`: 1
- `group_at`: 1
- `group_image`: 1
- `group_text`: 1
- `linked_reply_to_sent`: 1
- `linked_sent_parent`: 1
- `private_text`: 1
- `sent_text`: 1
- `shadow`: 1
