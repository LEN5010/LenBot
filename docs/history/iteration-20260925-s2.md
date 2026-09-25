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

## B. 固定材料的版本依据

改动符号：`scenes/models.py` 新增 `SegmentMaterial`，`ConversationSegment` 增加 `materials`、`material_changes`；`SceneActor._material_changes` 取代原先整段 system 文字与工具列表的单一比较；`SocialCognitionCore.material_basis` 逐项生成材料；`RetrievalToolkit.configured_tool_names`；`PluginHost.request_material_hooks`。合同写入[上下文·当前段的原话窗口引用](../context.md#当前段的原话窗口引用)。

逐项结论（源码核对，未运行）：

| 材料 | 装配变化入口 | 跨重启依据 |
|---|---|---|
| 人格前缀 | 面板保存运行设置后，同进程下一次保存逐项比较文字 | 根配置没有修订号，停机手改不留记录：不能证明未变 |
| 固定组件（对话合同、表情偏好提示、插件入口提示） | 源码修改时递增修订号；同进程亦比较文字 | 修订号相同即未变 |
| 插件入口指令 | 同进程比较文字 | 没有依据 |
| 核心读取工具 | 同进程比较定义 JSON | Schema 含根配置数值上限，归 runtime_config：不能证明未变 |
| 固定提案工具、tool_search、计算类工具 | 源码修订号；同进程比较 JSON | 修订号相同即未变 |
| 插件工具 | 插件启停、改设置、换版本后同进程比较 JSON | 只记插件代码版本；插件可能按设置生成定义，版本相同不证明定义相同 |
| before_model／after_tool Hook | 插件启停或换版本后同进程比较插件版本 | 输出每次重新产生，没有版本 |

因此当前任何配置下重启后都会开启 process_restart 段，并在 material_changes 中列出无法证明的项；这是如实记录，不是缺陷修复。旧段没有 materials 时同样按无法比较处理。同进程下原先已有的换段条件不变，只是现在能指出具体变化项。

## C. 压缩与未完事项交接

改动符号：新增 `scenes/handoff.py`（`collect_handoff`、`handoff_states`），`scenes/models.py` 新增 `SegmentHandoffItem` 与 `ConversationSegment.handoff`；`SceneActor._save_conversation_segment` 在保存时生成交接；`ConversationContext.install_segment_handoff` 与可选区块 `segment_handoff`；`SocialCognitionCore` 在每次最终请求前按最近一次保存的清单重读状态。合同写入[上下文·段内容离开时的未完事项交接](../context.md#段内容离开时的未完事项交接)。

取舍：任务卡要求“唯一压缩入口”并禁止两个压缩器同时改主会话，因此没有新增模型摘要调用。会话段的内容只在 Actor 保存时离开（窗口变小或较早交换组不再带入），原话的语义延续仍只用历史维护摘要；离开的交换组中的资料正文不另作摘要，只能按 R 编号定位回读，这是有意保留的缺口。交接清单只存身份，每次展示前重读当前状态，已完成的只计数。

源码核对结论（未实证）：历史维护在活动对话租约期间以 `HistoryCommitDeferred` 延后提交，段保存要求活动租约，二者经同一 Actor 队列串行，因此不会同时修改场景状态；维护提交认识使知识修订递增，下一次段保存把 `knowledge_revision` 记为变化项并换段。交接不调用任何结案、取消、预算或送达写入口。

已知限制：出站事实只扫描最近 50 条批准记录，更早的未定发送不会被交接挑中（仍在原回执与面板中）；清单最多 40 项；新交接的事项从保存后的下一次请求才出现。
