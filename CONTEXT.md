# 领域术语

此文件只定义现行领域语言；运行路径见 [架构](docs/architecture.md)，实施状态见 [实施记录](docs/implementation.md)。

| 术语 | 含义与边界 |
|---|---|
| Persistent Runtime | 持续存在的 Agent 权威，拥有时间、状态和行动生命周期 |
| Event | 不可变的原始观察或内部事实，不等同于消息或模型结论 |
| Scene | 群聊或私聊的隔离范围，也是检索和证据的权限边界 |
| SceneActor | 场景事实与 Session 的单写者，不等待模型推理 |
| Burst | 仅按到达时间聚合的一组有序场景事件 |
| GroupAgentSession | 持久工作认识，不是模型聊天历史 |
| Social Core | 解释社会场景、按需查询并提出行动的临时认知过程 |
| Observed Cutoff | 该轮实际读到的事件截点，晚到输入保持未读 |
| Social Revision | 社会理解版本，不是输入消息计数 |
| Episode Lease / Mailbox | 一轮社会认知的执行权及步骤边界输入通道 |
| Proposal / RuntimeGate | 候选更新及其权威事务边界，模型没有直接执行权 |
| Cognitive Tier | normal/deliberate 能力路由，不是 FAST/FULL 双轨 |
| Tool Budget / Forced Final | 模型和工具的硬额度，耗尽后必须收束，不假称完成 |
| Tool Observation | 工具取得的原始资料或错误，不等于已证明的现实结论 |
| Agent Job | 由 Runtime 管理的信息型工作，社会认知决定目标和表达，执行器只读工具并返回结果；实现状态见实施记录 |
| Preferred Address | 从发言证据形成的场景称呼偏好，独立于昵称和群名片 |
| Memory Revision | 有证据的撤销/替代，保留原认识及修订理由 |
| Social World / Self State | 对话题、人际与自身参与的可修订认识，不是运行时规则 |
| Retained Attention | 保留兴趣的软状态，不能自动生成任务 |
| Future Attention / Next Wake | 认知提出的未来观察意图，经 Gate 变成持久调度任务 |
| Task | 有来源、目标和触发条件的执行义务，到期不等于履约 |
| OpenLoop | 经确认送达后激活的社会期待，不是后台工作队列 |
| DeliveryResult | sent/not_sent/rejected/unknown 四种事实结果，unknown 不自动重发 |
| Shadow | 只记录候选，没有物理发送和社会送达事实 |
| 实发群名单 | 运营明确允许实发的群；只有名单内且全局 Shadow 关闭时才能实际发送，无消息数或评分门槛 |
| Simulated Delivery | 隔离测试中的虚拟回执，不是真实送达或真实人类互动 |
| Reflection | 提出认识、版本化补丁和核对事项，无任务或发送权 |
| ExecutionScope | SQL/存储层约束的允许场景集合，不能由模型扩大 |
| OneBot Link / Transport | 唯一事件连接及显式选择的发送通道，不作跨通道重试 |
