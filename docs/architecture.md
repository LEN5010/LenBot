# 当前架构

面向维护者。日期：2026-09-06。阶段与验收状态分别见 [实施记录](implementation.md)。

## 生产路径

OneBot、感知插件和 Scheduler 产生事件，SceneActor 将不可变事件、事实状态和场景会话原子保存。BurstAssembler 仅按时间聚合。单一 SocialCognitionCore 读取工作认识与近期原话，按需使用工具，提出表达、任务、记忆修订及稀疏状态更新。Actor 校验执行权、读取截点和版本，Gate 原子提交，ActionQueue 发送后将真实回执作为事件保存。

SQLite 是持续事实来源，模型不持有永久会话。普通聊天可以按实际截点提交，任务/OpenLoop 严格要求新输入；最终续接有界，未消费输入保持 pending。

## 状态所有权

| 状态 | 写入路径 |
|---|---|
| 原始事件与事实 Session | SceneActor → EventStore |
| 社会认识、称呼、反馈 | Social Core 提案 → Actor/Gate |
| 任务、记忆与 OpenLoop | Gate → EventStore 同一事务 |
| 到期/条件命中 | Scheduler 持久认领与待投递事件 |
| 送达 | ActionQueue → MESSAGE_SENT / MESSAGE_SEND_FAILED |
| 反思 | 校验 social_revision/游标后原子提交认识、摘要与核对事件 |
| 配置和样例 | 面板显式保存后热更新，启动不覆盖人格 |

## 保留的设计理由

Actor 不等待模型，Gate 保持原子性和送达边界。独立工作方案见 [ADR-0043](adr/0043-runtime-owned-information-work.md)。旧 Attention、ParticipationThread、FAST/FULL、样例轮换和风格重试已退出当前生产路径。

运营样例稳定提供，角色资料与群聊事实分开。长历史主动检索，SQL scope 隔离。Shadow 不产生社会送达事实，真实自然度不能由脚本测试或原历史群友反应证明。

## 当前限制

阶段 2 已实现结构化工具观察、通用正文读取、资料分页和工具发现；阶段 4 已接入独立工作；媒体理解与发送节奏尚待后续阶段。既有真实模型五例显示纠错和依据不稳定。限制随实施更新，不因批准计划而视为解决。

工具执行在 scoped RetrievalToolkit 中包装为 ToolResult。只读外部调用支持同轮去重和刷新；本地历史/记忆查询保持新鲜。长资料保存在 tool_observations，通过结果 ID 与场景读取；TOOL_OBSERVATION_RECORDED 不触发新的社会轮次。原始外部观察与派生检索内容通过 evidence_kind 区分。

## 交错评测

ReplayLab 复用生产 Actor/Burst/Core/Gate/Queue，六类 checkpoint 支持模型、工具与发送前后注入输入。模拟送达只使用临时数据库和虚拟适配器，事件标 simulated，指标不计 visible_messages；生产 Shadow 没有 MESSAGE_SENT。模型、工具、投递模式独立，所有 prompt/观察/失败可导出。历史 Bot 输出不是新候选，脚本后续输入不是实际人类反馈。

## 信息工作

Social Core → JobProposal → Actor/Gate → tasks/agent_jobs 原子提交 → Scheduler持久认领 → InformationJobRunner。执行器的步骤/观察事件保留账目但不推进社会输入截点；AGENT_JOB_PROGRESS/FINISHED 回到普通 Social Core。工作结果与发送完成分开，版本校验覆盖提案提交和实际发送。恢复时 processing 转 review_required，显式resume复用资料和预算，不重放发送。
