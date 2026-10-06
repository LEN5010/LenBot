# 独立业务插件

主程序只提供插件接口、运行时、安装更新和配置面板，业务插件独立维护。
在同级工作目录中，四个插件各自是独立 Git 仓库：

| 插件 | 本地仓库目录 | 群内入口 |
|---|---|---|
| clock | `lenbot_plugin_clock` | `/时间` |
| rss_broadcast | `lenbot_plugin_rss_broadcast` | `/订阅播报`、定点播报 |
| group_digest | `lenbot_plugin_group_digest` | `/群总结`、`/群工作` |
| gscore_adapter | `lenbot_plugin_gscore_adapter` | `/gs`、`/gs连接` |

清单、源码、README、许可证、技能、业务提示词和协议测试随插件仓库保存。
本阶段保留本地独立仓库，分发目录暂不列出未发布的 Git 地址；可以将插件打成 ZIP，通过已有导入入口安装。

安装副本、候选和数据统一放在实例 `plugins/`，主仓库 `.gitignore` 已忽略整个目录；它们不属于宿主源码提交或审查。
宿主的测试使用最小接口探针，不依赖这些业务仓库或本机安装副本。

已有实例保留插件名称、参数、场景选择和数据目录；将源码安装到 `plugins/<名字>/`，并使根配置 `plugins.paths` 包含 `plugins`。
后续发布 Git 仓库时，沿用现有候选、应用和更新流程。
