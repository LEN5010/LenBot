# 识图与群聊表达研究

日期：2026-09-06。本轮只研究识图和人格表达，没有切换生产模型、修改人格或加入新的表达流水线。另行实现的 Reset 是管理功能。

## 识图：同一个模型不等于同一次认知

当前路径是：图片事件登记为本群资产 → 主认知看到文字与 asset_id → 调用 inspect_image → 独立视觉请求 → 返回文字描述 → 主认知组织回复。

依据：`src/len_bot/cognition/projection.py` 的图片引用投影、`src/len_bot/media/service.py` 的 `inspect`、`src/len_bot/cognition/social_core.py` 的工具循环。视觉请求只拿图片与问题，不携带完整群聊语境。把视觉路由设成主模型，仍然会多调用一次模型。

读取配置时，普通认知是 `L/deepseek-v4-flash`，深度认知是 `L/omen-alpha`，视觉是 `L/mimo-v2.5`。这些是本地部署别名，不能据名字推断输入能力。此前已保存的真实探针显示，当时该网关的 Flash 不接受图片；这不是本轮重新测试，也不证明其他别名的能力。

最近实际记录包括：早期未配置视觉、约 60 秒超时、成功但耗时约 23–39 秒，以及一次空输出或输出达到长度上限。两个不同提问下，同一 GIF 的同一首帧被识别成不同角色，主认知先后采纳了两个答案。现有错误把空输出和长度截断合并记录，无法进一步区分该次原因。

每次视觉请求还占一个认知模型步骤。已见两次看图加三次主认知，以及三次看图加两次主认知耗尽五步预算。因此问题包含调用延迟、预算消耗和识别不稳定，不只是图片下载。

若明确使用支持图片输入的主模型，最短路径是把当前图片像素与群聊上下文交给同一个认知请求；历史图片按需读取时，工具只负责获取、校验与返回引用，再把图片加入当前认知上下文，不再做独立视觉摘要。现有资产 ID、权限与来源记录可以继续使用。

Chat Completions 支持同一 user 消息中的文本及多个 image_url，也支持 Base64 data URL。当前 SDK 的 tool.content 不接受图片块，所以历史图片不能直接塞进工具文本；应通过带来源说明的 user 图文消息进入同一轮。图片还需要独立的媒体预算，不能把 Base64 当正文估算。[OpenAI 图像输入文档](https://developers.openai.com/api/docs/guides/images-vision)

采用这种路径须明确选定能接图片的认知路由，包括同轮可能切换到的模型；不猜模型能力，也不在失败后偷偷切到另一条识图链路。它能减少一次转述与调用，但不能保证角色识别正确。本轮没有改动识图架构或生产模型配置。

## MaiBot 的实际机制

研究版本为 main 的 [27836534bc9340335e038d7804fabefbc4d7bcaf](https://github.com/Mai-with-u/MaiBot/commit/27836534bc9340335e038d7804fabefbc4d7bcaf)，提交日期 2026-09-05。以下来自活跃代码路径，不能据此断言其群聊效果必然优于 len_bot。

| 机制 | 源码事实 | 对本项目的意义 |
|---|---|---|
| 简短人格 | 身份、行为准则与表达风格分开；默认身份只有一两句，偏日常，不刻意找话题 | 人物小传与角色梗的篇幅不等于人格稳定 |
| 专门生成正文 | Planner 决定参与并调用 reply 工具，Replyer 生成实际发言 | 让最终表达更少受工具和运行说明影响；但会增加调用与维护成本 |
| 接近日常对话的输入 | Replyer 过滤原始工具、参考、媒体工具、回想消息；真实已发送正文按 assistant 历史组织 | 工具资料可用于回答，但没有必要全部变成可见台词 |
| 情境表达样例 | 从真实聊天提取“情境→说法”，排除 bot 自己的发言；按当前语境选择，可选零条 | 有来源的表达比堆叠“自然一点”更具体 |

来源：[人格默认配置](https://github.com/Mai-with-u/MaiBot/blob/27836534bc9340335e038d7804fabefbc4d7bcaf/src/config/official_configs.py#L189-L280)、[Planner 提示](https://github.com/Mai-with-u/MaiBot/blob/27836534bc9340335e038d7804fabefbc4d7bcaf/prompts/zh-CN/maisaka_chat.prompt#L1-L31)、[reply 工具](https://github.com/Mai-with-u/MaiBot/blob/27836534bc9340335e038d7804fabefbc4d7bcaf/src/maisaka/builtin_tool/reply.py#L354-L371)、[Replyer 历史组装](https://github.com/Mai-with-u/MaiBot/blob/27836534bc9340335e038d7804fabefbc4d7bcaf/src/chat/replyer/maisaka_generator_base.py#L649-L744)、[历史过滤](https://github.com/Mai-with-u/MaiBot/blob/27836534bc9340335e038d7804fabefbc4d7bcaf/src/chat/replyer/maisaka_generator_base.py#L969-L983)。

需要补充三个边界：

- 表达学习不是默认全自动上线。新表达写入时尚未精选，默认使用人工精选项；候选不足时可以跳过，不强塞表达。角色分工也不意味着必须用不同模型。[表达来源过滤](https://github.com/Mai-with-u/MaiBot/blob/27836534bc9340335e038d7804fabefbc4d7bcaf/src/learners/expression_learner.py#L630-L679)、[新表达状态](https://github.com/Mai-with-u/MaiBot/blob/27836534bc9340335e038d7804fabefbc4d7bcaf/src/learners/expression_learner.py#L471-L485)、[表达选择](https://github.com/Mai-with-u/MaiBot/blob/27836534bc9340335e038d7804fabefbc4d7bcaf/src/chat/replyer/maisaka_expression_selector.py#L96-L184)
- Replyer 并非完全隔离推理内容：没有显式回复参考时仍可能收到 Planner 思考。不能把它概括为一个保证去除 AI 味的黑盒。[参考内容优先级](https://github.com/Mai-with-u/MaiBot/blob/27836534bc9340335e038d7804fabefbc4d7bcaf/src/chat/replyer/maisaka_generator_base.py#L574-L594)
- 发送前还有删除中文括号内容、拆句、随机错字和长回复替换等处理。这能改变表面语感，却可能损伤意思，不适合照搬。实验性的行为学习默认关闭，也不是自然表达的必需前提。[后处理](https://github.com/Mai-with-u/MaiBot/blob/27836534bc9340335e038d7804fabefbc4d7bcaf/src/chat/utils/utils.py#L567-L650)、[实验配置](https://github.com/Mai-with-u/MaiBot/blob/27836534bc9340335e038d7804fabefbc4d7bcaf/src/config/official_configs.py#L978-L1026)

## 对照 len_bot 最近的真实输出

目前能确认的现象：

1. 数学问题回答结束后，又追加让群友变成数学家的反问。多条回复反复带“～”“（笑）”，说明角色表演和附加笑点过于固定；问题不只是文字长短。
2. 当前图片已回答，却主动汇报别人的旧图片超时。这里涉及“此刻该不该发这条消息”，不是换几个口语词就能解决。
3. “心宜”确实出现在群友长名片中。不能直接断言是角色设定串入；更准确的观察是模型未经确认就把长名片缩成了称呼。
4. 查询时启用的表达样例为 **0 条**，所以已有样例机制没有为这些回复提供实际参照。人格配置却已明确要求不强塞梗、不必反问、不主动解释运行，再添加同类禁止句缺乏依据。

研究判断：最值得借鉴的是简明的人格、具体的情境表达，以及让真实对话突出于运行说明。len_bot 已有相应配置入口，暂时没有证据要求增加独立 Replyer、向量表达库、学习审核系统或风格重试。输出重复也可能受模型、历史自我发言和上下文组织共同影响，当前没有对照证据可把责任全部归给某一个因素。

本轮保持人格、提示词、表达样例和模型配置不变。后续若决定修改，应围绕以上具体原话验证变化，而不是新增自然度评分流程。
