# 领域术语

此文件定义现行领域语言，供实现与审查时对齐边界。运行路径见 [架构](docs/architecture.md)，决策见 [ADR-0044](docs/adr/0044-native-conversation-and-evidence-ledger.md)。

| 术语 | 含义与边界 |
|---|---|
| Persistent Runtime | 持续运行并管理事件、调度、预算和行动生命周期的权威 |
| Event | 追加且不可变的观察或运行事实；模型结论的记录不等于独立事实证据 |
| Scene | 群聊或私聊的隔离范围，也是历史、工具观察和媒体的存储权限边界 |
| `SceneActor` / `SceneSession` | 单写 Actor 与事实投影，保存身份、游标、版本和收发事实，不保存气氛或自我意愿 |
| Burst | 仅按到达时间聚合的一组有序场景输入 |
| `SocialCognitionCore` | 唯一社会认知入口，决定参与、沉默、回忆与工作目标，并提出表达 |
| Conversation Context | 本轮的原话、原图、明确相处要求及运行资料；不充当永久会话 |
| Read Cutoff | 本轮实际读取的连续事件截点；晚到或未提供的输入继续 pending |
| Knowledge Revision | 认识账本版本，变化后旧认知须重建；不是消息数量 |
| Episode Lease / Mailbox | 一轮认知的执行权与新增输入通道，只确认实际已读截点 |
| Model Profile | `conversation` 或 `work` 的显式提供商、模型和推理强度配置 |
| `ModelGateway` | 固定一次运行的模型绑定，保留供应商原生 assistant 续接数据 |
| `AgentLoop` | 对话、工作和反思共用的原生工具循环，执行预算与终结契约 |
| `TurnReferences` | 本轮短引用到真实 ID 的映射，区分看到来源位置与实际读过证据 |
| `ProposalLedger` | 尚未提交的工作、提醒、认识和表达意向；可在终结前撤回暂存提案 |
| `finish_turn` / `RuntimeGate` | 零至三条文字或图片消息的终结提案，以及授权、事务和发送边界；空列表为沉默 |
| Tool Budget / Forced Final | 模型步骤与工具执行的硬额度；最后一步指定终结工具，失败不能伪装完成 |
| Tool Observation | 工具取得的原始资料、派生内容或错误；持久 result_id 不扩大场景权限 |
| Information Job | 由 `start_work` 提议、Runtime 管理的只读查询、解题或整理工作；结果回到对话入口表达 |
| Work Revision | 工作目标和约束版本；修订保留已用预算、资料和本次运行的模型绑定 |
| Evidence Ledger / Memory | 单一认识账本，保存主体、陈述、依据、来源、有效期和修订链 |
| `reported` / `inferred` | 有人明确报告与从互动推断的认识，均保留来源；不表示已客观核实 |
| Preferred Address | 有原话证据的称呼偏好，独立于协议昵称和群名片 |
| Memory Revision | `create/refute/supersede` 提案；替代或撤销保留旧陈述及修订理由 |
| Reflection | 使用 `work` 模型整理稀疏认识及核对事项，无工作、任务或发送权 |
| Task / Reminder | 有来源与触发时间的执行义务；触发、结果就绪和实际履约分别记录 |
| OpenLoop | 真实发送后激活的等待回应；关闭通过提案提交，过期遵循记录的期限 |
| Native Image / Media Manifest | 实际装入模型的原图及其来源、覆盖范围、未装入或移出状态 |
| Operator Palette | 最多 20 项固定运营目录及编号总览；选择缩略图，发送获准原图 |
| Voice Example | 运营固定顺序提供的文字、图片或混排表达参考，不是群友事实 |
| DeliveryResult | sent/not_sent/rejected/unknown 四种发送结果；unknown 不自动重发 |
| Shadow | 记录候选表达，不产生真实发送、履约或等待回应事实 |
| 实发群名单 | 运营允许实发的群；与全局 Shadow 共同控制实际发送，沿用保存值 |
| Replay / Simulated Delivery | 隔离验证中的输入和回执，不能冒充真实群聊或实际成功 |
| Operator Outcome | 记录操作者事件后经过 Actor/Gate 的干预，不消费未读人类输入 |
| OneBot Link / Transport | 唯一事件连接与显式发送通道，不作不确定发送的跨通道重试 |

GroupAgentSession、Social World、Self State、Retained Attention、Cognitive Tier、独立 vision 路由和 episode 摘要属于已替代设计，不用于描述当前实现。
