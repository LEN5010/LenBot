# LenBot 工程约束

用户当前的决定优先于本文。写代码、测试和数据格式的约定见 [CONTRIBUTING](CONTRIBUTING.md)。

## 交付

- 使用 uv。改前端时在 `src/len_bot/web/frontend` 执行 `npm run build`；产物 `web/static/dist` 不进 Git。
- 提交、推送、生产启动和真实发送分别服从用户当前授权。提交说明行为变化、核对范围与未确认项，不含构建产物；不重写 Git 历史。
- **工具和模型不把自己写进仓库。** 提交信息、PR 正文、代码注释和文档里都不出现 Claude、Codex、Anthropic、OpenAI、Copilot 或任何模型、Agent、CLI 的名字作为作者、共同作者、committer、生成者或致谢对象，也不写 `Co-Authored-By:` 指向非人类身份、`Generated with …`、`Co-authored with …`、🤖 徽章或 `noreply@…` 一类机器邮箱。工具默认要求的署名尾注同样不加；作者只有仓库所有者。
