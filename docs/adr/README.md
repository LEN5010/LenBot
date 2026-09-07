# ADR 索引

当前行为由 [ADR-0045：持久注意力、增量维护与可继续的工作](0045-attention-maintenance-and-durable-work.md) 与[架构](../architecture.md)定义，代码与新文档优先。实施与上线状态见[实施记录](../implementation.md)，操作见[运行手册](../operations.md)。

- [ADR-0045](0045-attention-maintenance-and-durable-work.md)：四类读取进度、三模型职责、模型调用账、历史维护、工作检查点与技能闭环。
- [ADR-0044](0044-native-conversation-and-evidence-ledger.md)：原生对话、单一认识账本、Actor/Gate 与真实回执底座继续有效；全量消费、work 反思与旧取消语义已由 0045 替代。
- [ADR-0036](0036-onebot-link-modes-and-action-transport.md)：运行时持有 OneBot 连接与发送传输。

更早已删除 ADR 的原文保留在 Git 历史，不再将失效链接作为当前实施要求。真实失败记录仍保留在 research 与 evaluation 中；不能将新代码或隔离验证改写为过去已经通过。
