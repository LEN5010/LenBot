# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 `feat/s0-product-contract`，本批基线 `4d48ef9`，开始时工作区干净；上一批已本地提交。
- S2-04 的历史维护与工作压缩边界。历史候选可能在生成、等对话结束后才提交，原路径没有重新核对当前维护资格；工作压缩将权限／预算拒绝一并写成内容失败，混淆退出原因。本批只接通现有判断与异常类型。

## 本批实现与核对

_maintain_history 建立 require_current_maintenance，仍使用 _running、_can_maintain_history 和 scene_policy.maintenance_allowed。入采用队列前检查，并通过原 Actor kwargs 传给 commit_history_batch 的必需内部回调；原写事务在保存维护事件及完成批次前复核。运行停止按取消处理；权限／入口不再允许时拒绝并回滚候选写入，原批次失败记录与诊断保留。没有改来源范围、摘要格式、相关认识版本比较或新输入接收。

WorkCompressor.prepare 对原 PermissionError、JobBudgetExhausted、AgentBudgetExhausted 直接传播，和已有 JobChanged 一样不落为 compression.status=failed。原工作运行器据真实权限／预算维度处理中断，不把 token 或期限拒绝误记为 context 失败。真正生成／窗口内容失败的原记录仍保留，没有清除旧失败状态、自动重试或重置已消耗额度。

- 阅读历史批次固定 source_event_ids／complete_event_ids、Actor 活跃租约下的 HistoryCommitDeferred、提交时当前 Session 和按相关目标／主体／类别比较 expected_memories 的原路径；生成期间新消息仍在原 Actor 路径接收，旧批次不覆盖新输入位置。
- 阅读 commit_history_batch 的原短事务、候选写入、知识修订和完成事件；资格回调拒绝沿 BaseException 回滚，Actor 只在成功返回后更新 session。没有把待采用候选写成已采用，也不把历史维护说成完整会话段交接。
- 核对工作压缩先使用原 charge／work_call_admission，调用拒绝可产生两种现有预算异常；原保存压缩仍要求同 job/revision 且 processing。保留原异常可继续使用 current_access_denied 或真实 budget_kind 的退出记录。
- uv compileall 编译 runtime/agent_runtime.py、memory/history.py、runtime/work_context.py，退出 0；git diff --check 无格式错误。未改前端，没有运行前端构建。
- 初始通配查找出现 `zsh:1: no matches found: src/len_bot/cognition/work*`；随后用类名定位 runtime/work_context.py。另有 `rg: src/len_bot/runtime/work_budget.py: No such file or directory (os error 2)`，随后定位 cognition/budget.py 的实际准入函数。均为源码定位错误，不是运行失败。
- 未生成摘要、撤销权限、耗尽预算或制造在途竞态；候选回滚、新消息保留、压缩退出分类及恢复仍待同版人工验收。编译与源码阅读不等同运行通过。
- 未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；未启动服务、读业务库、修改根配置、调用模型／平台或真实发送。

## 待决定与接续

1. S2-04 的现有租约、等待、成果采用和维护／压缩边界已有定向修复，完整活动段交接尚未接通，不能标成整项完成。接续核对 S2-05 的现有认识修订、检索有效范围和旧材料采用，不预设跨群共享或新存储结构。
2. S2 活动段中不可由引用还原的原生响应字段／必要回复片段保存边界仍待维护者回答；已确认的宿主所有权和原事件权威不是额外片段保存授权。本批不扩大保存，不重复提问。
3. S1 其他提示／动态材料和等待／失败计时仍待补；S2 尚缺完整交换、其他编号／提案句柄、窗外范围、基础摘要及压缩交接。S3 已进入复核范围但未验收或发布；S4—S7 仍需逐项实施或核对。
4. 许可证、字体／素材授权、长期数据保留、公开支持承诺仍待维护者决定；人物资料、五张常服与 19 张表情待人工采用，真实成果复用与文件交付仍待业务记录。
5. 300k／128k 仍为候选，根配置未改；既往令牌轮换与部署条件未复验。仅授权阶段性本地提交，不推送、合并、部署或真实发送。总体目标保持进行中。
