# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 feat/s0-product-contract，基线 7a5db6e，开始时工作区干净；上一阶段信息工作槽位等待已提交，属于实际进展。
- S1-04：同一工作槽位还服务方法维护，原 _learn_scene 在选定具体候选前等待，不能将它误归给来源工作。本批只补场景级等待记录，不增加候选身份、预算或新状态表。

## 本批交付与核对

InformationJobRunner._learn_scene 在进入原共享 _slots 前开始单调计时，取得后保存 skill_maintenance_wait 的 acquired 状态；取得前取消／失败分别记录 cancelled／failed。审计只属于当前场景调度，ref_id 为 scene_id，没有猜测或临时创建 candidate_id／job_id。原 maintain_candidates 循环、脏标记、预算、请求、实际采用与资源释放逻辑保持不变。取得槽位不证明有候选被处理。

等待审计沿原 traces 表和保存方法，不新增表、恢复路径或调用。记录失败不会被当成功继续维护；取消时若记录也失败，日志留下失败，原取消仍传播。取得槽位后的维护错误不记为等待失败。新时长不包括之后的候选筛选、模型调用、工作预算或结算。

查询服务在原阶段投影中加入 maintenance_slot_wait_ms／state，已有阶段组件以独立文案展示，并在轨迹类型目录标为“方法维护槽位等待”。这不把场景级等待接到具体候选或同轮用户消息，也不把几个时间求和成总延迟。

- 阅读 _start_learning／_learn_scene、maintain_candidates 的候选选取顺序、原 _slots 共享、EventStore.save_trace、阶段查询和界面路径；确认选取候选确实在取得槽位之后。
- uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/runtime/job_runner.py src/len_bot/web/query_service.py 退出 0；原前端目录 npm run build 退出 0，491 个模块、1.70s，日志 /private/tmp/lenbot-maintenance-slot-build.log。git diff --check 无格式错误，构建产物不进 Git。
- 无获准同版面板或自然发生的等待／取消；实际轨迹、页面、窄屏与键盘交互待人工验收。未运行测试、夹具、断言探针、自动截图、回放、故障注入、覆盖率、依赖安装、服务或真实模型／平台调用；未读取真实配置／业务库或实发。
- 本批无源码定位、编译构建或实际业务失败原文。静态计时实现不能证明时延改善、费用变化或业务完成。

## 待决定与接续

1. S1-04 还缺信息工作选中前及部分锁等待的可观察范围；没有原身份时不造 per-job 排队记录。接续 S2 可独立于活动原生字段决定的来源与未完事项核对，避免只为补指标无限扩大审计。
2. S2 完整活动段／未登记依赖仍有源码工作；额外原生字段保存、分类期限、许可证／素材授权、精确支持组合与公开承诺仍待决定。
3. S6 候选／现场／远端 CI／升级／外部迁移／发布及 S7 独立使用者／作者记录未完成；原 S3／S4／S5 待人工复核项保持。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
