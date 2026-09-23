# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 feat/s0-product-contract，基线 2618d5a，开始时工作区干净；上一阶段请求准备退出计时已提交，属于实际进展。其后两次自动续转在任何工具命令前中断；本批重新核对 Git，未沿不存在的进程或半成品重启工作。
- S1-04：信息工作在 _slots 前还没有运行审计，等待取消无处留存。仅在现有 traces 按原 job 身份增加工作等待种类，不新增表、工作状态、提前认领或测试路径。

## 本批交付与核对

InformationJobRunner 在原 list_jobs 选定 processing 工作之后、共享槽位之前开始单调计时；取得时写 agent_job_wait（acquired），取得前取消或异常写 cancelled／failed。以当时原 job_id 和 revision 保存来源，不将版本变化后实际执行的结论倒填旧选择；_active_jobs、_run_job、工作预算和 Gate 均维持原顺序。取得槽位只说明容量可用，不代表工作已处理。

既有 agent_job 审计在 _run_job 内更晚才建立，无法保存排队取消，因此复用 traces 表增加这个有原工作关联的种类，而非把等待伪造成执行步骤。取得后工作异常不会改写等待为失败；等待记录写入失败时不静默继续工作。取消时若审计写入也失败，留下原日志并仍传播原取消，不按已取得处理。

查询投影单列 work_slot_wait_ms／state，页面沿已有 TraceTimings 显示工作槽位耗时和取得／取消／失败；与对话槽位分开。选中工作前的目录读取、同群前一工作、共享槽位内的方法维护、模型与外部执行不包含在此计时；不能当成从工作创建到执行的总排队。旧轨迹不回填。

- 阅读 job_runner._run_scene／_learn_scene 与 _run_job 的审计初始化时点、list_jobs 返回字段、EventStore.save_trace 写入语义、原 _trace 投影和 TraceDetails／TraceTimings 页面入口。
- uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/runtime/job_runner.py src/len_bot/web/query_service.py 退出 0；原前端目录 npm run build 退出 0，491 个模块、1.75s，日志 /private/tmp/lenbot-job-slot-build.log。git diff --check 无格式错误，构建产物不进 Git。
- 无获准同版面板、真实工作排队或取消记录；实际轨迹和页面仍待人工验收。未运行测试、夹具、断言探针、自动截图、回放、故障注入、覆盖率、依赖安装、服务或真实模型／平台调用；未读取真实配置／业务库或实发。
- 本批无源码定位、编译构建或实际业务失败原文。

## 待决定与接续

1. S1-04 还缺方法维护自身等待、选中工作之前与其他锁等待的区分；S1 请求材料与审计时间线同版观察仍不完整，继续逐一核对真实路径。
2. S2 完整活动段／未登记依赖仍有源码工作；额外原生字段保存、分类期限、许可证／素材授权、精确支持组合和公开承诺待决定。
3. S6 候选／现场／远端 CI／升级／外部迁移／发布及 S7 独立使用者／作者记录未完成；原 S3／S4／S5 待人工复核项保持。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
