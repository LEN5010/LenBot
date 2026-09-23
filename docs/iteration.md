# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 feat/s0-product-contract，基线 a7dfebe，开始时工作区干净；上一阶段 S2-02／05 旧插件资料当前归属复核已本地提交，仍待同版人工验收。
- S1-04：工作执行槽位等待已有按 job 记录；其前的 `list_jobs(scene_id)` 读取、解码及选取发生在 job 执行审计开始前，不能从既有槽位等待或运行总耗时反推。只在选中真实 processing 工作后随原 `agent_job_wait` 登记场景调度阶段，不新增计时表、trace 种类或假工作身份。

## 本批交付与核对

`InformationJobRunner._run_scene` 从本次场景目录读取前计时，选出首个 processing 工作后记 `work_selection_ms`，与之后独立计量的 `work_slot_wait_ms` 同存原等待 trace。取得槽位及取得前取消／失败均保存此已完成的选择阶段；没有候选、目录读取中断或失败时没有可归属 job，不写假记录或零值。原 job_id／revision、槽位获取、预算、执行、提交和发送边界不变。

`RuntimeQueryService._trace` 沿原只读计时投影暴露该字段，TraceTimings 在原等待段单列“本群工作目录读取与选择”，说明它不代表所选工作创建后的总排队时长。旧记录缺字段仍为未记录；不将目录、槽位、同群前一工作和模型耗时相加称总延迟。

- 阅读 `_run_scene` 的目录筛选、原 `agent_job_wait` 持久记录、查询投影和 TraceDetails／MessageProgress 对阶段组件的引用。首次在前端工作目录调用 Python 编译，输出原文 `Can't list 'src/len_bot/runtime/job_runner.py'`、`Can't list 'src/len_bot/web/query_service.py'`，尽管命令退出 0，不能算编译通过；随后在仓库根目录按真实路径重跑 uv compileall 两文件，退出 0。
- 前端目录 `npm run build` 退出 0，491 个模块、1.81 秒，日志 `/private/tmp/lenbot-work-selection-build.log`；`git diff --check` 退出 0。构建产物未纳入 Git。源码核对组件已被实际轨迹详情引用；没有获准同版面板，页面显示、真实目录等待及取消分支待人工观察，不把静态路径或构建当运行通过。
- 未运行测试、夹具、断言探针、自动截图、回放、故障注入、覆盖率、依赖安装、服务、模型／平台调用或实发；未读取真实配置和业务库。本批没有业务运行失败原文。

## 待决定与接续

1. S1-04 仍缺其他所属锁等待、没有候选时的场景级排队判读和同版用量／延迟验收；不为凑齐单一总耗时追加无归属计数或把缺值当零。S1-02 动态定义／材料与 S1-06 实链页面也未全面验收。
2. S2 仍为 `source_window_only` 增量，原生续接字段与必要回复片段的保存边界待维护者答复；完整压缩交接、旧摘要未登记依赖与自由表达仍有缺口。S3／S4／S5 主要待同版现场，尤其首选 Linux 容器群报告及文件交付回执未验收。
3. S5-03 分类期限、许可证／素材授权、精确支持版本、S6 候选与远端 CI／升级、S7 外部使用者／作者闭环仍未完成；没有生产或发布授权。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
