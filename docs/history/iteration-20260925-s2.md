# 2026-09-25 会话段原生交换、固定材料依据与压缩交接

分支 `feat/s0-product-contract`，起点 `1a29959`（无 result_id 回执最小保存决定已提交）。本批按维护者当晚指令连续实施 S2，只做源码与文档，**全程没有任何运行验证**：没有启动服务、调用模型或平台、读取真实根配置／业务库，也没有新增、修改或运行测试。每个单元的核对方式固定为改动文件 `uv run --no-sync python -m py_compile`、全包 `uv run --no-sync python -m compileall -q src` 与 `git diff --check`，三者退出 0 只说明语法可编译，不说明行为正确。

## A. 完整原生交换的持久引用与重建

改动符号：`scenes/models.py` 新增 `SegmentPageReply`、`SegmentReceiptReply`、`SegmentExchange`、`SegmentExchangeGap`，`ConversationSegment` 增加 `ordered_items`、`exchange_gap` 和换段原因 `exchange_unrecoverable`；`SceneActor._check_exchanges` 与保存命令的 `exchanges`／`carried`／`exchange_break`／`exchange_gap`；`ConversationContext` 的 `install_segment_exchanges`、`segment_exchanges`、`_restore_page`、`restored_receipt`、`_archive_tool_body`（由原 `externalize_old_tool_bodies` 拆出）与 `tool_reply_sources`；`AgentLoop.run` 新增 `closed_exchange` 回调；`SocialCognitionCore` 的 `save_state`、`close_exchanges`、`tag_exchanges`；段 trace 增加交换组数与缺口。合同写入[上下文·当前段的原生交换](../context.md#当前段的原生交换)。

实现要点与源码核对结论：

- 保存点沿用原话窗口已有的“最终请求前”保存；循环在成功终结、最后一步参数拒绝或新输入冲突后不再发请求，由新增 `closed_exchange` 补存最后一组。没有复用 `exchange_checkpoint`，避免改变工作路径的检查点行为。
- 每组的场景位置取产生它的那次请求的读取截点；同一轮新输入吸收后，后续组位置随之后移。
- 回复形态由呈现时记录的输入决定（`prepare_tool_results` 在接受页面时记下呈现名、offset、limit、单位与展示范围），不从审计投影猜。循环在呈现后另存的错误观察按整段正文作为 `page` 保存。
- Actor 以执行租约 mailbox 区分“同一次运行的后续保存”和“新运行的首次保存”：等待恢复沿用 episode 但属于新运行，若按 episode 区分会把恢复前的组误判为本次运行已存组，在首次保存时冲突。
- 取消：执行中未完成的组没有保存入口；被取消的租约本来也被 Actor 拒绝写段。提交后发布失败：终结组没有回复，属于不完整组，不保存，trace 的 `segment_close` 记 not_saved；收尾保存自身失败只记入 `segment_close`，不撤销已提交结果。
- 下一轮只在模型绑定与段一致、上一段无缺口时重建；每组重新按本群范围读观察、核对插件归属（`saved_result_issue`）、认识版本（原呈现失败检查）与展示范围，任一组失败全部不带入。旧 query_jobs 目录只保留编号，不把旧快照放回当前工作表，控制前仍须重新读取。

已知限制：资料页的 evidence_ref 每次重新签发，重建页与上一轮文字不保证逐字相同，供应商前缀缓存可能在第一处重建页处中断；像素不随组重建；原生交换随 Session 一起写入，会增大每次场景状态保存的体积，实际大小未测。编号：重建的 query_jobs 目录重新登记工作编号，若该工作不在当前事实中，编号可能与上一轮不同。

未确认（需人工现场）：多轮续接后模型是否正确理解重建组、供应商是否接受跨轮原样回传的续接字段、插件停用／认识修订后是否按预期换段、发布失败分支的 trace 记录。
