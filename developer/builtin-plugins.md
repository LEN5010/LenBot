# 内置插件

在「能力 → 插件 → 发现」查看用途，进入配置后保存，再为目标场景打开。参数和场景保存定向重载涉及的插件。四个插件随主程序提供和更新，counter 是独立的[教学模板](examples/counter/)。

| 插件 | 群内入口 | 所需能力 | 配置与示例 |
|---|---|---|---|
| clock | `/时间` | 无模型；使用场景时区 | [显示秒数](../src/len_bot/next/builtin_plugins/clock/README.md) |
| rss_broadcast | `/订阅播报 [名称]`；定点自动发送 | RSS 2.0 地址；无模型 | [订阅、cron、场景与 KV](../src/len_bot/next/builtin_plugins/rss_broadcast/README.md) |
| group_digest | `/群总结 [要求]`、`/群工作 要求` | 总结用已有模型绑定；工作用 worker 与发送者任务权限 | [生成、委派与技能](../src/len_bot/next/builtin_plugins/group_digest/README.md) |
| gscore_adapter | `/gs 命令`、`/gs连接` | 已运行的 GSUID Core；状态工具沿角色许可 | [连接、令牌与协议](../src/len_bot/next/builtin_plugins/gscore_adapter/README.md) |

插件详情分别显示目录介绍版本、已安装源码版本与实际加载版本。命令匹配和自动播报可直接发送，处理结果进入后续聊天上下文，不因此额外唤醒主脑。

外部插件可从[静态目录](plugin-catalog.md)或仓库 URL 安装，配置表单取自实际插件清单。
