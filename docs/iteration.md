# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 feat/s0-product-contract，基线 9d9daf1，开始时工作区干净；上一阶段 S0-01／S0-05 基线索引与唯一文档入口已本地转待复核。
- S1-01：原身份关系与只读关联查询已有，但当前架构合同没有一张可直接使用的身份／阶段表。复用现有字段与查询，不新增 episode 表、追踪 ID 或第二套事件系统。

## 本批交付与核对

在架构主链下增加现有 event/source、episode/checkpoint、call/tool/result、job/revision、action/commit 和回执／平台 ID 的归属表。分别说明真实连接与不能推断的状态：来源覆盖不是结论证据，调用登记不证明发送，工具结果保存不授已读，提交／入队不等于送达，旧工作版本不能取得当前成果资格。answer_basis 仍是逐答复依据，不从 source/covers 自动产生；会话段不替代执行、工作或行动身份。表只总结已有源码和查询，不写新业务状态。

源码核对：普通 episode 在 Runtime 生成或等待恢复沿用；ModelGateway 原 model_calls 取得 call_id；原生 tool_call_id 随观察保存；Gate 分配 action_ids 并同序写提交 payload，ActionQueue 的发送尝试和最终事件回指 action_id，sent 还需对应平台 message_id／file_id。RuntimeQueryService.relations 按已存身份查询与扩展，不使用时间相近或正文相似。旧对象缺身份时不猜造关系。上述是静态路径核对，尚无同版真实关系链或页面操作记录，S1-01 仅转待复核。

- 阅读 Runtime 普通轮次、Actor 提交、Gateway 调用登记、工具观察、Gate 行动分配、ActionQueue 回执和 RuntimeQueryService.relations 的真实路径；对照原 S1 基线报告，不沿其旧行号推断新代码。
- 本批仅架构合同与路线状态文档改变，git diff --check 无格式错误；未修改 Python 或前端，不重复编译构建。没有同版服务，关联查询与回执待人工验收。
- 初次编辑时工具返回 `apply_patch verification failed: Failed to find expected lines in /Users/len5010/code/len_bot/docs/architecture.md`；改用实际小节标题定位后完成。该错误是文档补丁上下文不匹配，不是业务运行失败。本批无编译构建或实际业务失败原文。
- 未运行测试、夹具、断言探针、自动截图、回放、故障注入、覆盖率、依赖安装、服务、模型／平台调用或实发；未读取真实配置和业务库。

## 待决定与接续

1. S1-01 的同版普通回复／确定性插件／工作交付关联仍待获准人工观察；S1-02 动态定义与材料、S1-04 未登记等待、S1-06 单条可读时间线仍未全面验收，不把表格当实链通过。
2. S2 完整原生交换、固定材料基线与压缩交接有源码缺口；额外原生字段及必要回复片段保存边界待维护者答复。S0-02／04／06 的保留、授权、精确支持组合与公开承诺未决定。
3. S3／S4／S5 的同版现场、S5-03 分类期限、S6 候选与远端 CI／升级、S7 外部使用者／作者闭环未完成；没有生产或发布授权。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
