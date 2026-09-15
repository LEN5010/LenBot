# 社会 Agent C01—C10 fix：分支审查与修复合同

审查基线：`social-agent-m0-foundation@2c862c63b0a0575e84ff87019651d9f8812c1601`。比较基线：完整计划指定的 `7a4152d734b1430f0f42c728cae0c850cc9afae3`。

本文面向后续实施者和运营者，决定哪些基础实现需要修正、哪些能力暂不能放行。原阶段编号仍为 C00—C29；本文用 FX01—FX13 标识修复项，不重编号开发计划。“C1—C10”在此指原计划的 C01—C10。

本文记录审查基线的源码事实和拟修复合同，不代表修复已实施。当前处理状态、实际命令结果和未确认项只记在 [当前任务](../iteration.md)；目标和依赖见 [完整实施计划](../LenBot_社会Agent_完整实施计划_7a4152d.md)。下文源码链接及行号均对应审查基线，修复后应结合符号名和该提交核对。

## 1. 判断与证据边界

**选择定向修复，保留分支；完成相关修复前不将其作为已满足 C06—C10 合同的基础发布。**原 Actor、EventStore、JobStore、RuntimeGate、ActionQueue 没有被另一套框架替换，C01 的取消传播、C03 的日历失败卡、C04 的话题线索和 C05 的能力提示都有保留价值。

本次 diff 中的新表以追加为主，没有看到清空原始消息、重建人物身份或重写 OneBot 回执的代码修改。这只能说明未发现此类破坏性迁移，不能证明所有旧库和配置都能无损升级或回退。C07 的默认策略会实际改变未配置额度的旧部署行为，属于需要纠正的升级影响。

Gateway 客户端没有接入 `WorkspaceService.run_python`。FX06—FX10 是新 Gateway 自身及未来切换的阻塞项，不是已经发生的 QQ 或宿主 worker 事故。现有 `run_python` 仍调用宿主容器运行时，不能因新文件存在而认定执行隔离已完成。

本文的“确定缺口”来自类型、调用顺序、状态转换和数据写入的源码交叉核对；触发场景是控制流推导，未经运行复现。没有运行测试、模拟模型、临时库探针、容器、服务、故障注入或真实发送。编译和前端构建不能验证本文列出的异步行为。

### 对上一轮口头审查的校正

1. **Gateway 重启的直接故障不只是“变成未知后忘记清理”。**`sweep()` 对 accepted/starting/running 直接写 termination_unconfirmed，而转换表不允许这条边；会先抛出 `ValueError`，且该路径也由应用 startup 调用。只有 cancel_requested 可以走到该终态。合法写入的 termination_unconfirmed 确实又被后续扫描排除。FX06 分开记录两者。
2. **短执行不必然永久卡死。**代码会先遗留 starting；周期扫描在期限前可能因非法转换报错，到期后若停止/核对成功，可能收口。确定的问题是丢失原退出结果、释放容量被延迟以及恢复错误，不承诺每次都会永久不退出。
3. **`accepted=true` 本身不是误报成功。**[SubmittedExecution](../../src/len_bot/execution/client.py#L56) 将它定义为“本次新登记”；输入导入不支持时返回的 record 同时带 failed 和明确原因。保留此语义，消费端必须读取 record.state。不能据此虚构一个“已成功执行”缺陷。
4. **计划头部的双行尾空格是 Markdown 换行。**`git diff --check` 会提示它们，但不能据此评价代码质量，也不列为本次修复项。
5. **预算准入不能被说成供应商计费的数学硬上界。**本地估算无法保证真实 usage 不超出估算。要修的是未预留就调用、漏算和恢复篡改上限；真实差额仍须如实记账。

## 2. 修复索引

P1：影响额度、权限、执行生命周期或文件边界，相关能力放行前必须修复。P2：功能入口、显示或完成声明有误，须随对应功能交付修复。优先级不是对生产事故严重程度的推定。

| 项目 | 优先级 | 阶段 | 问题与影响 |
|---|---|---|---|
| FX01 | P1 | C07/C08 | 下一次模型请求不预留；压缩绕过 token 判定，终结可能透支 |
| FX02 | P1 | C07—C09 | 预算关闭早于全部调用结束，技能维护和晚到 usage 不能正确更新日账 |
| FX03 | P1 | C08/C09 | 累计活动时长被当作绝对期限；对话等待恢复和调用超时未贯通 |
| FX04 | P1 | C07—C09 | 原额度被结算覆盖；恢复读当前策略；null/默认值/热更新不一致 |
| FX05 | P1 | C06/C09 | 授权只覆盖部分控制入口，执行与策略选择未按真实主体闭合 |
| FX06 | P1 | C10 | Gateway 短执行、启动取消、异常清理及重启状态机存在断点 |
| FX07 | P1 | C10 | 新执行可重用他人或被占用的工作区，未知终止没有持久阻断 |
| FX08 | P1 | C10 | HTTP 枚举解析不匹配严格模型，错误分类不能证明未执行 |
| FX09 | P1 | C10/C11 | 默认执行 UID 与控制文件/工作目录权限不匹配 |
| FX10 | P1 | C10/C12 | 新 Gateway 产物缺少安全打开、固定内容和目录资源上限 |
| FX11 | P2 | C06 | 面板新建 Grant 在绑定签发者前就因空必填字段被拒 |
| FX12 | P2 | C07 | 余额由最近100条和默认策略计算，与真实准入不一致 |
| FX13 | P2 | C02及交付文档 | 条目分页不等于容量适配，历史“实现完成”声明需要收口 |

## 3. 工作预算与恢复

### FX01：每次调用缺少统一预留

**源码依据。**[AgentBudget._refusal/take_model](../../src/len_bot/cognition/budget.py#L179) 仅判断已用 token 是否达到上限；[force_terminal](../../src/len_bot/cognition/budget.py#L194) 只改变下一步工具可见性。[ModelGateway.complete](../../src/len_bot/cognition/gateway.py#L60) 已拿到最终请求估算，但 begin_model_call 只插入调用记录，没有检查并占用本次额度。[WorkCompressor](../../src/len_bot/runtime/work_context.py#L266) 直接 charge(model_steps=1) 后调用 ModelGateway，charge/job_checkpoint 不检查 token。

**触发与后果。**工作余额为 1000 token，而终结或压缩请求需要数千 token 时，主循环只因 used < limit 就放行；强制终结并不会让输入或输出自动变小。上下文准备期间的压缩更可以在主循环 take_model 前消耗额度。并发子调用缺少按请求占用的统一事实，不能把工作创建期的原子预占当作每次调用的原子预留。

**修复合同。**在真实模型请求边界基于最终输入估算和本次输出上限进行统一准入，并在与原调用记录一致的写事务中保存唯一 call_id 的在途占用。覆盖主工作、压缩、技能维护和插件子调用。终结也须有足额预留；不能容纳终结时直接保留已有结果与未完成项。资源不足不改模型、不重置、不免费加一次总结。对实际 usage 超过预留的情况记差额并停止新调用，不能截断已发生用量。

**核对目标。**A19：获准的小额真实工作能展示请求估算、输出上限、准入记录和停止原因；源码逐入口确认没有跳过准入的 ModelGateway 调用。无运行证据时不得标“token 硬上限通过”。

### FX02：工作结果完成后仍有消费，结算行却已经关闭

**源码依据。**[complete_job](../../src/len_bot/runtime/job_store.py#L674) 在业务结果事务内结算；[commit_result](../../src/len_bot/runtime/job_runner.py#L421) 在它之后调用 `_start_learning`；[maintain_candidates](../../src/len_bot/skills/learning.py#L63) 仍以同一 job_id 调用模型，但只核对次数/累计时长。结算 [UPDATE](../../src/len_bot/cognition/call_store.py#L198) 仅作用于 status='held'；[end_model_call](../../src/len_bot/cognition/call_store.py#L228) 更新调用行后不更新已关闭预占。

**触发与后果。**工作提交带 skill_candidate 的结果后，原预占先变为 settled，之后技能维护的调用虽进入 model_calls，却不进入已结算的日账；余额被高估，维护也没有原 token 准入保护。取消或 interrupt 在模型调用尚未保存最终 usage 时提前结算，也会把后续真实回执留在调用账中而不修正预占账。现行 [job_measured_tokens](../../src/len_bot/cognition/call_store.py#L169) 用调用时未持久化的统一 `conservative_output_tokens` 估算所有未知调用，无法区分 work、maintenance 和插件各自的输出上限。

**修复合同。**区分业务结果完成、请求仍在途和预算最终关闭。原预算必须覆盖所有允许继续的维护，或明确在额度/期限不足时停止维护；不能另开免费额度。按 call_id 幂等结算，晚到真实回执替换该次估算占用并更新日账，不重复扣费。保存本次请求自身的输出上限/估算，避免后续配置变化改写过去计量。取消不先释放尚不能确认未消费的余额。

**核对目标。**A20：一次合法产生技能候选的真实工作，其工作调用和维护调用均出现在同一账户最终用量里；取消中的请求状态、保守占用与最终 usage 能对应。旧已结算记录的补账需离线核对既有 model_calls，不能猜用量或回滚旧事件。

### FX03：绝对期限没有作为恢复事实保存

**源码依据。**[remaining_seconds](../../src/len_bot/runtime/job_runner.py#L547) 计算配置时限减累计 `elapsed_seconds` 和本运行段时长；[job_checkpoint](../../src/len_bot/runtime/job_store.py#L605) 只加上运行段的时间增量。[ConversationResume](../../src/len_bot/cognition/models.py#L27) 新增 elapsed_seconds_limit，但 [social_core.run 的恢复分支](../../src/len_bot/cognition/social_core.py#L35) 未将该字段带回配置，[window_deadline](../../src/len_bot/cognition/budget.py#L46) 又从本次 monotonic + 剩余活动时长重建。对话 [AgentLoop 调用](../../src/len_bot/cognition/social_core.py#L322) 未被该 deadline 对应的 timeout 包围，take_tool 也不检查期限。

**触发与后果。**工作运行 60 秒后暂停一小时，在 600 秒配置下仍能获得约 540 秒，而不是因首次开始后已过期被拒。对话等待期间同样不计时，恢复时修改全局 window 还可能改变旧轮次时限。对话模型在到点前发起请求后，即使到点才返回工具调用，仍可能执行工具或终结提交。工作已有运行段 timeout，这部分应保留；缺口在持久绝对期限与对话覆盖。

**修复合同。**首次开始时保存绝对期限或可唯一求得期限的原事实，排队不计时；开始后的等待、停机和恢复计入。当前进程用单调时钟执行剩余期限，持久字段用于跨进程恢复；调用/工具等待受期限约束，已提交结果和回执不得因超时被改写为未发生。恢复读原期限与原时限，普通 revise 不隐式延期。D05 的 1800 秒数值仍需独立确认，不能因修语义就修改根配置。

**核对目标。**A21：获准环境中使用正常短时工作和等待恢复观察，检查首次开始、原 deadline、恢复时刻和停止原因。旧记录仅有累计时长时不能推算确切首次开始，应保留待运营处理。

### FX04：额度含义、默认值与恢复快照不一致

**源码依据与四种触发。**

1. [settle_reservation_in_transaction](../../src/len_bot/cognition/call_store.py#L198) 把 reserved_tokens 改成消费量；[rehold_job_budget_in_transaction](../../src/len_bot/runtime/job_store.py#L147) 用当前策略、当前配置重新算上限，原创建上限没有单独保存。结束后改默认策略再恢复，会扩大或缩小旧工作的累计额度，和“不重新赠送额度”不符。
2. [ReservationPolicy.work_reservation](../../src/len_bot/cognition/budget.py#L112) 在 model_steps=None 且 work_token_limit=None 时返回 0；[budget_state](../../src/len_bot/runtime/job_runner.py#L544) 把它作为 token 上限，第一次调用即因 used >= 0 被拒。null 的“不设上限”变成“零额度”。
3. [ResourceSettings](../../src/len_bot/config_store.py#L72) 说明空策略升级不新增限制，但 [Runtime._apply_budget_configuration](../../src/len_bot/runtime/agent_runtime.py#L348) 和 JobStore 回退到硬编码 `ReservationPolicy()`（10M/30M），且创建工作总会预占。旧配置不改动也会新增日限额；不能继续称“默认不改变行为”。
4. [update_runtime_settings](../../src/len_bot/runtime/agent_runtime.py#L274) 用 model_copy 替换 runtime.config，却没有同步 event_store.budget_config。保存 job_max_steps 后，创建预占仍可使用旧值，运行段读取新值，直到重新应用资源配置或重启才一致。

**修复合同。**保留不可变的原累计上限、计费主体和预占日；消费、在途/持有和上限分别表达，重开不能从当前默认值补造。默认策略、显式策略、策略缺失与 null 各有确定语义；升级行为必须公开且由根配置确认，系统账户不隐藏套用用户日限额。新工作预占和运行读同一发布配置；已有工作按原快照执行，显式加额/延期作为单独运营决策保留记录。若无限单工作量与有限日预占无法共同成立，拒绝该配置组合，而不是预占零。

**核对目标。**A09/A21：同一工作的创建、结算、默认策略修改和恢复前后能看到原上限不变；null 不会首轮误判；面板保存后的新预占与新执行预算一致。已被结算覆盖原上限的旧行需从确切历史还原，无法还原时不可直接恢复。

## 4. 授权链与面板

### FX05：能力结构存在，执行闭合仍未完成

**源码依据。**[Gate](../../src/len_bot/runtime/gate.py#L241) 为非人类 create/resume 检查 grant，却跳过 revise；[JobStore 控制分支](../../src/len_bot/runtime/job_store.py#L436) 的 revise 会变为 pending 或继续 processing。[require_current_access](../../src/len_bot/runtime/job_runner.py#L397) 仍按 handler/人类 chat_allowed 判定，没有读取 CapabilityAuthority；system 工作 requester=None，在非 handler 路径可能直接被人类白名单规则拒绝。身份类型不等于可运行的自主工作路径。

授权选择和预算选择也不相同：[SYSTEM_WORK_CAPABILITIES](../../src/len_bot/runtime/capabilities.py#L54) 将 information 映射到 public_research，但 [reservation_policy_for](../../src/len_bot/runtime/job_store.py#L101) 只找 long_work Grant。仅有 public_research 的系统策略会被忽略。显式失效策略名称又在 [policy_for_grant](../../src/len_bot/runtime/capabilities.py#L202) 回退默认值。`CapabilityGrant.concurrency` 虽能保存和显示，尚未参与工作并发准入。

**触发与后果。**有效系统授予可在创建后被执行器错误拒绝；在能进入执行的插件路径，撤销后下一次调用缺少当前 grant 检查。单独修复 system 无 QQ 导致的拒绝，却不补当前 grant 与公共资料范围，会进一步扩大缺口。revise 不是“只改文字不执行”，不能被排除在授权检查之外。此处描述接线缺口，不声称当前存在未经授权的真实系统发群。

**修复合同。**真实 initiator、授权主体、计费主体和数据范围一起贯穿创建、修订、恢复和调用前检查。授权采用哪条 Grant，资源选择就绑定同一条事实；命名策略失效直接拒绝，不隐式换默认值。运行中再次核验当前授予与场景；系统公共研究只能读自己的公共范围，不能复用群资料通道。未生效的并发字段应标明不生效，正式启用前纳入准入。保持原人类聊天、插件和发送权限边界。

**核对目标。**A08/A21：获准主体可开始其能力；停用、到期或撤销后，create/resume/revise 和下一次执行均给出一致拒绝；选中 Grant、策略与计费主体可追溯。不能通过构造假的系统 QQ 号演示通过。

### FX11：面板无法通过正常流程新建 Grant

**源码依据。**[SettingsView.addGrant](../../src/len_bot/web/frontend/src/views/SettingsView.vue#L157) 将 operator_id 初始化为空串；界面“签发运营者”只读，说明由服务端填入。[AccessSettingsRequest](../../src/len_bot/web/routes/settings.py#L36) 却直接以完整 CapabilityGrant 解析请求，其 [operator_id](../../src/len_bot/runtime/capabilities.py#L70) 要求 min_length=1。路由内绑定当前登录人的语句在请求验证之后才执行。

**触发与后果。**正常新增授权，填好可编辑项再保存，会在进入 handler 前因 operator_id 为空被拒绝。现有路由还会给全部 Grant 重写签发人，即使旧客户端只提交白名单，也可能改变不相关的授权历史。该问题是接口合同与表单的确定冲突，未进行实际 UI 保存。

**修复合同。**单独定义用户可编辑的授予请求字段，认证后再构造完整存储类型；不接受客户端伪造签发者。只改白名单保留原授予；授权内容变化按明确修订语义绑定操作者和记录版本。类型切换时不得残留不适用的 scope，保存前给出具体字段错误。

**核对目标。**A24：正常登录面板新增、编辑和撤销授予均能保存；只改白名单不改历史授予。前端修复按仓库要求构建并人工查看实际页面，不生成自动截图。

### FX12：账户余额从截断明细和错误策略计算

**源码依据。**[model_reservations](../../src/len_bot/web/query_service.py#L176) 先读取 limit=100 的当日工作，再在 Python 中求和；scene_id 筛选也在读取前生效。所有账户的 daily_limit 都使用默认策略。真实准入 [account_used_tokens_in_transaction](../../src/len_bot/cognition/call_store.py#L83) 则在数据库对整日记录聚合，跨群和具名策略可有不同结果。

**触发与后果。**同日记录超过100条，较早消费被丢弃；按群查看只计这个群，却仍称账号可用余额；使用具名策略的主体看到默认上限。面板会显示可用而创建被拒，或者相反。它是显示错误，不会让数据库的原子创建检查自动失效。

**修复合同。**汇总与明细分页分开，账户按完整账务日的全局消费和当前适用策略求余额，群视图另列场景小计。主体在不同授权范围适用不同上限时，展示具体能力/范围对应的准入余量，不能制造一个含糊的全局“可用”。held、真实消费、估算消费分别说明。

**核对目标。**A24：有足够自然历史时核对超过100条的日账；否则通过完整聚合源码审阅并标明未运行该规模。不能制造批量工作填满100条来替代当前禁止的探针。

## 5. Gateway 接线前必须完成的修复

### FX06：启动、取消、重启与终态不一致

**源码依据与触发链。**

- [启动确认](../../src/len_bot/services/worker_gateway/runner.py#L245) 遇 inspect=False 立即返回；[inspect 封装](../../src/len_bot/services/worker_gateway/runner.py#L365) 把所有已退出的非零 inspect 返回都解释为 False，不能区分还未创建、已退出和 Docker 错误。脚本很短、镜像启动失败、docker run 尚未创建容器时均可能未写 running。
- [正常退出处理](../../src/len_bot/services/worker_gateway/runner.py#L178) 在 state!=running 时直接返回，丢弃上述路径真实 returncode、日志和产物；记录遗留 starting。
- [sweep](../../src/len_bot/services/worker_gateway/runner.py#L348) 将未到期孤儿直接转为 termination_unconfirmed，但 [ALLOWED_TRANSITIONS](../../src/len_bot/execution/journal.py#L27) 不允许 accepted/starting/running → termination_unconfirmed。[startup](../../src/len_bot/services/worker_gateway/app.py#L130) 直接 await sweep，故相关旧记录可导致应用启动失败；周期扫描只记错误，当前记录仍未收口。
- 合法写入的 termination_unconfirmed 被 [unfinished/count_unfinished](../../src/len_bot/services/worker_gateway/store.py#L52) 排除，[cancel/_stop](../../src/len_bot/services/worker_gateway/runner.py#L289) 也直接返回，不再核对仍可能存活的容器。
- accepted/starting 阶段收到 cancel_requested 后，`_run` 仍可能启动进程并写 starting/running，从而触发非法转换；[通用异常分支](../../src/len_bot/services/worker_gateway/runner.py#L196) 只写 failed，没有保证已有容器被停止，写 failed 本身也可能被状态表拒绝。

**影响边界。**Gateway 可丢失真实退出结果、无法正常启动查询服务，或不再负责未知容器的停止。并非每次短执行都永久卡死；到期后的清理可能结束它。没有运行容器验证事故。

**修复合同。**同 execution 的启动、取消、退出更新序列化；写入路径和状态表共同修订。未观察到 running 也要记录真实进程结果；取消后不再启动新的执行。容器可能已创建时，任何异常都转入有界停止/核对，不能只改业务标签。恢复仅恢复对已存身份的清理责任，不重放代码；未知终止继续占用资源资格，后续核对追加事实。单条恢复错误记录到该执行并保留阻断，不能使整个查询/取消接口不可用。正常退出的业务结果与之后的容器清理事实不能相互覆盖。

**核对目标。**A22/A08：获准环境中的短时离线执行、正常取消和服务重启均能回查执行身份、真实输出、停止事实；竞态分支静态逐边审阅，不通过故障注入或模拟断言冒充运行证明。

### FX07：幂等执行 ID 没有保证工作区所有权

**源码依据。**[accept](../../src/len_bot/services/worker_gateway/runner.py#L103) 检查同 execution_id 和全局 count；[record_execution](../../src/len_bot/execution/journal.py#L105) 只对重复 ID 校验内容。新 ID 下不检查 workspace_id 是否属于另一 job/scene/initiator，也不检查同工作区是否仍有活跃或未知执行。[workspace_directory](../../src/len_bot/services/worker_gateway/runner.py#L58) 只校验名称与根路径。

**触发与后果。**有服务认证的调用者为不同工作或同工作不同修订传同一 workspace_id，两个容器即可同时写同目录。旧执行仍在运行但结束状态未知时，新执行也能复用，注释声称的“目录封锁”没有实际准入条件。服务 token 证明调用者身份，不证明请求携带的工作区归属正确。

**修复合同。**复用已有持久执行记录保存/核对 workspace 的工作与主体归属，给同一可写目录建立唯一执行占用；接受新执行时原子核对，跨工作借用、并发修订或未确认终止均拒绝。全局容量应包含未释放资源，而不只是四种活动状态。旧修订可保留观察，不能写新修订进度或替换其产物。不得只增加进程内锁而让重启后丢失归属。

**核对目标。**A23：源码确认新 ID 不绕过归属、同目录串行和未知终止阻断；正常业务多次执行的工作/执行/目录关联可追溯。

### FX08：传输解析及“未执行”证据不可靠

**源码依据。**服务 [fetch/submit/cancel](../../src/len_bot/services/worker_gateway/app.py#L51) 返回 model_dump(mode='json')，state 为字符串；[ExecutionRecord](../../src/len_bot/execution/protocol.py#L87) 是 strict=True 且 state 为 ExecutionState。客户端 [_request 与 submit/get/cancel](../../src/len_bot/execution/client.py#L89) 先 response.json，再 model_validate Python 字典，没有将枚举字符串转换为内部枚举。相比之下，[数据库解码](../../src/len_bot/execution/journal.py#L66) 显式构造 ExecutionState。按该严格模型合同，正常 HTTP 返回的记录存在确定的解析不匹配。

同一客户端把所有 >=400 都包装成文档写着“nothing was started”的 GatewayRefused。HTTP 500 可能发生在记录或启动之后，不能证明没有执行；409 身份冲突也说明旧执行可能存在。非法 JSON 或不匹配响应没有明确归类为“结果不能确认”。

**修复合同。**在线上边界按 JSON 合同一次解析成内部类型，保持其他字段严格，不用全局 strict=False 掩盖问题。明确已拒绝且未启动、身份冲突需查询、以及结果未知三类结局；5xx/传输失败/不可解析回包都不能授权新 ID 重做。保留同 ID 查询/重读。`accepted` 仍可表示是否新登记，但必须与 execution state 分开使用。

**核对目标。**A22：正常 submit/get/cancel 的实际 HTTP 回包均可解析成相同记录；没有实际联调前不因数据库读取成功就宣称客户端可用。未知结果路径通过源码核对，禁止自动重试副作用。

### FX09：默认执行用户读不到控制脚本

**源码依据。**[WorkerImage](../../src/len_bot/services/worker_gateway/config.py#L33) 默认 container_user='65532:65532'；Gateway [控制目录](../../src/len_bot/services/worker_gateway/runner.py#L68) mode=0700，[写 task.py](../../src/len_bot/services/worker_gateway/runner.py#L514) mode=0600，由 Gateway 进程用户拥有；[容器命令](../../src/len_bot/services/worker_gateway/runner.py#L230) 切到 worker UID 并只读挂载目录。工作目录同样由 Gateway 创建，mode=0770。

**触发与后果。**普通 Linux bind mount 且 Gateway UID 与 worker UID 不同、未另行配置 UID 映射时，worker 不能遍历控制目录/读取脚本，也可能不能写工作目录。镜像内 Python 正常也不能完成最小执行。rootless 映射等具体部署会影响结果，因此不声称所有部署都失败。

**修复合同。**在唯一支持的 Linux 后端中明确宿主/Gateway/worker 的 UID/GID 和卷映射；worker 仅能读取本执行脚本和输入、写本工作目录，不能读服务 token 或其他执行控制资料。必要权限准备在受控部署或网关目录创建边界完成；不把 worker 改 root、不放开全部控制根目录、不用777掩盖问题。

**核对目标。**A07/A22：正常非 root 运行读取脚本、写文件和退出有实际证据；同时记录执行用户及卷权限，编译不能代替此项。

### FX10：产物读取与目录限额退回不完整边界

**源码依据与三个缺口。**

1. [artifact_bytes](../../src/len_bot/services/worker_gateway/app.py#L120) 先 resolve，再 is_symlink/is_file，最后交给 FileResponse 再开路径。resolve 后检查不到原链接；检查与实际打开分离，未使用现有宿主 worker 的目录描述符、O_NOFOLLOW、O_NONBLOCK 与 fstat 方式。
2. [register_artifact](../../src/len_bot/services/worker_gateway/store.py#L72) 只保存执行、相对路径、大小和类型；下载仍读可变工作目录。同工作之后覆盖文件，旧 artifact_id 会返回新内容，原记录大小和执行来源可能失真。仅有稳定 ID 不代表产物内容稳定。
3. [GatewayConfig](../../src/len_bot/services/worker_gateway/config.py#L55) 只有输出截断和登记条目上限，没有工作目录总字节/条目执行限制；[_watch](../../src/len_bot/services/worker_gateway/runner.py#L263) 只看取消和期限；[文件枚举](../../src/len_bot/services/worker_gateway/runner.py#L498) sorted(rglob('*')) 先遍历全树再检查 max_artifacts。该上限不限制写盘，也不限制枚举资源。

**触发与后果。**后续执行或其他写入者改变产物路径时，旧下载可能读错文件；路径检查与实际打开之间存在替换窗口，但本次未证明任何具体宿主文件泄露。脚本大量写文件可耗尽网关磁盘或导致结束枚举长时间占用；内存/CPU容器限制不限制 bind mount 所在磁盘。

**修复合同。**下载沿同一文件描述符确认归属和普通文件类型，逐级拒绝链接/穿越/特殊文件并有字节上限。产物登记后以受控不可变存放位置或等价生命周期约束固定内容，复用原资产边界，不引入内容指纹/校验平台。运行中和退出后检查目录总量，超限停止并如实标记；文件枚举流式有界，输出日志保留已取得部分与截断标记。与 FX07 的工作区占用一同实施，不能靠路径检查代替所有权。

**核对目标。**A07/A23：正常中文/空格文件可列出与下载，后续执行不改变已登记产物；安全打开及限额覆盖由源码审阅确认。特殊文件、替换竞态和磁盘耗尽不运行攻击或压力探针，运行证据缺失如实保留。

## 6. 前期交付与状态说明

### FX13：C02 的“分页”尚未解决请求容量，历史验收措辞过强

**源码依据。**[LLMReflector.query_memory](../../src/len_bot/memory/reflector.py#L113) 接受1—200条并把整页正文加入工具结果；没有根据当前 trajectory 余量选取能装下的完整记录。[prepare_request](../../src/len_bot/memory/reflector.py#L162) 在后续请求超容量时抛错退出，模型无法收到拒绝后缩小页量。减少字段和增加 offset 有价值，但记录数有界不等于 token 有界。

**触发与后果。**当维护原始批次已占据大部分输入窗口时，即便 query_memory 只读少量记录，也可能装不下。该问题继承了原容量失败，C02 只部分缓解，不是新版本破坏摘要游标。失败不推进原覆盖应保留。

**修复合同。**使用已有请求预算与呈现机制选择能容纳的完整认识记录，保留真实分页位置与未读范围；不能把否定和条件截断后当作完整认识。连一条都无法装下时给出可保存的容量不足结局，不重复读取或硬塞请求。C01/C03/C04/C05 未在本次发现足以支持重写的直接缺陷，但仍需原计划规定的正常业务证据。

**文档收口。**审查基线的 [readiness](social-agent-readiness.md)、[architecture](../architecture.md)、[product](../product.md)、[operations](../operations.md) 中“绝对期限”“同一原额度恢复”“默认不新增限制”“create/revise/resume 当前 grant”等当前能力说明，与上述源码不完全一致。不得让旧“实现完成 / 未运行”覆盖已知失败条件。代码修复时同步所属当前行为文档，注明实际覆盖和未运行边界；历史实现日志保留，但通过当前任务明确其结论已被本次复核限制。原记录中的临时库/模拟检查不是本项目今后继续运行探针的授权。

**核对目标。**A04：正常较长维护批次能容纳后续读取或明确保留失败区间，不能仅凭 API 新增 limit/offset 认定容量问题完成。

## 7. 实施顺序与完成条件

1. **修预算事实和公共调用入口：FX01—FX04。**先确定原上限、期限、在途调用与最终消费的关系，再修改各调用路径，避免在错误账务语义上继续加补丁。与 FX12 的查询合同一起核对。
2. **修授权闭合和正常管理入口：FX05、FX11。**补足 revise 和执行期检查，保持旧人类行为；非人类接线与公共资料范围未完成时保持关闭。
3. **在未接线状态修 Gateway：FX06—FX10。**协议、启动/停止、工作区归属、挂载用户与产物边界合在同一完成范围核对。可以分多个可审查提交，但在全部依赖收口前不通过 C11 切换。
4. **修 C02 余量呈现及状态文档：FX13。**不依赖 Gateway，可独立交付；更新每批实际证据，避免继续累计“已完成”声明。

优先在原模块和事务内修复。确需持久字段时说明它补的业务事实及旧数据处理，不因本报告新增通用授权平台、风险评分、自动自愈、备用执行后端或重试编排层。不删除有价值的既有取消、回执和安全文件打开规则。

完成一项修复应同时满足：源码路径闭合、所属文档一致、适用的正常编译/构建完成、人工观察范围如实列出。没有真实环境证据时最多标“实现完成 / 未运行”；仍有已知合同缺口时标“部分实现 / 待修复”，不能继续标实现完成。验收采用完整计划 A19—A24 和上述各条目标；不新增测试、夹具、临时库断言、自动截图、覆盖率或故障注入。

## 8. 升级与回退

本文只定义修复合同，不实施代码、配置、数据库或部署变更；文档的提交/推送范围与实际状态见 [当前任务](../iteration.md)。后续涉及运行升级时，先按 [运行手册](../operations.md) 停机、普通备份，核对在途调用和外部容器，再实施必要的一次性结构补充。

旧记录无法确定原上限、首次开始或容器终止事实时，保留事实并阻止自动恢复；不能按当前配置补造。修复前后均保留原 job/revision、call_id、execution_id、action_id 和真实回执。回退先停止新能力入场、核对并结束原执行；不得通过恢复过时数据库丢弃新消息或用新执行 ID 重跑未知操作。

是否部署、真实调用模型、执行容器或实发消息仍按用户当前授权分别决定。本文的修复合同不是这些操作的授权。
