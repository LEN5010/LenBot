# GSUID Core 桥接

在根配置 `plugins.gscore_adapter` 显式填写 `ws_url`，按需要填写 `access_token`，并在相应 `scenes.<场景>.plugins` 加入 `gscore_adapter`。在「能力 → 插件」保存配置会定向重载，再选择使用场景。本插件连接已运行的 Core，不启动 Core 或修改其账号库。

- `/gs <命令>`：仅把明确命令交到Core；WebSocket写出不代表游戏操作已完成。
- `/gs连接`：连接已断开时显式再连接一次，已连接时不重置连接。
- 低频工具 `gscore_status`：实际连接、进程内计数、最近错误；仍受角色工具许可限制。

图文响应统一经宿主发送，原图保存在现有媒体存储，回执如实区分失败、未确认和模拟。支持范围及上游协议见 [SOURCE.md](SOURCE.md)。命令转发不调用模型；`gscore_status` 是提供给模型的状态工具。

配置示例（面板使用相同字段）：

```json
{"ws_url": "ws://127.0.0.1:8765/ws/lenbot", "access_token": "", "token_query_parameter": "token"}
```

将 `ws_url` 换成 Core 实际端点，令牌填在独立密钥字段。`timeout_seconds` 默认 10 秒，`max_frame_bytes` 默认 20,000,000 字节。
