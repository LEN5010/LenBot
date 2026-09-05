# 通用 Agent 实施记录

面向实施者与运营者。基线 4da367d；分支 codex/general-agent-capabilities。批准信息型工具工作、图片/表情，以及 group:126300994 的门槛后实发。不包含代码执行、外部写操作、浏览器、MCP 或站点专用业务流程。

## 阶段与提交

| 阶段 | 提交 | 状态 | 验证 |
|---|---|---|---|
| 1 当前文档 | docs: establish current architecture and staged agent plan | 完成 | 引用检查，运行行为未变 |
| 2 通用工具 | feat: add structured tool observations and generic retrieval | 完成 | 171 项回归；HTML/文本/JSON、压缩、重定向、分页、scope、并发 |
| 3 交错回放 | test: add interleaved multi-turn agent evaluation | 完成 | 175 项回归；十二类脚本基线、面板模式选择、前端构建 |
| 4 独立工作 | feat: add runtime-owned information jobs and conversational steering | 完成 | 182 项回归；查询中修订/取消、预算失败、恢复、进展及面板 API |
| 5 媒体投递 | feat: add scoped media understanding and paced message delivery | 完成（真实视觉验收待配置） | 188 项回归；图片/引用/scope/预算/混排/失败、公平性、面板实测 |
| 6 互动质量 | feat: add conversational quality evaluation and reply feedback | 计划中 | 后续反馈、依据和模型对照 |
| 7 真实验收 | test: record live agent acceptance and controlled rollout | 计划中 | 真实工具、Shadow、指定群实发 |

每阶段检查相关测试和完整 pytest，前端变更构建并验证面板；文档同阶段提交，失败保留，提交不 push 或合并。

## 批准的能力与默认值

工具返回类型化状态、来源/时间与持久引用，长结果分页；核心及常用网页工具直接可见，其余按需发现。同工作复用结果，独立只读调用有界并发，重试与失败计入预算。

回放支持步骤交错；模型、工具、投递分别选择真实/模拟或 Shadow。模拟送达仅在隔离库，人工预写后续不计真实群友反馈。

工作由 Social Core 提案、Gate 提交，执行器不发送/写认识。默认每群 1、全局 2，16 个模型步骤、24 次工具、300 秒、64K 上下文，修订不重置预算。结果回流后按最新语境表达，不固定播报。中断保留观察并待核对恢复。

图片按 scene 保存，vision 独立配置，未配置不猜测。表情由运营维护；消息使用文字/图片段，最多三条，后续有限等待；失败/不确定停止本组，不重试未知发送。

反馈观察真实发送后的 5 分钟或 15 条人类消息；无反馈是未知。自动评价不写人格/记忆/任务。隔离比较 L/Flash 与 L/Pro，不改变生产路由。

真实验收与回退见 [运行手册](operations.md)。过时文档有效信息迁出后删除，旧失败和人格来源保留。

## 阶段 2 实测

2026-09-06：Python asyncio 文档（3660 字）、Trafilatura API 文档（30294 字）、普通 GitHub issue 页面（3906 字）均由同一通用读取路径获得正文。首次抽测发现压缩体二次解码，已修复并补回归；没有增加站点判断。真实搜索结果与正文均不代表模型已正确综合。ToolResult/ToolSource、read_tool_result、tool_search 和只读能力元数据已接线；旧字符串插件结果保持内容但标记 coverage=unknown。观察与待投递事件同事务保存，模型/检索派生内容不能充作独立记忆证据。控制面板查询经 QueryService 的 tool-results 接口。

## 阶段 3 基线

十二类跨主题输入与交错点位于 tests/fixtures/generic_agent_cases.json；[脚本基线](evaluation/runs/generic-stage3-scripted.json) 保存输入模型的消息、工具、观察、状态、回执、源码树哈希和失败字段。脚本结果仅证明链路；jobs/media 仍显式列为未支持，assessment 为 null，不计自然度通过。

命令：`uv run python scripts/eval_agent_cases.py --scripted --repeats 1 --output /tmp/agent-scripted.json`。真实模型去掉 --scripted 并指定 --provider-db；--model 只接受保存目录中的型号，--tool-mode real 单独运行真实工具。每个案例的模型调用默认上限 100，失败和未触发交错点令运行未完成。面板可分别选择工具模式和隔离投递模式，失败不显示推演成功。

## 阶段 4 实现

工作详情 agent_jobs 与 tasks 使用同一 ID，任务表是调度/送达状态权威。JobProposal(create/revise/cancel/resume) 与 Session、记忆、确认消息在 Gate 同事务提交；任务通用 payload 不能冒充工作类型。执行器独立并发、只读、不会写记忆或发送；目标版本、已消耗模型/工具/时间及资料引用持久化。默认 16/24/300s/64K，每群一个、全局两个；JOBS_ENABLED=false 可停用新工作执行。

后台步骤和原始工具观察不抢占社会读取截点；进展/结果事件才回到社会理解。report_progress 需已有资料引用并受30秒记录冷却约束，仍由 Social Core 判断说不说。只有含真实 job_id/job_revision 的就绪结果才能关联交付，队列发送前再次检查取消/版本。模型摘要和进展不能成为独立记忆证据。SDK隐式重试关闭；工作主/备模型尝试都消耗额度。

新增工作面板查看/修订/停止/恢复及资料分页；HTTP读经 QueryService，控制操作先记录运营事件再走Gate。七项新增工作测试覆盖事务失败、旧版本结果、重复完成、在途补充和取消、重启与主备调用预算；前端构建通过。真实模型工作判断尚待阶段6/7验证。

## 阶段 5 实现

原始 OneBot segments 保留，入站图片与 source Event 同事务登记为 scene-scoped media_assets。图片按需获取、限10MB/20MP、Pillow验证、哈希缓存；不读取任意本地路径。资产和预览须通过鉴权及scope查询。运营上传的素材可明确选global-safe，群内图不会自动晋升。MEDIA_ENABLED=false停用模型媒体能力和图片发送。

inspect_image 使用独立 routing.vision，不回退到文字模型；搜索图片/表情使用search_media。视觉解释属于model证据类型，保留原图事件，不能作为独立记忆证据；动图只分析首帧并明确标注。视觉实际调用、主备模型尝试和格式修复共用认知模型额度，保留最终回应步骤。模型页提供真实数字图片读取测试。

文字/图片segments为消息权威，旧content仍兼容；模型只能选择资产ID，适配器生成OneBot消息数组。ActionQueue每群保序，全局最多4个投递并发，后续片段按长度等待0.6–2秒；某片段失败/未知停止同组，队列join等待实际处理，发送前复查资产、工作版本和Shadow。

已在临时数据库的浏览器面板验证工作修订版本、图片预览及未配置视觉提示，无OneBot连接。前端构建通过；回归曾与构建同时运行撞到临时资产目录缺失，最终按构建→回归顺序验证。真实视觉型号、真实群媒体验收仍待后续，不将模拟红图结果算真实模型通过。
