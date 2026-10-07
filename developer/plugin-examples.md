# 独立插件与示例

LenBot 提供插件接口，以及安装和运行插件的能力。业务插件独立安装和更新，不随宿主一起打包。

| 插件 | 群内入口 | 依赖条件 | 仓库 |
|---|---|---|---|
| group_digest | `/群日报 [小时数]`、`/群总结 [要求]`、`/群工作 要求` | 总结使用已有的模型绑定。群工作需要 worker，发送者也要有任务权限 | [群聊总结](https://github.com/lendevs/lenbot-plugin-group-digest) |
| gscore_adapter | `/gs 命令`、`/gs连接` | 已经运行的 GSUID Core。状态工具遵循角色许可 | [GSUID Core 桥接](https://github.com/lendevs/lenbot-plugin-gscore-adapter) |
| asoul | `/日程`、`/日程高亮`，以及日程、动态、二创的查询和卡片工具 | 需要配置日历和动态站。四个工具遵循角色许可 | [A-SOUL](https://github.com/lendevs/lenbot-plugin-asoul) |
| bilibili | 视频查询和搜索，直播和关注推送，共五个工具 | 公开查询不需要账号。读取账号和执行账号操作时，需要开启配置，并校验真实发送者是主人 | [哔哩哔哩](https://github.com/lendevs/lenbot-plugin-bilibili) |
| counter | `/计数`、`计数加一`、`/计数清零` | 基础计数不需要模型，每个群使用独立的 KV。卡片说明需要模型 | [插件模板](https://github.com/lendevs/lenbot-plugin-template) |

在面板的「插件 → 发现插件」中选择插件安装，也可以填写仓库地址或上传 ZIP。准备完成后填写参数并应用，再为目标群开启。插件详情中可以看到三个版本，分别是目录介绍的版本、已安装的源码版本和实际加载的版本。

从旧的内置版本迁移时，先更新宿主，再安装同名的独立插件，原来的参数、启用的群和插件数据都可以保留。时间查询和 RSS 播报已经移除，旧实例需要从根配置和各群设置中删除 `clock` 和 `rss_broadcast` 的引用。宿主每轮仍然向聊天模型提供按场景时区计算的当前时间。

命令匹配和自动播报可以直接发送消息，处理结果会进入之后的聊天上下文，但不会因此额外唤醒主脑。配置表单取自插件的实际清单。插件发现目录的格式见[静态目录](plugin-catalog.md)。
