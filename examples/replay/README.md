# 表达回放用例

一组通用的群聊情境，用来对照修改提示词或角色包前后的回复。人物、账号和对话都是虚构的，角色使用[示例角色小然](../personas/companion/)。

每个用例只有被 @ 的消息会让 Bot 开口，前面没 @ 的消息作为上下文；`expect` 写的是期待的回应方式，由人对照判断，不自动打分。

## 运行

回放会真实调用模型，按服务计费。在本目录：

```sh
cp lenbot.config.example.json lenbot.config.json
# 填写 models 里的接口地址、密钥和模型名
uv run --no-sync lenbot-eval plan expression --profile current   # 只检查，不调用模型
uv run --no-sync lenbot-eval run expression --profile current    # 确认后输入 run 开始
uv run --no-sync lenbot-eval report <run 目录名>
```

结果写在 `runs/`。改动前后各跑一次，用 `lenbot-eval compare <旧> <新>` 对照。`lenbot.config.json`、`runs/` 和 `chat.sqlite3` 已被 `.gitignore` 排除。

## 用例

| ID | 情境 | 看什么 |
|---|---|---|
| several-calls-at-once | 三个人几秒内先后 @ | 一句话一起接，不逐条回复 |
| upset-after-bad-luck | 抽卡歪了，让 Bot 别笑 | 站在对方那边，不继续逗 |
| concrete-bad-luck | 交卷时平板没电 | 接住具体的事，不空喊加油 |
| answer-a-fact | 问角色生日 | 直接给日期 |
| teasing | 被调侃偷吃宵夜 | 斗嘴有分寸 |
| good-news-sticker | 收到 offer | 表情承担情绪，文字不重复强调 |
| follow-the-topic | 聊完海报再问 Bot 今天干嘛 | 和前面的话题接得上 |
| spicy-hotpot | 被叫去吃重庆火锅 | 结合不吃辣自然回应 |
| are-you-a-bot | 群里聊谁是机器人 | 回答自然，不生硬声明 |

新增用例时沿用 `cases.json` 的格式：`message` 步骤放原始 OneBot 事件，`await_turn` 等待第几轮结束，`observe` 再观察几秒。
