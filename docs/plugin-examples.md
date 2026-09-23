# 三条插件参考路径

面向正在为当前接口世代 2 编写插件的开发者。本文按真实调用顺序阅读已有实现，不另复制一份插件代码；接口定义仍以[插件合同](plugins.md)为准，任务状态只在[产品路线](plan/README.md)。

以下均未在同版运行环境验收，不是已经开放的生产能力。需要实际操作时，先由维护者提供获准环境和真实业务来源；不为教程创建假事件、测试群、截图任务或新服务。配置只由原面板保存，代码升级按[运行手册](operations.md)停机处理。

| 路径 | 现有实现 | 当前参考边界 |
|---|---|---|
| 读取＋确定性命令 | [业务时钟](../local_plugins/local_clock/__init__.py) | 全部框架依赖从 plugins.api 导入；正常读取与可选模型表达共用同一观察 |
| 业务事件＋模型表达 | [真实开播事件](../src/len_bot/plugins/builtin/bilibili_live/plugin.py) | 发布、handler、表达和提交入口已有；完整传感器的延期刷新、存储查询及卡片仍含内部依赖，不能整包复制成独立公共接口范例 |
| 后台工作＋产物 | [当前群报告](../src/len_bot/plugins/builtin/group_summary/__init__.py) | 全部框架导入从 plugins.api 取得；业务模型、分析和渲染留在原插件内，外部字体由维护者配置 |

## 一、读取与确定性命令：业务时钟

### 1. 从描述符和配置开始

阅读 LocalClock 所在文件底部的 PLUGIN。它声明 api_version=2、自身 version、配置模型、创建函数及 REGISTER_TOOL 权限；没有中央注册清单补丁。ClockConfig 规定工具超时及简报调用额度，ClockSceneConfig.commands 决定两个精确命令是否开放。

启用前应在原配置面板确认业务 time.timezone、全局插件参数、目标群插件开关与 commands。本地目录须已纳入根 plugin_directories。不要用系统时区、环境变量或代码默认群替代缺失参数。[本插件说明](../local_plugins/local_clock/README.md)列出具体配置职责。

### 2. 只写一次读取服务

on_load 将 local_time_now 注册为 read 工具；参数模型 EmptySceneConfig 同时用于 Schema 与本地解析。read_time 只调用 context.now，使用已配置的时区生成 ClockReading，并返回带 fetched_at、coverage、sources 的 ToolResult。它不维护聊天历史、不调用模型、不发送。

该读取没有兄弟调用间的共享写入，显式声明 ordered=False。不要照此将所有 read 工具设为无序：文件写后读、页面操作和状态更新仍需要顺序；混合批次仍服从原循环规则。

### 3. 确定性命令复用同一工具

on_now 使用 call.invoke_tool，检查原结果状态，再严格解析 ClockReading，最后 call.submit_message。命令处理器不直接调用 read_time 绕过观察登记，不创建平台发送客户端。工具读取失败在此结束，不改为模型猜时间或自动重发。

正常使用“现在几点”时，预期链路是保存真实命令来源、handler 路由、工具观察、消息提交及最终回执；没有模型调用是正常设计，而非少执行了一步。提交返回与群实际送达分别看原记录。

### 4. 可选表达遵守当前 respond 合同

on_brief 先取得同一真实时钟观察，再通过 call.run_agent 选择已有模型路由；input_mode=source、output_mode=respond、tool_names 为空。它只组织文字，不新建历史或模型客户端。

普通消息的 messages[].source 使用本次输入提供的已读 M，next=end。成功回应由宿主根据消息归属派生，不填写 sources.status=replied；sources 仅声明 silent／incomplete 及原因。不要在代码中固定 M 编号或把观察 ID 当成实际阅读证明。“时间简报”与确定性命令的差异应体现在真实调用记录，不靠模拟结果证明。

## 二、业务事件与表达：沿真实开播场次阅读

这是一条已有业务链的阅读教程，不是已完成独立化的传感器模板。公共入口部分可以复用；完整包仍需迁移下述内部依赖。

### 1. 先声明业务事件，再发布真实采样

[描述符](../src/len_bot/plugins/builtin/bilibili_live/__init__.py)用 event_models 声明 live_started／live_ended 及各自载荷模型，并声明 EMIT_EVENT。插件的原共享轮询在 on_enable 通过 context.start_task 启动，停用由宿主管理，on_unload 关闭已有客户端。

采样与场次转换由业务插件判定；发布使用 context.emit_event(name, typed_payload, scene_id=..., event_id=..., timestamp=...)。身份来自真实平台场次与目标场景，不根据生成文案制造新事件或重复通知。首次采样、重复场次与延期逻辑必须阅读原实现，不能把每次状态查询都改成开播事件。

### 2. 让宿主保存来源和 handler 路由

on_load 注册 PLUGIN_EVENT handler，限定 plugin_event 来源。匹配只识别已声明事件名称；available 核对本群订阅，validate_announcement 核对保存载荷与当前场次，allow_mention_all 只读取当下群配置。真实来源和路由由宿主保存，模型不决定目标群或通知权限。

不要从轮询直接发送，不将任意群消息构造成开播载荷，也不要把工具注册看成事件发布权限。

### 3. 生成文字与提交分开

on_live_started 将真实 LiveSample 作为 ToolResult 材料，调用 call.run_agent，使用 materials 输入和 result_only 输出，返回类型为插件自己的 Invitation。模型只通过 return_result 返回文字；这一步不发送消息，也不需要 respond 的 sources。

取得文字后，原处理器再次核对当前场次，生成业务卡片，经 call.save_image 登记，再 call.submit_message 提交原场景的文字和图片。登记资产不代表平台已取得图片，提交不代表已送达；最终状态只取真实回执。已结束场次不能靠旧模型正文恢复资格。

### 4. 不掩盖尚未独立化的部分

refresh_deferred 仍直接使用内部场景管理和存储；共享轮询的事件存在查询以及卡片的资料／渲染模块也未全部纳入公共插件合同。本教程不重新实现这些业务，不把整个插件标成仅依赖公共入口。其迁移与现场运行证据仍是 S3-05／S3-06 的后续工作。

## 三、后台工作与产物：当前群增量报告

### 1. 描述符绑定专用工作合同

从 [WORK](../src/len_bot/plugins/builtin/group_summary/work.py) 阅读 operation、参数模型、修订模型、进度模型及回调，再看描述符的 work=WORK。模型输入、预算和任务队列仍由宿主提供；插件不建立独立工作库或额外模型客户端。

原 [配置模型](../src/len_bot/plugins/builtin/group_summary/config.py)包含分页、超时、表达要求、运行时工作预算及 render_font_path。字体路径相对插件目录或使用明确绝对路径；on_load 要求该文件存在。字体与上游素材授权另行确认，不随导出绘图辅助方法获得授权。

### 2. 用真实人类委托建立工作

[GroupSummaryPlugin.summarize](../src/len_bot/plugins/builtin/group_summary/plugin.py)读取本次 request_source，按该原话时间和业务时区解析范围，固定当前群、事件截点与分析要求。已有同范围活动工作返回原工作信息，不重置预算或再造一份。

新工作通过 call.stage_work 暂存，由原 respond 提交。插件不自填 requester、不写任务表。自然语言委托实际进入工具、暂存和提交分别查原记录；仅生成暂存引用尚不表示工作已开始。

### 3. 沿宿主上下文读取、分析和保存进度

[service.py](../src/len_bot/plugins/builtin/group_summary/service.py)通过当前调用读取原话、已保存资料和本插件工作；固定源快照不一致时结束，不改用当前全群范围继续。[analysis.py](../src/len_bot/plugins/builtin/group_summary/analysis.py)使用 PluginWorkContext 的 progress、save_progress、save_result、adopt_results 和 remaining_model_calls。

分批分析与合并通过已有 call.run_agent，仍受父工作预算及调用资格约束。成功批次可复用，读到、分析完成、合并、渲染各有原进度，未完成范围保留 unresolved。修订和取消使用原工作入口，不生成新身份绕开旧限制。

### 4. 渲染只负责字节，交付走原回执

[render.py](../src/len_bot/plugins/builtin/group_summary/render.py)使用 Pillow、插件业务报告模型、配置字体，以及公共 CARD_THEME／split_card_pages；不访问模型、网络或存储。辅助对象直接复用既有实现，没有复制样式、创建渲染服务或隐式读取字体。

图像字节由 call.save_image 登记，产物说明用 context.save_result 保存。最终 JobResult.delivery 使用 PreparedWorkDelivery，把本次结果 ID 与已保存图片片段交给原完成事件及交付链；后台工作不调用 submit_message。结构化报告存在、图片生成、资产登记、准备交付和真实送达逐项区分，不以本地路径证明平台收到文件。

正常工作应留下真实 job/revision、资料结果、模型调用、进度、资产、完成事件与最终回执。本文未产生这些现场记录；渲染失败、部分报告及跨修订复用仍需在获准环境依真实业务核对，不用伪造输入替代验收。
