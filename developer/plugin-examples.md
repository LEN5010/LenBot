# 独立插件与示例

LenBot 提供插件接口、安装和运行能力，业务插件独立安装和更新，不随宿主打包。

| 插件 | 群内入口 | 所需能力 | 仓库 |
|---|---|---|---|
| group_digest | `/群日报 [小时数]`、`/群总结 [要求]`、`/群工作 要求` | 总结使用已有模型绑定；群工作需要 worker 与发送者任务权限 | [群聊总结](https://github.com/lendevs/lenbot-plugin-group-digest) |
| gscore_adapter | `/gs 命令`、`/gs连接` | 已运行的 GSUID Core；状态工具沿角色许可 | [GSUID Core 桥接](https://github.com/lendevs/lenbot-plugin-gscore-adapter) |
| asoul | `/日程`、`/日程高亮`；日程、动态、二创查询和卡片工具 | 日历与动态站配置；四个工具沿角色许可 | [A-SOUL](https://github.com/lendevs/lenbot-plugin-asoul) |
| bilibili | 视频与搜索、直播与关注推送；五个工具 | 公开查询无需账号；账号读取与动作需开启配置并校验真实发送者为主人 | [哔哩哔哩](https://github.com/lendevs/lenbot-plugin-bilibili) |
| counter | `/计数`、`计数加一`、`/计数清零` | 基础计数无需模型；每群独立 KV；卡片说明需要模型 | [插件模板](https://github.com/lendevs/lenbot-plugin-template) |

在面板的「插件 → 发现插件」里选插件安装，或者填仓库地址、上传 ZIP。准备好后填参数、应用，再为目标群打开。插件详情里能看到目录介绍的版本、已安装的源码版本和实际加载的版本。

从旧内置版本迁移时，先更新宿主，再安装同名独立插件；原参数、选群和插件数据可以保留。时间查询与 RSS 播报已移除，旧实例需删除根配置和各群中的 `clock`、`rss_broadcast` 引用。宿主每轮仍向聊天模型提供按场景时区计算的当前时间。

命令匹配和自动播报可直接发送，处理结果进入后续聊天上下文，不因此额外唤醒主脑。配置表单取自实际插件清单；发现目录格式见[静态目录](plugin-catalog.md)。
