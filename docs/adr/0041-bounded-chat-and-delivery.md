# ADR-0041: 有界群聊续接与明确发送结果

Status: accepted; final-continuation tool restriction superseded by [ADR-0042](0042-revisable-understanding-and-natural-voice.md). Date: 2026-09-06.

## Context

真实群聊的连续输入使 ADR-0040 的全局最新游标要求产生提交饥饿：一次认知耗尽五步，另一轮耗时 157 秒。发送接口的布尔值又把未连接和协议拒绝合并，甚至让没有适配器的队列误记发送成功。

## Decision

工具步骤继续吸收新事件，最终决定最多续接一次且禁止继续工具，总模型预算仍为五步。普通聊天允许以实际读取截点提交，晚到消息留给下一轮。任务操作、任务确认及履约、未来唤醒、OpenLoop 操作保持严格新鲜度，整份提案原子接受或拒绝。

SceneActor 是唯一 Session 写入者。Runtime 提供已读游标和 social_revision；后者只由认知或反思理解更新递增。Actor 校验执行权、取消、版本和证据后，在 Gate 的同一事务提交理解与副作用提案。合并基于最新事实状态，不回退新事件引用、昵称和发言计数。未读消息不靠清空 mailbox 隐藏，也不假装已经理解。

DeliveryResult 区分 sent、not_sent、rejected、unknown。ActionQueue 保存协议结果与 OneBot message_id，真实发送确认才激活 OpenLoop 或完成任务。缺适配器是 not_sent；请求后异常无法证明未发送时是 unknown。没有跨传输重试，没有不确定发送自动重发。

## Consequences and validation

普通聊天可能与截点后的纠正交错，后续回合负责修正；严格任务仍可能在繁忙场景延后。这是明确的产品取舍，不是关键词社会判断。测试必须持续注入新消息验证有限收束、未读游标恢复、任务拒绝、事实字段保留、取消与发送不确定性。真实模型 Shadow 未通过前不声称自然度改善已验证。

嘉然角色资料和运营编写的表达示例属于可编辑配置，不属于群聊经历或证据。应用人格是显式运营操作，不在启动时反复覆盖人工编辑。原始资料保留来源与日期，资料冲突不伪装为当前事实。
