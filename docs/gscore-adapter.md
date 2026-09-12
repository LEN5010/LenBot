# GSUID Core 适配边界

`gscore_adapter` 是一个默认停用的原生插件。它只在场景配置明确允许时，把精确的 `/gs <命令>` 转成独立 GSUID Core 的 `MessageReceive` 帧，再把目标为已配置群的 `MessageSend` 转回 LenBot 的插件事件。`MessageSend.echo` 只在 Core 请求时用于 `recall_message_id` 回执；它不代表 Core 请求已完成。普通群聊不会上报给 Core，Core 也不能直接访问 OneBot、LenBot 数据库或发送队列。

启用前需要确认 Core 的 WebSocket 端点、认证方式、bot id 和 Core 版本协议。LenBot 只实现当前协议模型中声明的消息段；未知消息段会被保留在观察中但不会假定为可发送内容。发送前仍经过现有场景 Gate、Proposal/ActionQueue 和真实回执链。

示例配置在 `lenbot.config.example.json`，插件和场景均默认关闭。连接身份 `core_bot_id`、上行平台身份 `platform_bot_id` 和 `bot_self_id` 分开配置。该适配器没有搬运 AstrBot 插件运行时，也不会自动安装游戏插件。协议细节与来源记录在插件目录的 `SOURCE.md`。
