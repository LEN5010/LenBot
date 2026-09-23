# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 `feat/s0-product-contract`，本批基线 `11436cc`，开始时工作区干净；上一批已本地提交。
- S2-04 的工作取消／修订与撤权核对：取消、改版已有事务内检查；但最后一次模型请求或插件执行结束后，成果提交路径没有再次调用当前资格检查。本批接通这个采用边界，不更换预算或增加权限体系。

## 本批实现与核对

InformationJobRunner 的唯一 commit_result 调用点将原 require_current_access 传入 JobStoreMixin.complete_job。该内部回调为必需关键字，不提供省略后放行的分支；它复用现有插件可用性、场景／人类请求者资格以及非人类能力检查，不新增授权来源。

complete_job 在原写锁、BEGIN IMMEDIATE 和版本／processing 核对内，完成原证据、进度与候选验证后，在 completed／partial 成果写入前同步检查当前资格。拒绝会沿原 BaseException 回滚整个成果事务，已在本事务暂存的工作进度、技能候选或兴趣采用一并回滚。此前已保存的工具观察与实际费用不抹除，原 PermissionError 分支保存 current_access_denied 的 interrupted 收尾。中断／失败说明不需重新取得已撤销权限，不能借此采用成功候选。

普通 finish_work 和专用插件执行均走这一提交入口。取消／修订后仍由原 job_checkpoint 与 complete_job 的 revision／status 条件阻止旧结果提交；没有新增状态、表、事务层、恢复重试或全局配置锁。出站提交与真实发送仍分别核对当前资格，不从结果保存推断交付。

- 阅读原 before_model、before_tool、prepare_request、工具页装配与 commit_result，确认现有 require_current_access 已用于执行准入，但成功成果采用前缺少同一核对。
- 阅读 complete_job 的候选验证／采用、结果写入、完成事件与预算结算；回调在结果写入及结算前，拒绝不转成可由模型修改参数解决的证据错误。之后沿原权限中断处理，不额外调用模型。
- 阅读原取消／修订增加工作版本、保留实际费用、处理原 reservation 的代码；未重置次数、期限、令牌上限、账号或模型绑定。根权限保存并不保证立即取消全部在途请求，因此采用点核对有独立作用。
- 首次文本替换匹配到同文件 interrupt_job 的同形 UPDATE；通过 git diff 发现后立即撤销该处修改，并按 complete_job 方法范围重新定位。没有运行该中间版本，最终 diff 只包含成果提交处及唯一调用方。
- uv compileall 最终编译 runtime/job_store.py、runtime/job_runner.py，退出 0；git diff --check 无格式错误。未改前端，没有运行前端构建。
- 初始查找出现 `rg: src/len_bot/cognition/job_store.py: IO error for operation on src/len_bot/cognition/job_store.py: No such file or directory (os error 2)` 与 `rg: src/len_bot/runtime/configuration.py: No such file or directory (os error 2)`，随后沿实际 runtime/job_store.py 及 agent_runtime.py 的配置入口阅读；均为源码定位错误，不是业务运行失败。
- 没有实际撤权、取消、修订或模拟竞态；最后响应、事务回滚、候选未采用及中断说明仍待同版人工验收。编译和源码核对不等于运行通过。
- 未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；未启动服务、读业务库、保存根配置、调用模型／平台或真实发送。

## 待决定与接续

1. S2-04 继续核对压缩期间输入、检查点恢复及权限变化后的请求材料装配；本批采用点修复不等于整项完成。S4 的连续体验仍需依赖这些真实边界。
2. S2 活动段中不可由引用还原的原生响应字段／必要回复片段保存边界仍待维护者回答；已确认的宿主所有权和原事件权威不是额外片段保存授权。本批不扩大保存，不重复提问。
3. S1 其他提示／动态材料和等待／失败计时仍待补；S2 尚缺完整交换、其他编号／提案句柄、窗外范围、基础摘要及压缩交接。S3 已进入复核范围但未验收或发布；S4—S7 仍需逐项实施或核对。
4. 许可证、字体／素材授权、长期数据保留、公开支持承诺仍待维护者决定；人物资料、五张常服与 19 张表情待人工采用，真实成果复用与文件交付仍待业务记录。
5. 300k／128k 仍为候选，根配置未改；既往令牌轮换与部署条件未复验。仅授权阶段性本地提交，不推送、合并、部署或真实发送。总体目标保持进行中。
