# 群聊表达材料

表达沿两条路径使用同一份角色与场景材料：direct 由主脑写台词；voice 由主脑交接回应对象、目的、态度、必要事实与相关背景，表达器结合近期多人原话组织台词。两条路径都使用条件式的信息取舍、详略和结尾原则，不新增规划或审查调用。

## 来源与采用范围

本轮选段来自维护者提供的 `智脑-Z（10.1）-2.json`、`NVWA-Cosmos-TT.json`。前者读取 `prompts` 对应 identifier 的 `content`；后者读取 `extensions.tavern_helper.scripts[0].content` 中的提示字符串，未执行脚本。完整预设不随程序分发。

| 来源位置 | 采用范围 | 运行材料 |
|---|---|---|
| 智脑 `role_play_copy` | `【核心】`、`【三 · 目的与需求】`、`【六 · 连续性】` | `next_response_choice.md` |
| 智脑 `knowledge` | 未知、推测与认知修正的最后两段 | `next_response_choice.md` |
| NVWA `COSMOS_PORTED_GROUPS.roleplay.options[id=emotion]` | 第一段，情绪如何承接新信息 | `next_response_choice.md` |
| NVWA 同组 `id=independent` | 关系判断段，不取场外生活模拟 | `next_response_choice.md` |
| NVWA 同组 `id=lifelike` | 第一、二、四段 | `next_expression_principles.md` |
| NVWA `COSMOS_BAGU_OPTIONS[id=no-overexplaining]` | 解释是否带来新理解、减少重复与保留必要新信息两段；不取中间的抬下巴／门口示例 | `next_expression_principles.md` |
| NVWA 同组 `id=no-handoff-ending` | 第一、二、五段，保留不代替对方选择与自然结束的条件 | `next_expression_principles.md` |
| NVWA `COSMOS_ADAPTIVE_LENGTH_PROMPT` | adaptive 正文三段，不取固定字数分支 | `next_expression_principles.md` |
| NVWA `COSMOS_BAGU_OPTIONS[id=plain-description]` | 正文三段，保留必要修饰的适用条件 | `next_expression_principles.md` |

这些正文使用原语言、原标点和完整选段，未另写同义规约。仅有以下字面替换：

| 原文 | 运行文件 |
|---|---|
| `NVWA` | `$name`，装配为当前角色名 |
| `{{user}}` | `the other person`，具体对象由当前语境判断 |
| `narrative response` | `chat response` |
| `the narration` | `the response` |
| `the narrator` | `the speaker` |
| `the narrative prose`、`the prose` | `the reply` |
| `current plot` | `current conversation` |
| `paragraphs, or scenes` | `paragraphs, or responses` |
| `enriching the scene` | `enriching the reply` |

材料实际位于 [`src/len_bot/prompts/`](../src/len_bot/prompts/)。来源提示字符串中的换行转义解码为真实换行，不改变句子。长篇叙事、代演真人、虚构场外活动、外显思考格式、成人协议、宏和脚本不进入运行材料。

## 装配职责

- `next_character.md`：同一份身份、行为、口吻、边界、人工样例与场景补充，供主脑和表达器使用。按需角色知识继续由原工具读取。
- `next_response_choice.md`：只给主脑，人物倾向参与本轮判断，不要求输出心理分析。
- `next_expression_principles.md`：主脑和表达器共用，每份请求各装配一次。
- `next_direct.md`／`next_intent.md`／`next_voice.md`：各路径的实际工具与交接职责，不复制原文原则。
- `ChatContext`：准备多人对话稿、引用、黑话、学习参考和本轮表达意图；较早回想与宿主当前状态分别标明来源。`ChatExpression` 负责表达选择调用与发送，保留原来的事实和发送回执。

本轮意图通过现有 `say.content` 传递，不增加必填心理字段；长度仍是详略倾向。say／react 的可选 `end_turn` 只表达模型的收轮决定：成功表达且整组没有失败时，处理完整工具组和可追加的新消息后直接收轮，不再为结束单独请求主脑；需要继续看结果或行动时保持 false。任务结果先按当前请求选择内容，交付登记与平台上传继续分别表达。角色人工文件和模型绑定不因这些工程材料而被改写。

## 角色草稿

角色设定页可导入／导出与表单字段一致的 JSON，先试聊再保存采用。载入不写当前角色，不包含角色 ID、工具／技能许可和图片素材；完整角色迁移仍使用 ZIP。采用样例可使用原有人工材料或实际回放原文，并在外部来源说明中区分完整回复和摘录，不把裁出的首段描述为原模型已经自然收尾。
