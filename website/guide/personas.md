# 角色

角色决定 Bot 是谁、怎么说话。一个角色是一个目录：

| 文件 | 内容 |
|---|---|
| `persona.yaml` | 名字、身份、性格、别名、工具和技能许可 |
| `voice.md` | 说话方式 |
| `boundaries.md` | 底线 |
| `examples.yaml` | 人工写的情境和台词 |
| `knowledge/` | 按需检索的资料 |
| `stickers/` | 表情图片和 `index.yaml` |

首次配置会建好第一个角色。之后在面板的角色页编辑：

- 改完先开**草稿试聊**，用草稿和当前模型聊几句，满意再保存。试聊开始后发消息才调用模型。
- 可以导出、导入表单草稿；ZIP 导入会建一个新目录，不覆盖已有角色。
- 哪个群用哪个角色，在群设置里选。
- 角色改动在重启后生效。

写法和样例见[写角色包](../develop/personas)，可以参考仓库里的[示例角色](https://github.com/lendevs/LenBot/tree/master/examples/personas/companion)。
