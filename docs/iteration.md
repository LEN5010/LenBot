# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 `feat/s0-product-contract`，本批基线 `887816c`，开始时工作区干净；上一阶段已本地提交。
- S3-03：任务卡要求纯读取不默认等同可并行。实际 register_tool、register_plugin_tool 和 PluginToolDefinition 都将 ordered 默认为 False，未声明顺序的读取因此进入同轮并发。本批定向修复这一个注册缺口，不重构全部内置插件。

## 本批实现与核对

三处 ordered 默认值统一为 True。沿原循环的 proposal_tool_names／ordered_tool_names 判断，一轮响应中只要有需顺序的调用，所有非终结调用就按原响应顺序执行；未新增队列、锁、权限表或调度层。显式 False 仍被保留，表示允许进入原并发分支而不是保证并发。业务时钟每次只读取时间、不依赖兄弟调用的共享写入，本批明确为 False；工作区与浏览器已有 True 声明不变。普通对话、后台工作和独立插件表达均传入原宿主顺序集合。

补充插件工具合同：同一参数模型生成 Schema 和严格解析；kind 与 ordered 分开；角色、范围、能力和提案准入；先筛选再排名；执行时重新核资格；空结果、部分取得和失败区分；保存正文与展示页分离。接口世代保持 2，不新增必填参数、不改已有显式值，但省略 ordered 的调度行为有变化；文档明确升级影响与作者选择，不承诺耗时不变。

| 任务要求 | 本批源码依据与处理 |
|---|---|
| 同一参数模型生成定义与本地解析 | PluginHost.get_tool_definitions／search_tools 调 parameter_model.model_json_schema，execute_tool 用同一模型 strict=True 解析；保留原实现 |
| 稳定定义不枚举本轮回执 | 宿主只使用注册模型生成定义，不把当前 result/job 写成动态枚举；编号由原资料与上下文提供，不新增第二套 Schema |
| 发现先过滤后排名，执行复核 | search_tools 候选先 has_tool 再 rank_discovery；get_tool_definitions、execute_tool 复用 has_tool；原权限撤销不由旧定义恢复 |
| 不把读取默认当并行 | 修改公共注册、宿主注册、定义模型三处默认；原批次顺序算法未改，时钟明确选择 False |
| 空结果、错误和分页分开 | 保留 ToolResult.status／error_stage；tool_search 无匹配 no_results；RetrievalToolkit 在原观察登记后分页，page_chars 不截断原保存结果，next_call 与 source_next_call 分工不变 |

- uv compileall 编译 plugins/base.py、plugins/host.py、plugins/models.py、cognition/agent_loop.py、local_plugins/local_clock/__init__.py，退出 0；git diff --check 无格式错误。未改前端，没有运行前端构建。
- 初次按假定文件名阅读出现 `rg: src/len_bot/cognition/toolkit.py: No such file or directory (os error 2)`、`rg: src/len_bot/cognition/loop.py: No such file or directory (os error 2)`；随后定位真实 tools/retrieval.py、cognition/agent_loop.py。另有 `rg: src/len_bot/plugins/builtin/group_summary/models.py: No such file or directory (os error 2)`、`sed: local_plugins/local_clock/plugin.py: No such file or directory`；之后沿实际 plugin.py／__init__.py 阅读。均为源码定位错误，不是业务运行失败。
- 同版运行仍未提供；默认顺序、显式并发、混合批次、权限撤销、空结果和分页均待人工验收。静态阅读与编译不证明运行通过，S3-03 未标为已验收。
- 未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；未启动服务、读取业务库、修改根配置、调用模型／平台或真实发送。

## 待决定与接续

1. 下一步 S3-04：沿现有 Hook、装载／卸载与取消路径核对局部修改合同和资源所有权；不为此新增全局锁或自动恢复机制。S3-02 的专用内部依赖随实际参考场景收窄，不为公共入口全量改写所有内置插件。
2. S2 活动段中不可由引用还原的原生响应字段／必要回复片段保存边界仍待维护者回答；已确认的宿主所有权和原事件权威不是额外片段保存授权。本批不扩大保存，不重复提问。
3. S1 其他提示／动态材料和等待／失败计时仍待补；S2 尚缺完整交换、其他编号／提案句柄、窗外范围、基础摘要及压缩交接。S4—S7 仍需逐项实施或核对，不宣称整体完成。
4. 许可证、字体／素材授权、长期数据保留、公开支持承诺仍待维护者决定；人物资料、五张常服与 19 张表情待人工采用，真实成果复用与文件交付仍待业务记录。
5. 300k／128k 仍为候选，根配置未改；既往令牌轮换与部署条件未复验。仅授权阶段性本地提交，不推送、合并、部署或真实发送。总体目标保持进行中。
