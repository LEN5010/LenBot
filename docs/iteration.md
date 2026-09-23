# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 feat/s0-product-contract，基线 f0b9fd4，开始时工作区干净；上一阶段压缩固定合同已本地提交，属于实际进展。
- S1-02：历史维护、方法维护与动作审查的固定提示及历史工具定义缺档，复用原请求记录，不新增存储、模型或执行路径。

## 本批交付与核对

LLMReflector 的 finish_history_maintenance 与 query_memory 使用已有 _RecordedToolDefinition，Schema 仍来自 ReflectionOutput／MemoryLookup；未提供认识存储时仍不注册查询工具。原 finalize_request 容量核对之后返回消息列表与固定首消息的副本，附 history_maintenance.contract 定位；原轨迹不被写入侧带信息，不改变下一轮估算。无新回调层、工具或请求。

maintain_candidates 在原输入与预算核对后为固定 system 文本附 skill_maintenance.contract；原 save_skill／skip_skill 已有定义快照，不重复改写。ActionReviewer.approve 在原上下文核对后附 action_review.contract，覆盖源码固定说明及原来已在该文本内的 ReviewDecision Schema。审查工具仍为空，绑定、权限、预算、期限、动作结论与重用规则不变，没有新增审查模型。

组件与定义初始修订均为 1；提示／Schema 改变由维护者递增，原网关剥离定位并比较最终内容后保存匹配快照。沿原调用登记事务和既有界面呈现，不批量回填旧调用。动态批次原话、既有认识、方法候选、工作要求、动作参数及原生回复不进入这些新增快照，待确认的活动段保存边界没有被扩张。

- 阅读三条真实组装路径、AgentLoop 的最终回调顺序、固定模型 Schema 和原窗口／预算检查；历史固定 system 文本没有运行值插入，动作的实际参数仅在 user 消息中，本批不保存它。
- 原 _PromptComponent／_RequestLocation 与 _RecordedToolDefinition 不新增格式版本；原估算先完成，私有信息在网关计量／传输前移除；历史工具附属属性不参与 JSON。请求内容、工具名、参数模型和解析不变。
- uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/skills/learning.py src/len_bot/cognition/action_review.py src/len_bot/memory/reflector.py 退出 0；git diff --check 无格式错误。未修改前端，不重跑前端构建；实际调用记录与页面显示仍待同版人工观察。
- 本批无源码路径定位失败、编译失败或业务失败原文。未运行测试、夹具、断言探针、自动截图、回放、故障注入、覆盖率、依赖安装、服务或模型／平台调用；未读取真实配置／业务库或实发。

## 待决定与接续

1. 接续 S1 剩余请求定位与耗时缺口，动态内容无可还原载体时继续明确缺档，不用静态组件快照宣称完整请求可重建。
2. S2 完整活动段／未登记依赖仍有源码工作；额外原生字段保存、分类期限、许可证／素材授权、精确支持组合与公开承诺仍待决定。
3. S6 的候选、同版现场、远端 CI、升级、外部插件迁移与发布依赖未完成，S7 独立使用者／作者记录尚未取得；原 S3／S4／S5 待人工复核项保持。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标进行中。
