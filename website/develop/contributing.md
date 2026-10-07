# 参与开发

只编写插件时不需要阅读本页，请从[写插件](./plugins)开始。

## 搭建开发环境

需要 [uv](https://docs.astral.sh/uv/) 和 Node.js 22 或更新版本。

```sh
git clone https://github.com/lendevs/LenBot.git
cd LenBot
./scripts/install.sh       # 安装运行依赖，构建面板
uv sync --locked           # 再安装测试和 ruff 使用的开发依赖
uv run --no-sync pytest -q
```

`install.sh` 只安装运行依赖，不包含 pytest，因此运行测试前需要先执行 `uv sync --locked`。

- 修改面板后，在 `src/len_bot/web/frontend` 执行 `npm run build`。构建产物不提交到 Git。
- 修改这个文档站时，在 `website/` 执行 `npm ci`，再执行 `npm run dev` 在本地预览。`npm run build` 会检查失效的链接。

## 更多

- [开发指南](https://github.com/lendevs/LenBot/blob/master/CONTRIBUTING.md)说明代码约定和测试范围，以及数据格式和发行构建。
- [架构说明](https://github.com/lendevs/LenBot/blob/master/developer/architecture.md)介绍内部结构。
- 问题和建议请提交到 [GitHub Issues](https://github.com/lendevs/LenBot/issues)。
- 安全问题请按 [SECURITY](https://github.com/lendevs/LenBot/blob/master/SECURITY.md) 中的方式私下报告，不要公开提交 Issue。
