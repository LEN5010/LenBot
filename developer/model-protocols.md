# 模型协议与用途绑定

服务商只保存协议、接口地址、密钥和可选的网络代理。用途绑定保存模型、窗口、输出额度、超时和生成参数。聊天、视觉、记忆整理、学习、插件生成和任务都通过 `models.providers` 与 `models.roles` 解析，不各自维护密钥。

## 支持范围

| `api` | 地址示例 | 聊天与后台用途 | 续接方式 |
|---|---|---|---|
| `openai-chat` | 兼容服务的 API 根地址，一般以 `/v1` 结尾 | mind、vision、memory、learner、worker | 保留原生 assistant 字段和工具 ID |
| `openai-responses` | `https://api.openai.com/v1` | 同上 | `store=false`，携带 output 和加密 reasoning |
| `anthropic` | `https://api.anthropic.com/v1` | 同上 | 保留 Messages 内容块、thinking 和 signature |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta` | 同上 | Gemini API GenerateContent，保留完整 model parts 与 thoughtSignature |
| `openai-audio` | 兼容服务的 API 根地址 | asr | audio/transcriptions |
| `openai-embeddings` | 兼容服务的 API 根地址 | 本地记忆、场景学习的 embedding | embeddings |

协议能够表达工具和图片，并不代表所选的模型具备相应的能力。

暂不接入以下功能。

- Vertex
- Azure 专用鉴权
- 订阅 OAuth
- 服务商内置的搜索或图像生成

OpenAI 兼容服务也可以提供 ASR 和向量接口，是否实际可用以服务商为准。其他聊天协议不能代替专用的 ASR 接口或向量接口。

Gemini 的模型名填写短名称，带 `models/` 前缀的名称也能解析。宿主发送的图片使用内联 data URL，不会把普通的图片网址伪装成 Gemini Files API 地址。其他三种协议可以使用图片 URL 或内联图片，但服务商可能限制 URL。

## 添加与测试

在面板的服务商页可以读取模型列表。读取失败时显示服务商返回的原始错误，仍然可以直接手动填写模型。列表中的窗口和输出额度只使用服务商明确返回的字段，其余的按服务商文档填写。用途页只显示支持该用途的协议。

连接测试使用尚未保存的草稿。旧密钥留空时由后端读取，不会回传到浏览器。

已有服务商可以改名。保存请求通过 `previous_alias` 指明原名称，一次保存更新用途、本地记忆和场景学习的引用；这个字段不写入根配置。只改名或补上地址末尾的斜杠可以保留旧密钥，修改协议、地址或代理需要重新填写密钥。模型列表显示名称和完整 ID，选择时写入 ID。

- 文本测试调用一次模型。
- 工具测试调用两次，确认工具请求和结果能够续接。
- 向量测试调用一次 embeddings 接口，验证返回的向量与维数，不写入业务索引。

测试不发送 QQ 消息，不验证图片和长上下文，也不验证全部业务效果。测试可能产生费用，服务商报告的用量会显示在结果中，但测试不计入聊天预算。

在「模型 → 向量模型」中分别配置 `memory.local.embedding` 和 `scenes.*.learning.embedding`。两者只能绑定支持 embeddings 的协议，维数留空时使用服务商返回的维数。更换地址、模型或维数后，已有索引需要停机重建；重启本身不重建。`pending-restart` 接口返回已有索引的维护项目。兼容性依据地址、模型和维数判断，服务商别名不影响已有向量。

记忆查询遇到索引绑定不匹配、缺少待建向量或向量 HTTP 查询失败时，记录错误后使用现有全文索引；查询不修改或重建旧向量。自动召回的全文分支只使用消息正文，向量分支继续使用带说话者的上下文。正常的混合检索也会合并自动全文召回结果。写入和离线重建仍要求选定模型返回合法向量。`memory/state` 的 `index` 字段返回已有绑定、运行配置、当前检索方式和重建状态。

### 生成参数

- `temperature: null` 表示不发送温度。
- `reasoning_effort` 按协议映射到 reasoning effort、adaptive thinking 的 effort 或 Gemini 的 thinkingLevel，必须填写该模型接受的值。
- `thinking_budget_tokens` 用于 Anthropic 的额度思考或 Gemini 的 thinkingBudget，与思考强度二选一。Anthropic 的思考模式下，温度必须留空，额度至少为 1024，并且小于输出额度。

兼容聊天接口可以明确把 `output_token_field` 选为 `max_completion_tokens` 或 `max_tokens`。请求失败时，不会自动更换字段或删除参数，也不会更换模型。原生协议必须使用 `history_policy: native`。`omit-reasoning` 只用于已经确认会自行保持签名续接的兼容路由。

### 代理

`proxy` 是服务商级别的 HTTP(S) 代理地址，留空时直接连接，不读取环境变量中的代理。同一服务商的所有请求共用这个代理，包括聊天、模型列表、测试、后台任务、ASR 和向量请求。代理地址中不包含用户名和密码。API 密钥只在宿主中使用，任务容器只会收到本次任务的令牌。

## 会话与工作代理

场景以统一的格式保存可读文本和工具调用，同时保留原生响应和绑定来源。

聊天模型、服务商地址与协议可以在面板中保存，重启后生效。切换到原生协议时，编辑器把兼容聊天专属的续接方式与输出字段恢复为原生默认值。

- 协议、地址和模型都相同时，续接原样使用签名数据。
- 更换绑定后，已完成的异源工具组会转为历史资料，旧的签名不会发给新模型。
- 未完成的异源工具组拒绝续接，需要先处理旧的轮次，或者在面板中开始新的上下文。
- 旧版兼容聊天中没有来源的历史，仍然按原有的兼容接口发送。服务商不接受旧字段时，按实际的错误处理，或者开始新的上下文。

工作代理为 Pi 配置相应的原生协议，把流式字节原样转发，并观察完成事件、工具参数和真实用量。生成参数、超时和额度由同一个用途绑定控制。出错或被截断的响应，以及没有完整读取的响应，都不能记为成功。宿主的聊天调用目前仍然是非流式的，思考内容不会进入群消息。

新增工作桥接路由后，现有的任务镜像需要从当前源码重新构建。这不需要修改实例的格式编号，也不会自动发布镜像。真实的服务商和代理网络，以及完整的容器任务，仍然需要在实际环境中验证，本机的协议回归测试不能代替这些结果。

协议依据：[Responses 状态与加密续接](https://developers.openai.com/api/docs/guides/migrate-to-responses)，[Messages](https://platform.claude.com/docs/en/api/messages/create)，[Gemini GenerateContent](https://ai.google.dev/api/generate-content)，[Gemini 签名](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)。
