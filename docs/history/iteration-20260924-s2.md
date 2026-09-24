# 2026-09-24 会话段摘要范围形状修正

任务状态只见[产品路线](../plan/README.md)，当前交接见[当前任务](../iteration.md)。本页归档对应批次的静态核对，不是段保存或恢复的运行证据。

阶段提交：`c95e221`。

分支 `feat/s0-product-contract`，基线 `5145cf1`。上一批 `SegmentSummaryRef.range` 声明为严格 Python 元组，而 `ConversationContext` 的历史摘要侧记给出四项列表，`scene_sessions.state_json` 以 JSON 数组保存和重载；原字段形状与实际装配／持久化输入不一致。本批沿原字段改为长度恰好四项的 `list[int]`，不加转换层、表或新状态。批次 ID、字符串版本、位置顺序、Actor 租约资格与写事务不变，旧段无该字段仍读取为空列表。

同步[段结构](../plan/s2-01-conversation-segment.md#3-最小持久结构)和[上下文装配](../context.md#当前段的原话窗口引用)。静态核对 `context.build` 的四项数组、`social_core.finalize_request` 到 Actor 的列表传递、`model_dump(mode='json')` 与 `SceneSession.model_validate(saved)` 读取路径。没有接触或转换真实业务数据；此修正不证明原生交换、压缩或完整段完成。

`uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/scenes/models.py` 和 `git diff --check` 均退出 0，仅证明语法及差异格式。未运行同版段保存、重载、服务、模型或平台，未新增、修改或运行测试、夹具、断言式探针、截图、回放、故障注入、压力或覆盖率任务。没有本批业务失败原文。

S2 仍为 `source_window_only`；固定材料版本、跨轮原生交换与压缩交接未完成，必要回复片段持久边界待维护者答复。同版人工核对、首选 Linux／SnowLuma 现场、数据期限、许可证／素材授权、公开承诺与发布决定仍未完成。只做阶段性本地提交，不推送、合并、部署或实发。
