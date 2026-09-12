# 协议来源

当前 wire model 对照 `KimigaiiWuyi/astrbot_plugin_gscore_adapter` 的 `models.py` 与 `client.py`（读取日期 2026-09-12；上游仓库未作为运行时依赖）。适配的是 GSUID Core 的 `MessageReceive`、`MessageSend` 和 `recall_message_id` 回执语义：`MessageSend.echo` 是逐帧回执令牌，不是请求完成标志。

LenBot 只把明确 `/gs` 命令上报到 Core，并将目标为已配置群的 `MessageSend` 交给现有场景、Gate、ActionQueue 和 OneBot 链。Core 的游戏插件、账号状态和 token 不在 LenBot 进程中管理。普通文件、合并转发、撤回控制、私聊登录和未知消息段保持未支持并记录为失败，不静默扁平化。

上游适配器以 MIT 许可发布；本文件只记录协议来源，未复制其 AstrBot 代码或运行时。
