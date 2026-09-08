# 日程来源与移植说明

- 源仓库：[LEN5010/astrbot_plugin_asoul](https://github.com/LEN5010/astrbot_plugin_asoul)。
- 参考版本：`5a945f695ecaa434ff71d402a344f8e9e40feab0`，原作者标记为 `LEN5010`。
- ICS 折行、文字转义与日期处理参考 `asoul_calendar.py`；Pillow 暖色卡片、时间块与文字测量布局参考 `asoul_render.py`。
- 上游许可证原件保留为 [LICENSE](LICENSE)（GNU AGPL v3）。
- `resources/font.ttf` 保留上游同名字体原件；上游在本版本未提供独立字体许可证文件，本地不另行声明其授权范围。
- 本轮指定的日历源为 [枝江站 ICS](https://asoul.love/calendar.ics)，一次正常 GET 返回 HTTP 403，未取得实时 ICS 内容。运行时只读取根配置 `source_url` 指定的单一源，地址可由运营管理，不提供替代来源。
- 源事件 UID、时区、取消状态与原文保留；不按标题重新编号。

移植保留正常读取和本地图片渲染，移除了原 AstrBot 入口、旧缓存失败续期、图片/文本切换、随机表情选择、默认字体搜索、固定团体成员展开与日程标题合并。
