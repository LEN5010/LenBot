# 2026-09-24 会话段摘要范围形状修正

任务状态只见[产品路线](../plan/README.md)，当前交接见[当前任务](../iteration.md)。本页归档对应批次的静态核对，不是段保存或恢复的运行证据。

阶段提交：`c95e221`。

分支 `feat/s0-product-contract`，基线 `5145cf1`。上一批 `SegmentSummaryRef.range` 声明为严格 Python 元组，而 `ConversationContext` 的历史摘要侧记给出四项列表，`scene_sessions.state_json` 以 JSON 数组保存和重载；原字段形状与实际装配／持久化输入不一致。本批沿原字段改为长度恰好四项的 `list[int]`，不加转换层、表或新状态。批次 ID、字符串版本、位置顺序、Actor 租约资格与写事务不变，旧段无该字段仍读取为空列表。

同步[段结构](../plan/s2-01-conversation-segment.md#3-最小持久结构)和[上下文装配](../context.md#当前段的原话窗口引用)。静态核对 `context.build` 的四项数组、`social_core.finalize_request` 到 Actor 的列表传递、`model_dump(mode='json')` 与 `SceneSession.model_validate(saved)` 读取路径。没有接触或转换真实业务数据；此修正不证明原生交换、压缩或完整段完成。

`uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/scenes/models.py` 和 `git diff --check` 均退出 0，仅证明语法及差异格式。未运行同版段保存、重载、服务、模型或平台，未新增、修改或运行测试、夹具、断言式探针、截图、回放、故障注入、压力或覆盖率任务。没有本批业务失败原文。

S2 仍为 `source_window_only`；固定材料版本、跨轮原生交换与压缩交接未完成，必要回复片段持久边界待维护者答复。同版人工核对、首选 Linux／SnowLuma 现场、数据期限、许可证／素材授权、公开承诺与发布决定仍未完成。只做阶段性本地提交，不推送、合并、部署或实发。

## 当前段必要原生片段边界

阶段提交：`ab724c7`。分支同上，基线 `6ce0b77`。维护者确认宿主可在当前活动段保存无法由原事件和已存观察还原的必要原生 assistant 工具调用片段；已有观察的对应工具回执只存资料引用，不复制正文、不更改数据保留期限，也不把它当真实群发言。本批只将决定写入[原决策记录](../plan/s0-02-product-positioning-and-decisions.md)及[会话段设计](../plan/s2-01-conversation-segment.md)，没有新增未消费字段。

源码静态核对表明：普通对话只在最终请求前保存 `source_window_only` 段，尚未传 `exchange_checkpoint`；循环的成功终结直接返回，不能靠该回调覆盖结尾；工作归档保存完整工具正文，不能原样复用为本段“仅引用”表示。无 `result_id` 的暂存提案与终结回执如何处理已另请维护者决定。`git diff --check` 退出 0；无编译或业务运行、无业务失败原文，未执行仓库禁止的验证任务或真实操作。后续实现不能把设计确认记成跨轮续接已完成。

## 认识、提醒与等待编号的当前段预留

分支 `feat/s0-product-contract`，基线 `56ffe7f`。原 `TurnReferences` 仅将资料 R、工作 J 的编号身份与本轮可解析对象分开；认识 B、提醒 T、等待 L 仍按每轮登记顺序重新编号，同一对象换轮重现时可能得到不同短号。沿原 R／J 方法，为 B／T／L 增加当前活动段的身份预留映射，并在普通对话取得段时装入、最终请求装配后仅保存本轮实际重登记的短号与原对象 ID。旧段缺字段按空映射读取，Actor 沿原租约和 JSON 事务保存并拒绝保留短号改指别的对象。

预留映射不写入本轮 `memories`／`tasks`／`loops` 可解析表：本轮偏好、工具页或当前事实重新提供对象时才登记，并由现有认识有效状态、提醒控制原值、等待活动资格及 Gate 继续判定可否操作。没有增加事实正文、跨轮权限、原生工具交换或新的表／配置。严格 Session 写入新增字段后旧程序仍有既定回退限制；本批未转换真实数据。

`uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q` 编译 `scenes/models.py`、`scenes/actor.py`、`cognition/context.py`、`cognition/social_core.py` 退出 0；`git diff --check` 退出 0。只说明语法与差异格式，未运行段保存／重载或同版群聊。未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；未读取真实根配置／业务库、启动服务、调用模型／平台或实发。没有本批业务运行失败原文。

## 原生工具回执可还原形态核对

分支同上，基线 `0627e0d`。只读核对原循环、观察保存与呈现、原话范围、工作定位投影：`record_tool_result` 把工具错误导向已存观察，普通观察页的原 result_id 不包含实际分页范围；`read_message_range` 将原事件范围写入展示但不产生观察的 displayed_range；`query_jobs` 的目录不等于工作详情；暂存提案／终结回执没有 result_id，成功终结与提交后发布失败也不能靠现有部分 checkpoint 推断完整回复。Hook 改写同样不能由原观察 ID 自动重建。结论写入[会话段设计](../plan/s2-01-conversation-segment.md#回执定位不等于原生回复可还原)，没有新增持久字段或把资料正文复制到段。

这次核对收窄了 S2 实现入口：先按调用 ID 确认完整组，再按实际回复形态选择既有引用与范围；不能把所有 result_id 当同一种正文页。无 result_id 的最小回执政策仍待维护者答复，固定材料版本、完整交换、压缩交接与同版正常操作均未完成。本批只改文档；没有服务、模型、平台、真实数据或仓库禁止的验证任务，也没有业务失败原文。
