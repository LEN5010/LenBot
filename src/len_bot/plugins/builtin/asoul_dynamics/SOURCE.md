# 动态站接口来源

- 接口参考：[LEN5010/astrbot_plugin_dynamic_asoul](https://github.com/LEN5010/astrbot_plugin_dynamic_asoul)。
- 参考版本：`7ccd9007b12d3cabdd174d7a53fefefd525ce4f8`，`metadata.yaml` 原作者为 `LEN5010`。
- 实际接口来自 `dynamic_client.py` 的 `/search`、`/on-this-day`、`/fanart`；字段参考 `dynamic_models.py`，并核对过公开 `/search` 的真实 JSON 响应。
- 源站为 [A-SOUL 动态查询站](https://len5010.top/dynamics)。成员筛选使用其 `uid:<bilibili_uid>` 协议，分页使用源站 `nextCursor`。
- 此版本上游未包含 LICENSE 文件；这里保留来源事实，不为上游补写或假定许可证。实现只使用已记录的接口协议，没有移植 AstrBot 装饰器、自动重试、双 HTTP 客户端或卡片素材。

本站没有已确认的动态详情接口。`read_asoul_dynamic` 读取本进程已取得且仍新鲜的源记录，明确标记源查询范围；原平台详情和图片像素不由该工具假定取得。
