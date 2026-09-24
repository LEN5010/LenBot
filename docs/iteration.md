# 当前任务

更新于 2026-09-24。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 `feat/s0-product-contract`，基线 `5145cf1`；上批首选 Linux 报告与文件前置已归[历史记录](history/iteration-20260924-s5.md)，仅完成静态部署说明，未现场验收。
- S2-01／S2-02：上批段摘要引用的覆盖范围由装配侧记给出四项 Python 列表，`scene_sessions.state_json` 也只能以 JSON 数组重载，但新增严格模型声明为 Python 元组。原字段形状与生产／持久化输入不一致；本批直接修正同一字段类型，不另加转换层、表或状态。

## 本批交付与核对

- `SegmentSummaryRef.range` 由仅接收元组改为长度恰好四项的 `list[int]`，与 `ConversationContext` 产生的批次侧记及原 JSON 序列化一致；批次 ID、字符串版本、范围顺序、Actor 资格和写事务不变。旧段无 `summary_refs` 仍按空列表读；本批不回填或转换真实数据。
- 同步[段结构](plan/s2-01-conversation-segment.md#3-最小持久结构)和[上下文装配](context.md#当前段的原话窗口引用)。静态核对 `context.build` 的 `range: [start_rowid,start_offset,end_rowid,end_offset]`、`social_core.finalize_request` 传给 Actor 的列表，以及 `model_dump(mode='json')` 后 `SceneSession.model_validate(saved)` 的读取路径。此修正解决形状冲突，不证明完整段或原生交换已实现。
- `uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/scenes/models.py` 退出 0；`git diff --check` 退出 0。仅证明语法与差异格式；未运行同版段保存或重载，也不把静态推断写成运行通过。
- 未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；未读取真实根配置／业务库、启动服务、调用模型／平台或实发。没有本批实际业务失败原文。

## 待决定与接续

1. S2 的原话窗口和摘要引用仍为 `source_window_only`；固定材料持久版本、跨轮原生交换与压缩交接未完成，回复片段持久边界待维护者答复。段保存／重载、旧字段读取与实际恢复需同版人工核对，不能以编译代替。
2. S1／S3／S4 同版人工核对及 S5 首选 Linux／SnowLuma 报告和文件回执仍待获准现场；数据期限、许可证／素材授权、公开承诺、S6 候选与 S7 外部闭环未完成。
3. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
