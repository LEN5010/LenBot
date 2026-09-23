# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 feat/s0-product-contract，基线 6b085d2，开始时工作区干净；上一阶段调用定位展示已提交，属于实际进展。
- S1-04：独立插件表达与插件等待恢复的共享对话槽位等待缺档。原普通对话已有单独等待记录，本批复用插件父执行审计与阶段展示，不另建计时表或排队系统。

## 本批交付与核对

_model_slot 仅在实际申请共享信号量时向原 execution.audit.model_slot_waits 追加等待项，用单调时钟记录取得前耗时和 acquired／cancelled／failed。一次父执行可能多次进入，故用本次原审计内的列表，不覆盖前一次等待；没有新业务状态或独立 trace 类型。取得后执行失败保留 acquired，不把模型时间计入等待，也不改变异常传播或信号量释放。

model_slot_owned 分支保持原样，不等待也不补零；该值表示已有执行容量，不由本次计时重新授予。等待计时不覆盖前面的 agent_lock、资料准备、工作槽位和模型调用，未登记的其他等待仍未知。原父处理／恢复 finally 保存审计，未额外写库或调用模型。

RuntimeQueryService 沿原嵌套审计收集 model_slot_waits，并投影 cognition_slot_wait_state；TraceTimings 复用已有等待时长，显示取得、等待取消或失败，不由等待结局推导后续业务成功。原对话等待也使用真实已存 state；无状态的旧记录不反推。列表是分段展示，不用其顺序重建全局时间线或相加为总延迟。

- 阅读 _model_slot 的两个真实调用方、父执行容量、独立表达及恢复审计保存、原对话等待和 AgentLoop／网关响应计时；确认插件排队取消此前尚未建立子运行审计，故将等待记录留在原父执行中。
- 阅读查询服务 _trace_runs 的既有 runs／agents 展开及耗时字段投影；新增等待叶项没有工具／模型步骤，不填其他阶段零值。沿现有 Vue 组件和文字状态展示，不新建图表或视觉系统。
- uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/runtime/plugin_interactions.py src/len_bot/web/query_service.py 退出 0；npm run build 在原前端目录退出 0，491 个模块、1.71s。日志 /private/tmp/lenbot-plugin-slot-build.log；git diff --check 无格式错误，产物不进 Git。
- 源码查找出现 `rg: src/len_bot/web/frontend/src/components/ModelCallDetails.vue: No such file or directory (os error 2)`，随后按实际文件清单改读 TraceTimings.vue／TraceDetails.vue 的既有路径；未新增猜测组件。无本批运行失败原文。
- 无获准同版面板，实际改动页面及正常等待／取消仍待人工验收；未为验证创建服务或制造等待场景。未运行测试、夹具、断言探针、自动截图、回放、故障注入、覆盖率、依赖安装或真实调用；未读取业务库／根配置、启动服务或实发。

## 待决定与接续

1. 继续 S1 的其他锁／工作排队与失败请求耗时缺口，先核对原记录再补实际边界；不把本批插件槽位计时说成全部排队已完成。
2. S1 其他请求材料、S2 完整活动段／未登记依赖仍有源码工作；额外原生字段保存、分类保留、许可证／素材授权、精确支持组合与公开承诺待决定。
3. S6 候选／现场／远端 CI／升级／外部迁移／发布及 S7 独立使用者／作者记录仍未完成；既有 S3／S4／S5 待人工复核项保持。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
