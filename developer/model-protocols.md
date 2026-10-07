# 模型协议与用途绑定

服务商只保存协议、接口地址、密钥和可选网络代理；用途绑定保存模型、窗口、输出额度、超时和生成参数。聊天、视觉、记忆整理、学习、插件生成和任务都通过 `models.providers` 与 `models.roles` 解析，不各自维护密钥。

## 支持范围

| `api` | 地址示例 | 聊天与后台用途 | 续接方式 |
|---|---|---|---|
| `openai-chat` | 兼容服务的 API 根地址，一般以 `/v1` 结尾 | mind、vision、memory、learner、worker | 保留原生 assistant 字段和工具 ID |
| `openai-responses` | `https://api.openai.com/v1` | 同上 | `store=false`，携带 output 和加密 reasoning |
| `anthropic` | `https://api.anthropic.com/v1` | 同上 | 保留 Messages 内容块、thinking 和 signature |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta` | 同上 | Gemini API GenerateContent，保留完整 model parts 与 thoughtSignature |
| `openai-audio` | 兼容服务的 API 根地址 | asr | audio/transcriptions |
| `openai-embeddings` | 兼容服务的 API 根地址 | 本地记忆的 embedding | embeddings |

协议能表达工具与图片，不代表选中的模型具备相应能力。暂不接入 Vertex、Azure 专用鉴权、订阅 OAuth、厂商内置搜索或图像生成。OpenAI 兼容服务也可承载 ASR 和向量接口，是否实际可用以服务商为准；其他聊天协议不能代替专用 ASR／向量接口。

Gemini 模型名填短名，带 `models/` 的名字也可解析。宿主图片使用内联 data URL；不把普通图片网址伪装成 Gemini Files API 地址。其他三种协议可表达图片 URL 或内联图片，服务商可能限制 URL。

## 添加与测试

面板的服务商页可以读取模型列表；失败显示服务商原错，仍可直接手动填模型。列表里的窗口与输出额度只使用服务商明确返回的字段，其余按服务商文档填写。用途页只显示支持该用途的协议。

连接测试使用未保存的草稿；旧密钥留空时由后端读取，不回传浏览器。文本测试调用一次，工具测试调用两次，确认工具请求与结果续接；不发送 QQ 消息，也不验证图片、长上下文或全部业务效果。测试可能计费，服务商报告的用量会显示在结果里，测试不纳入聊天预算。

`temperature: null` 表示不发送温度。`reasoning_effort` 按协议映射到 reasoning effort、adaptive thinking 的 effort 或 Gemini thinkingLevel，必须填写该模型接受的值。`thinking_budget_tokens` 用于 Anthropic 的额度思考或 Gemini thinkingBudget，与思考强度二选一；Anthropic 思考模式温度必须留空，额度至少 1024 且小于输出额度。

兼容聊天可明确选择 `output_token_field` 为 `max_completion_tokens` 或 `max_tokens`。不会因请求失败自动换字段、删参数或换模型。原生协议必须使用 `history_policy: native`；`omit-reasoning` 只用于已确认自行保持签名续接的兼容路由。

`proxy` 是服务商级 HTTP(S) 代理地址，留空直连，不读取环境代理。该服务商的聊天、列表、测试、后台任务、ASR 和向量请求共用。代理地址不含用户名和密码；API 密钥只在宿主使用，任务容器仅收到本次任务令牌。

## 会话与工作代理

场景保存统一的可读文本和工具调用，同时保留原生响应及绑定来源。相同协议、地址、模型的续接原样使用签名数据；改绑后已完成的异源工具组转成历史资料，不能把旧签名发给新模型。未完成的异源工具组拒绝续接，需要先处理旧轮次或在面板开始新上下文。旧版兼容聊天的无来源历史仍按原有兼容接口发送；遇到服务商不接受旧字段时，按实际错误处理或开始新上下文。

工作代理为 Pi 配置相应的原生协议，将流式字节原样转发，并观察完成事件、工具参数与真实用量。生成参数、超时和额度由同一用途绑定控制；错误、截断和未消费完整的响应不能记成成功。宿主聊天调用当前仍为非流式，思考内容不进入群消息。

新增工作桥接路由后，现有任务镜像需从当前源码重建；不需要更改实例的格式编号，也不会自动发布镜像。真实厂商、代理网络和完整容器任务仍需在实际环境验证，本机协议回归不能代替这些结果。

协议依据：[Responses 状态与加密续接](https://developers.openai.com/api/docs/guides/migrate-to-responses)、[Messages](https://platform.claude.com/docs/en/api/messages/create)、[Gemini GenerateContent](https://ai.google.dev/api/generate-content)、[Gemini 签名](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)。
