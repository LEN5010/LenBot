# len_bot 工程约束

当前实现与阶段状态见 [架构](docs/architecture.md) 和 [实施记录](docs/implementation.md)。ADR 记录当时决策，被取代的历史说明不是当前实现要求。

## 不变量

1. Event → Runtime State：事件追加且不可变；Session、任务、OpenLoop、记忆由事件或获准提案更新。
2. 插件和适配器提供感知与工具，不直接调用认知或发送。Social Core 只有提案权，表达必须经过 Gate。
3. SQLite 保存持续事实；模型上下文是临时的，不能成为永久会话或唯一状态来源。
4. Reflection 仅提出认识、版本化补丁和待核对事项，不创建工作、任务或发送。
5. 软兴趣、话题与 retained_attention 不自动成为任务，需要显式认知提案。
6. SceneActor 是 Session 单写者，不等待模型；验证 episode lease、实际读取截点与 social_revision。
7. 普通聊天按实际截点提交；任务、工作控制、履约和 OpenLoop 严格新鲜。未读输入保留，不能用清空 mailbox 掩盖。
8. 记忆、工作状态、回执和行动提案原子提交，冲突回滚且不能发送确认。原始证据和被撤销认识保留。
9. 检索、工具观察和媒体在 SQL/存储层强制场景隔离；global-safe 由运营显式发布，不靠提示词限制越权。
10. 不自动每消息 RAG；Social Core 判断回忆、查询、参与和沉默，禁止用关键词、随机性、固定套话替代社会判断。
11. sent/not_sent/rejected/unknown 明确区分；真实 MESSAGE_SENT 才证明送达、激活 OpenLoop 或确认履约。Shadow 与模拟事实分开，不确定发送不重试。
12. 账号昵称、群名片是事件事实；偏好称呼和反馈是有证据的增量理解。列表添加/移除，省略不清空，同名不合并。

## 认知与配置

唯一社会认知入口为 SocialCognitionCore。普通轮次五个模型步骤、六次工具执行，格式修复计入步骤。最终草稿后最多续接一次，至少剩三个步骤及工具额度才能吸收新请求，否则留给下一轮。独立工作预算见新 ADR。

运营样例按固定顺序携带；人格预设经面板显式预览应用并保留人工编辑，启动不覆盖。角色资料、模型摘要和自我猜测不是群友事实证据。

## 模块边界

- events/store：事件、持久化和事务；scenes：事实 reducer 与单写 Actor。
- cognition：临时理解与提案；runtime：生命周期、Gate、预算和指标。
- scheduler/actions：持久认领、调度、传输与回执。
- memory：认识修订及反思，无执行权。
- plugins/tools：工具发现、只读能力和隔离检索；adapters：协议归一化与获准传输。
- web：所有读取经过 RuntimeQueryService，干预使用事件/提案接口，路由不直接读取私有容器或数据库。
- testing：复用生产链路的隔离回放，模拟不能伪装成真实成功。

## 开发流程

使用 uv，按批准阶段分别提交，代码、对应文档和验证记录一同更新。过时且有效信息已迁出的文档可以删除，失败证据保留。

```sh
uv run pytest -q --tb=short
```

涉及前端时在 `src/len_bot/web/frontend` 执行 `npm run build` 并验证面板。检查 `git diff --check` 与 Markdown 引用。边界由确定性测试验证，模型自然度另测，缺配置或失败不得模拟通过。

升级前停机备份；测试与模型对照使用隔离库。仅按运营批准的场景策略实发，不自动更换生产提供商、模型或人格。
