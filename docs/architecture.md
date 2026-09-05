# 当前架构

面向维护者。日期：2026-09-06。实施状态见 [实施记录](implementation.md)。

## 生产路径

OneBot、感知插件和 Scheduler 产生事件，SceneActor 将不可变事件、事实状态和场景会话原子保存。BurstAssembler 仅按时间聚合。单一 SocialCognitionCore 读取工作认识与近期原话，按需使用工具，提出表达、任务、记忆修订及稀疏状态更新。Actor 校验执行权、读取截点和版本，Gate 原子提交，ActionQueue 发送后将真实回执作为事件保存。

SQLite 是持续事实来源，模型不持有永久会话。普通聊天可以按实际截点提交，任务/OpenLoop 严格要求新输入；最终续接有界，未消费输入保持 pending。最终续接后可以确认本轮已读的连续工具观察，不能越过未读人类输入推进截点。

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

Actor 不等待模型，Gate 保持原子性和送达边界。旧 Attention、ParticipationThread、FAST/FULL、样例轮换和风格重试已退出当前生产路径。

## 工具与信息工作

工具执行在 scoped RetrievalToolkit 中包装为 ToolResult。只读外部调用支持同轮去重和刷新；本地历史/记忆查询保持新鲜。长资料保存在 tool_observations，通过结果 ID 与场景读取；TOOL_OBSERVATION_RECORDED 不触发新的社会轮次。原始外部观察与派生检索内容通过 evidence_kind 区分。

Social Core → JobProposal → Actor/Gate → tasks/agent_jobs 原子提交 → Scheduler 持久认领 → InformationJobRunner。执行器步骤和观察保留账目，不推进社会输入截点；AGENT_JOB_PROGRESS/FINISHED 回到普通 Social Core。工作结果与发送完成分开，版本校验覆盖提案提交和实际发送。恢复时 processing 转 review_required，显式 resume 复用资料和预算，不重放发送。详细决策见 [ADR-0043](adr/0043-runtime-owned-information-work.md)。

## 图片与表达

原始图片登记随 Event 事务，缓存和运营更新形成 MEDIA_UPDATED 事件；模型只获得资产 ID，通过 scope 读取。MediaService 负责受控 IO、校验及独立视觉路由，不直接发送。视觉模型解释与原始图片证据分开。出站 segments 经 Gate 校验、运营拦截和资源准备，由 OneBot 编码为数组。队列按 scene 保序，跨 scene 独立，失败或未知中止同组片段，发送前重新验证资产与工作状态。

运营样例稳定提供，角色资料与群聊事实分开。diana-v3 预览展示具体样例，保留已有样例 ID、人工修改和启停；启动不自动应用。群友的原话、引用和相处反馈由 Social Core 理解，按有证据的稀疏更新保存；没有独立人工评分或回复观察窗口。

## 发送配置与观察

实发群名单和全局 Shadow 是唯一运营发送控制，持久化后重启恢复。默认全局 Shadow 开启，初始实发群名单为 `group:126300994`。只有名单内且全局 Shadow 关闭时才能实际发送；Gate 保留工作和消息的起始模式，队列在实际投递前检查当前配置。Shadow 不产生 MESSAGE_SENT、OpenLoop 激活或履约事实；发送结果 unknown 不自动重试。

源码、模型或人格变化不重置发送配置、不要求重新验收，也不批量改写既有任务。旧版本已形成的核对项保留，仍经实际工作和任务控制处理。

面板提供配置、工作、素材、记忆、事件、trace 与运行日志。模型、工具、队列和发送耗时保留在原有记录中，定位问题时按实际链路查看。已删除评测页、打分 API、报告导入与样本/分数门槛。历史评测事件保留为历史资料，不能作为独立记忆证据。

群聊效果主要靠真实群聊判断。内部 ReplayLab 仅作为现有确定性测试的隔离工具，复用生产链路；它没有日常面板入口，也不是开放实发的前置步骤。真实模型曾出现纠错和来源依据不稳定，已知文字型号不支持图片；这些问题在使用中继续核对，不用脚本通过代替真实效果。
