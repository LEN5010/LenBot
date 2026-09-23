# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 feat/s0-product-contract，基线 2f8c14d，开始时工作区干净；上一阶段插件固定通知已本地提交，属于实际进展。
- S1-04：同一父执行的插件 Agent 先等待原 agent_lock，再可能等待共享模型槽位；原审计只记后者，串行锁等待取消缺档。复用父执行 audit 与阶段投影，不新增持久表或通用排队层。

## 本批交付与核对

_plugin_interactions._agent_lock 沿原 contextmanager 包裹既有 execution.agent_lock：每次等待向父 audit.agent_lock_waits 追加 waiting 条目，取得更新 acquired 与单调时长；取得前取消／失败更新 cancelled／failed 与异常类型后仍传播。锁体内的模型、工具、提交异常不改写锁等待状态；原锁获取、持有与释放顺序不变。子运行审计建立在锁之后，故早于该时点的取消只能归父执行，不能生成假子调用。

查询服务沿原父子运行收集新增等待叶项，TraceTimings 显示插件子调用串行锁的真实等待及状态，与共享模型槽位分开。两段可能属于同一插件调用，却不代表整个调用的独占总时长；未记录不补零，不给等锁过程补模型 call_id 或请求数。不存在相应业务来源时不根据等待值推断权限或送达。

- 阅读 run_agent 原 budget、agent_lock、_model_slot、子 audit 构造与父审计保存，以及 _trace_runs 嵌套展开和现有阶段组件；只修改实际锁持有处与现有只读投影。
- uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/runtime/plugin_interactions.py src/len_bot/web/query_service.py 退出 0；原前端目录 npm run build 退出 0，491 个模块、1.85s，日志 /private/tmp/lenbot-agent-lock-build.log。git diff --check 无格式错误，构建产物不进 Git。
- 无获准同版面板，也未在本批实际等待或取消；代码与构建不当运行验收。未运行测试、夹具、断言探针、自动截图、回放、故障注入、覆盖率、依赖安装、服务或真实模型／平台调用；未读取真实配置／业务库或实发。
- 本批无源码定位、编译构建或实际业务失败原文。

## 待决定与接续

1. S1-04 仍有工作选中前与其他所属锁等待以及同版人工证据缺口；S1-02 动态定义／材料范围仍需来源事实。不要无限加时钟来宣称完整互动延迟。
2. S2 当前 source_window_only，完整活动交换与 S2-03 压缩交接未完成；额外原生字段保存、分类期限、许可证／素材授权、精确支持组合与公开承诺仍待维护者决定。
3. S6 候选／现场／远端 CI／升级／外部迁移／发布及 S7 独立使用者／作者记录未完成，原 S3／S4／S5 待人工复核项保持。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
