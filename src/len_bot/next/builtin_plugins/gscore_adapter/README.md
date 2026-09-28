# GSUID Core 桥接

在根配置 `plugins.gscore_adapter` 显式填写 `ws_url`，按需要填写 `access_token`，并在相应 `scenes.<场景>.plugins` 加入 `gscore_adapter`。宿主插件面板可以保存这些字段，重启生效；本插件不启动Core或改其账号库。

- `/gs <命令>`：仅把明确命令交到Core；WebSocket写出不代表游戏操作已完成。
- `/gs连接`：连接已断开时显式再连接一次，已连接时不重置连接。
- 低频工具 `gscore_status`：实际连接、进程内计数、最近错误；仍受角色工具许可限制。

图文响应统一经宿主发送，原图保存在现有媒体存储，回执如实区分失败、未确认和模拟。支持范围及上游协议见 [SOURCE.md](SOURCE.md)。本机合成连线不是运营账号或真实QQ验收。
