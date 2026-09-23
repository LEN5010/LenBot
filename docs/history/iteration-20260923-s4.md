# 2026-09-23 群聊体验与工作路径记录

当前状态只见[产品路线](../plan/README.md)，当前交接见[当前任务](../iteration.md)。以下过程与未确认项限定原批次。

## 观察窗口与接话沉默的判读

阶段提交：`5c2fa10`。

### 当前批次

- 分支 `feat/s0-product-contract`，本批基线 `c4120e2`，开始时工作区干净；上一批已本地提交。
- S4-01：核对现有观察机会、快速搭话、有限关注与沉默。本批未发现需要新增机制的明确源码断点，交付参与策略及判读合同，不为了产生代码改动重复实现既有机制。

### 本批核对与交付

新增架构中的“接话、有限关注与沉默的判读”，把读取机会、处理结果与真实送达分开。路线 S4-01 进入待复核：这表示现有源码路径和合同已核对，不代表真实群聊表现已验收。依赖的完整活动段交接仍属 S2，未由本批替代。

- AttentionPolicy.apply：真实提及、回复及私聊形成直接搭话理由；certain 表示读取优先而不是强制回复。名字和关键词经原冷却生成弱机会，不被提升成明确请求；引用片段不参加当前文字的昵称／关键词匹配。
- AttentionPolicy 与 BurstAssembler.ingest：普通观察时刻取已有截止与 now+间隔的较早者；缓冲 max_deadline 也只取较早者，后续输入不能不断推迟最大等待。快、观察、弱机会、周期四条车道仍共用一个缓冲；定时任务须匹配原缓冲和任务身份。没有为说明这一点运行计时探针。
- SceneActor._focus_renewal_actors 与 AttentionPolicy.apply：只有真实 live 送达的关系匹配对象续期 focused_participants，并排除明确释放对象；在途已提交但未回执的关系只作短时线索。observing_until 则可由真实直接输入或本轮新处理原话的明确 continue／end 控制，普通新消息不自行续期。
- 逐项核对配置和字段：有效 focus_seconds 当前仍继承全局；pending_response_actors 读取的内部 MessageProposal.addressed_to 确实存在，与出站回执 response_actor_ids 是不同阶段字段。未把这些已有正确映射当作缺陷改写。
- ScenePolicy、睡眠与 Runtime 的入口／最终资格过滤：快速机会仍受场景、主体、睡眠和额度约束；小时限制、sleep、chat_not_allowed 等拒绝不同于模型主动沉默。等待槽位后仍重新过滤，不能用旧来源绕过关闭或限额。
- ProposalLedger.finish、SceneReducer 与 _preserve_unhandled_bursts：silent 须有理由且不能同时关联实际消息／操作；明确请求不会因为只有原话已读就自动当成处理完毕。普通机会的完整阅读、来源处理及送达是不同事实，空完成不反复购买新一轮预算。
- 只修改文档和路线，没有修改源码、前端或运行参数；因此未额外运行编译、构建或任何测试。git diff --check 无格式错误。源码核对不能证明现场时延、话题结束后的自然沉默或真实对象关联已经正确运行。
- 初始路径查找出现 `rg: src/len_bot/scenes/attention.py: No such file or directory (os error 2)`、`rg: src/len_bot/scenes/scene_policy.py: No such file or directory (os error 2)`，沿 actor.py 实际导入定位 runtime/attention.py、runtime/scene_policy.py。另有 `rg: src/len_bot/cognition/ledger.py: No such file or directory (os error 2)`、`rg: src/len_bot/cognition/decision.py: No such file or directory (os error 2)`，以及 `rg: src/len_bot/cognition/social_ledger.py: IO error for operation on src/len_bot/cognition/social_ledger.py: No such file or directory (os error 2)`；最终用类定义定位 cognition/proposals.py。均为源码定位错误，不是运行失败。
- 没有同版开发面板或授权真实群操作；事件入口、截止冲洗、睡眠／额度拒绝、来源 silent 原因及送达后的关注期限仍待真实记录复核。未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；未启动服务、读取业务库、调用模型／平台或真实发送。

### 待决定与接续

1. 接续 S4-02 的普通查询／计算、分页和长工作选择，以及可由宿主推导的语义合同去重；先核对现有 ProposalLedger 与工具目录，不新增规划流水线，不改人工人格原文。
2. S2 完整活动段的额外原生字段／必要回复片段保存边界仍待维护者回答；宿主所有权不是额外保存授权，不重复提问。未登记自由文本依赖不作自动推断，S2 不标整体完成。
3. S1 仍有材料／耗时口径待补；S3 及本批 S4-01 仅待复核，没有同版人工验收或发布。S4 其他项与 S5—S7 仍有工作，不把当前观察路径核对等同整个群聊体验完成。
4. 许可证、字体／素材授权、长期数据保留、公开支持承诺仍待维护者决定；人物资料、五张常服与 19 张表情待人工采用，真实成果复用与文件交付仍待业务记录。
5. 300k／128k 仍为候选，根配置未改；既往令牌轮换与部署条件未复验。仅授权阶段性本地提交，不推送、合并、部署或真实发送。总体目标保持进行中。
