# 通用 Agent 实施记录

面向实施者与运营者。基线 4da367d；分支 codex/general-agent-capabilities。批准信息型工具工作、图片/表情，以及 group:126300994 的门槛后实发。不包含代码执行、外部写操作、浏览器、MCP 或站点专用业务流程。

## 阶段与提交

| 阶段 | 提交 | 状态 | 验证 |
|---|---|---|---|
| 1 当前文档 | docs: establish current architecture and staged agent plan | 完成 | 引用检查，运行行为未变 |
| 2 通用工具 | feat: add structured tool observations and generic retrieval | 完成 | 171 项回归；HTML/文本/JSON、压缩、重定向、分页、scope、并发 |
| 3 交错回放 | test: add interleaved multi-turn agent evaluation | 计划中 | 工具期间输入，隔离发送，失败记录 |
| 4 独立工作 | feat: add runtime-owned information jobs and conversational steering | 计划中 | 原子性、版本、取消、预算、恢复 |
| 5 媒体投递 | feat: add scoped media understanding and paced message delivery | 计划中 | 视觉、资产、分段、回执、公平性 |
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
