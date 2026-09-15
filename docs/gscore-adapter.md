# GSUID Core 适配边界

本文说明 `2c862c6` 的适配器代码边界；Core 是可选后端，真实部署和联调状态只看 [当前任务](iteration.md)。

`gscore_adapter` 是一个默认停用的原生插件。它只在场景配置明确允许时，把精确的 `/gs <命令>` 转成独立 GSUID Core 的 `MessageReceive` 帧，再把目标为已配置群的 `MessageSend` 转回 LenBot 的插件事件。`MessageSend.echo` 只在 Core 请求时用于 `recall_message_id` 回执；它不代表 Core 请求已完成。普通群聊不会通过该适配器上报给 Core；适配器不提供直接操作 OneBot、LenBot 数据库或发送队列的接口，网络访问隔离仍需部署保证。

启用前需要确认 Core 的 WebSocket 端点、认证方式、bot id 和 Core 版本协议。LenBot 只实现当前协议模型中声明的消息段；未知消息段会被保留在观察中但不会假定为可发送内容。发送前仍经过现有场景 Gate、Proposal/ActionQueue 和真实回执链。

全局停用示例在 [lenbot.config.example.json](../lenbot.config.example.json)，样例 scenes 为空，实际群条目需按插件 Schema 显式配置。连接身份 `core_bot_id`、上行平台身份 `platform_bot_id` 和 `bot_self_id` 分开配置。该适配器没有搬运 AstrBot 插件运行时，也不会自动安装游戏插件。协议细节见 [SOURCE.md](../src/len_bot/plugins/builtin/gscore_adapter/SOURCE.md)，操作见 [运行手册](operations.md)，后续支持矩阵属于完整计划 C27。
