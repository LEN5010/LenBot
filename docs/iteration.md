# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，历史见[2026-09-22](history/iteration-20260922-s1.md)和[2026-09-23](history/iteration-20260923-s1.md)。

## 当前批次

- 分支 `feat/s0-product-contract`，本批起点 `dc919e8`，工作区当时干净；上批已提交事件诊断预览与下载。
- 持续推进授权包括阶段性本地提交，不包括推送、合并、部署、真实模型／平台调用或发送。当前阶段补 S1-03 的具体展示缺口，核对并保留 S1-05 已有实现。

## 交付与源码依据

| 边界 | 已核对的真实路径 | 本批处理 |
|---|---|---|
| 来源处理 | cognition/models.py 的 SourceOutcome；events/store.py 同事务保存 source_outcomes；scenes/reducer.py 按处理来源移除 pending_wakes | 保留五种来源状态，消息详情补原 unfinished；不因读过或整轮沉默改变本条结局 |
| 失败与拒绝 | agent_loop.py 保存终结参数失败、新输入冲突、预算异常等；agent_runtime.py 保存异常类型与阶段；GateDecision.record 保存 accepted/reason | 查询投影原 error_type、gate_accepted、gate_reason；页面按原类型显示，缺错误原文的 conversation_error 也保留问题入口 |
| 提交与发布 | scenes/actor.py 已有 checkpoint 返回 not_repeated；event_store 同事务提交；RuntimeGate.publish_committed 限原 pending 状态，失败保留持久提交 | 未改运行代码；补状态判读表和运营步骤，不重建事务或发布队列 |
| 发送与未知 | actions/delivery_store.py 尝试先落库；delivery_fact 无终态时返回 unknown；ActionQueue 先读事实且不自动重放；receipt_delivery_status 核对平台消息／文件 ID | 未改既有实现，不用事件名称冒充真实送达；保持模拟／Shadow 与线上分离 |

新增展示只说明本次候选或关联轨迹，不能覆盖先前已提交 checkpoint。工具错误统计从原 tool_outcomes 读取，属于整轨迹而非本条独占。解释和未完成项只展示已保存文本，不生成心理过程，不扫描错误正文猜分类。诊断下载继续不含错误原文，仅补原 Gate 接受状态。

## 核对结果与未确认

- uv compileall 编译本批 query_service.py，退出 0；前端 npm run build 退出 0，491 个模块、1.65 秒，日志 /private/tmp/lenbot-s1-outcomes-build.log。
- git diff --check 无格式错误。本批没有新增定位或编译失败，也没有运行业务故障观察。
- S1-03 本阶段展示源码完成；S1-05 现有实现已核对并保留，未为重复完成而改代码。两项记待复核，不记已验收。
- 没有可用同版面板，实际 Gate 拒绝、失败后继续、发布中断、发送未知及重复提交均待人工验收。未运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力任务；未读业务库、改真实配置或启动服务。阶段代码与文档一起本地提交，构建产物不入 Git。

## 接续与未确认项



1. S1-04 仍为实施中：首次／轮内后续请求的独立汇总、未记录的入场等待与失败阶段尚未补齐；同版缓存覆盖及取消／晚到 usage 的实际核对仍缺证据。现有阶段不能拼成完整无重叠总延迟。
2. S1-06 的事件诊断材料已有源码，待同版人工验收；S1-03／S1-05 的源码核对与定向展示已交付，保持待复核，未关闭人工验收。
3. S1-02 仍缺其他核心、工作与插件定义的留存，以及动态提示、人格、插件指令、工具资料页完整定位和完整请求逐字还原。涉及公共结构或保留政策时先确认既有决策边界。
4. 同版面板具备后，人工核对固定组件、阶段展开、缺档、失败调用、多来源轮次、模拟／Shadow 与窄屏，取得证据前不标验收通过。
5. 会话段与插件 API 世代的公共结构所有权仍待[三项决策草案](plan/s0-02-product-positioning-and-decisions.md)确认；许可证、字体素材与保留政策见[授权与支持底稿](plan/s0-04-06-license-and-support.md)。
6. 人物资料、五张常服和 19 张表情仍待人工预览、入库或采用；[素材交接](persona/asoul/README.md)保留原人格与频率要求。图片关系、研究成果复用、文件生成／登记／上传及纠正后的采用仍待同版业务证据；平台上传须以实际授权、挂载、版本及真实 file_id 确认。
7. 对话 300k／工作 128k 仍是候选容量，根配置未改。既往诊断令牌暴露后的轮换未确认，部署与可选能力未复验，见[缓存交接](serious-issue.md)和[历史记录](history/iteration-20260919-21.md)。

S2／S3 公共结构方向已向用户给出“采纳现有宿主所有权草案／暂缓／调整草案”选项，等待确认期间不实施相关新结构。许可证、素材授权与数据保留仍未决定；其他源码工作继续。
