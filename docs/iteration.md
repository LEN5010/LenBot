# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 `feat/s0-product-contract`，本批基线 `6ac52e6`，开始时工作区干净；上一批已本地提交。
- S4-03：核对长工作、明确控制、成果复用和真实交付。保留已有队列、job/revision、原预算和发送链，只修复成品交付上下文与工具箱仍指向旧处理器调用的不一致。

## 本批实现与核对

插件成品 deliver_work_result 原先只在返回的调用上补 job_id，未补 job_revision，episode_id 仍是原处理器轮次；_execution 创建的工具箱回调还捕获原调用副本。现在交付调用明确携带所选 job/revision、既有 work-delivery 轮次及原工作 initiator；工具箱回调绑定同一个最终调用，Mailbox 也使用该轮次。来源事件、插件 origin、场景、读取截点和 conversation 角色不变；这不是把已完成工作重新变成 processing 或授予新的工具权限。

发起者沿已有 job_initiator 从工作记录取得；未建立身份的旧记录保留 None，不借用处理器请求者。交付仍先检查 result_ready 与未有关联行动，调用前核对插件资格，经 before_commit、Actor/Gate 提交，再由原发送队列在尝试前检查工作版本及资格。后台 runner 仍只返回／安排原成品交付，不直接调用平台发群。

- 阅读 JobStore.validate_job_proposals、apply_job_proposals_in_transaction 及原 JobChanged 收尾。修订／取消增加原工作 revision，清除当前结果和旧交付关联；已发生资料与费用保留。resume 不改目标与范围，不重置累计额度、模型绑定或绝对期限；旧执行不能用旧版本覆盖新结果。
- 阅读 kick／_run_scene 的后台任务与独立工作槽位；群事件只安排工作，不等待整项模型、计算或渲染结束。没有新增调度器或声称事件循环从不阻塞。
- 阅读 ReusedWorkResult.from_job、暂存的实际已读成果条件及事务同版本比较；后续整理／导出是新的人类委托，保存原结果版本，原资料身份可以传递，旧已读账、预算和上传资格不复制。已取消／修订工作清除了当前 result，不能借旧结果冒充当前成果。
- 阅读现有群报告参考路径，从真实 request_source、专用工作、分批资料与进度到 PreparedWorkDelivery，再到本次修复的交付回调。保留该参考业务，不复制实现或制造新的示例输入；正常 job/revision、资料、资产与回执仍待现场提供。
- 阅读 ActionQueue 的发送前 validate_job_message、缺平台 ID 转 unknown，以及事件写入仅结清同场景 awaiting_delivery 且 delivery_action_id 匹配的事项。文件资产可生成／可下载／上传成功分别判读；重发既有正文不要求重新研究，但 unknown 和重复文件上传仍不得自动重放。
- uv compileall 编译 runtime/plugin_interactions.py，退出 0；git diff --check 无格式错误。未修改前端，不运行前端构建。未改模型、人格、根配置、预算、字体或真实发送名单。
- 源码定位出现 `rg: src/len_bot/runtime/job_delivery.py: No such file or directory (os error 2)`；沿 job_runner.py 的真实导入定位 runtime/plugin_interactions.py。属于路径查找错误，不是交付运行失败。
- 未实际建立工作、取消／修订、继续、复用、生成文件或发送；交付上下文、候选拒绝、原计费和同版真实回执均待人工验收。编译与源码阅读不等于完整业务已运行成功。
- 未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；未启动服务、读业务库、调用模型／平台或真实发送。

## 待决定与接续

1. S4-03 源码和完整参考路径进入待复核，真实从委托到消息／文件回执的业务记录仍缺。取消不等于已经撤回平台效果；已发生调用和终止未确认继续由原执行记录表达。
2. 接续 S4-04 的事件触发、心跳与主动分享，先核对现有 scheduler、heartbeat 和直播入口；不擅自启用场景、账号写入或主动实发。
3. S2 完整活动段额外原生字段／必要回复片段保存边界仍待维护者回答，未登记自由文本依赖不作自动推断。S1 仍有材料／耗时口径待补；S3 与 S4-01—03 仅待复核，未验收或发布；S4 其他项和 S5—S7 仍有计划工作。
4. 许可证、字体／素材授权、长期数据保留、公开支持承诺仍待维护者决定；人物资料、五张常服与 19 张表情待人工采用，真实成果复用与文件交付仍待业务记录。
5. 300k／128k 仍为候选，根配置未改；既往令牌轮换与部署条件未复验。仅授权阶段性本地提交，不推送、合并、部署或真实发送。总体目标保持进行中。
