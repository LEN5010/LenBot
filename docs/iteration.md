# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 feat/s0-product-contract，基线 36919e7，开始时工作区干净；S6-05 兼容对齐已本地提交，属于实际进展。
- 回到 S1-02：工作压缩调用的固定合同与工具定义仍未登记快照。本批沿原 WorkCompressor 请求和既有记录类型补齐，不因发布准备而跳过核心源码缺口。

## 本批交付与核对

WorkCompressor 在原 summarize_work_segment 定义处使用已有 _RecordedToolDefinition，组件为 core.work_compression.summarize_work_segment，修订 1，Schema 仍来自实际解析的 WorkSegment。原区间选定并确认来源后，在调用前用既有 _RequestLocation／_PromptComponent 标记固定 system 合同 work_compression.contract，修订 1。没有复制第二份提示文本、改变提示内容或增加组件存储表。

提示定位在窗口估算之后附加，原网关先取出私有定位再估算／传输；工具附属属性不参与字典 JSON。沿原 prepare_request_record 比较最终内容，匹配才留快照，再与调用登记在原事务内保存。失败仍按原压缩／调用边界结束，没有新请求、重试、预算维度或降级。原 UI 已可显示格式 v3 的组件和工具快照，不修改页面。

本批只多保留源码固定合同和固定 Schema；动态目标、约束、归档交换、媒体与模型回复未复制进新快照，原压缩区间与结果仍归工作记录。没有扩展待确认的活动段原生回复保存范围，也没有为旧调用补造记录。上下文文档同步组件、修订、估算与可还原范围限制。

- 阅读请求记录、ModelGateway 的复制／剥离／估算顺序、begin_model_call 的原事务、WorkCompressor 的区间选择／来源核对／计数与响应解析，以及现有 RequestRecordDetails 组件显示入口。
- 核对核心提案、读取、普通工作与方法维护定义已使用原快照类型，不重复接入；动态插件 return_result Schema 与其他提示仍有缺口，未顺手扩大保存。当前定位只针对无动态插值的压缩 system 文本。
- 源码检索出现 `rg: src/len_bot/cognition/compaction.py: No such file or directory (os error 2)`；之后按工作运行器真实导入定位 runtime/work_context.py，没有新建猜测路径。
- uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/runtime/work_context.py 退出 0；git diff --check 无格式错误。无前端源码变化，未重跑前端构建。编译不证明新调用记录或页面现场通过。
- 无本批业务失败原文。未运行测试、夹具、断言探针、回放、故障注入、自动截图、覆盖率、依赖安装或服务；未读取业务库／真实根配置、调用模型／平台或实发。

## 待决定与接续

1. 继续 S1 请求材料／耗时的真实缺口，优先复用现有记录和状态；对动态内容仅凭位置不能还原的范围继续明确说明，不擅自复制私人正文。
2. S2 完整活动段／未登记依赖仍有源码工作；额外原生字段保存、分类保留、许可证／素材授权、精确支持组合及公开承诺待决定。
3. S6 各项候选、同版业务、远端 CI、升级、外部插件迁移与发布依赖未完成；S7 独立使用者／作者记录未取得，不将模板或静态清单作为实际闭环。
4. 已有 S3、S4、S5 源码／页面待人工复核项保持；仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
