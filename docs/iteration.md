# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 `feat/s0-product-contract`，基线 `b7ce7f4`，开始时工作区干净；上一批动态简图复用了已有公共随包字体定位，仅编译未做插件装载或双架构镜像重建。
- S2-01／S2-02：当前段只保存原话窗口和 R／J 别名；历史摘要虽在每次请求从原批次筛选，却没有记录哪几个批次实际构成这一段的摘要材料。沿已确认宿主所有权方向，只在原 `scene_sessions.state_json` 的当前段增加批次身份引用，不复制正文或建立新表。

## 本批交付与核对

`ConversationSegment.summary_refs` 保存最终请求实际保留的 history_summary 批次 ID、字符串 `generation_version` 和四个原覆盖位置。`SocialCognitionCore.finalize_request` 在来源复核与最终容量裁剪后，仅当摘要区块未被省略且仍与装配时整条声明一致时提取引用；已撤下、已改写或未装入的区块不登记。`SceneActor` 在原活跃普通对话租约内规范化引用、与上一段比较，变动记为 `binding_changed`，沿原写锁只更新段字段；旧段无新字段按空列表读取。摘要正文、已读资格、未完事项和工作预算仍归各自权威记录；下轮重新查询本群当前可用批次，不从段引用恢复旧文本。

- 阅读 `history_batches.generation_version` 的真实类型为字符串（当前值由历史维护使用），因此段字段使用字符串，不以调用界面的显示格式猜整数。没有更改批次生成、查询、清理或原始事件。
- 同步[上下文装配](context.md#当前段的原话窗口引用)、[结构设计](plan/s2-01-conversation-segment.md)、[数据生命周期](data-lifecycle.md)、[升级回退](operations.md#会话段字段的升级与回退)及路线状态。`source_window_only` 仍表示缺少跨轮原生交换；保存摘要位置不完成段内轨迹或压缩。
- `uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/scenes/models.py src/len_bot/scenes/actor.py src/len_bot/cognition/social_core.py` 退出 0；`git diff --check` 退出 0。只证明语法与差异格式，未运行同版普通对话或恢复。
- 未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；未读取真实根配置／业务库、启动服务、调用模型／平台或实发。没有本批实际业务失败原文。

## 待决定与接续

1. S2 的完整活动段仍缺固定材料持久版本、必要原生交换和终结保存；原生回复片段的持久边界待维护者答复。S2-03 压缩交接尚未实现，不因摘要引用入段而标记完成。
2. 旧程序不认识新增的嵌套字段；获准升级先停机备份，回退条件沿运行手册。本批未转换或接触真实场景状态。
3. S1／S3／S4 多项源码待同版人工复核；S5-01 双架构目标与本机 SnowLuma 协议方向已定，但实际安装、报告／文件回执未获准；保留期限、许可证／素材授权、公开承诺、S6 候选及 S7 外部闭环仍待决定或证据。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
