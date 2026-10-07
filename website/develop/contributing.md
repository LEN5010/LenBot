# 参与开发

只写插件的话不用看这页，从[写插件](./plugins)开始就行。

## 跑起来

需要 [uv](https://docs.astral.sh/uv/) 和 Node.js 22 或更新版本。

```sh
git clone https://github.com/lendevs/LenBot.git
cd LenBot
./scripts/install.sh       # 装运行依赖、构建面板
uv sync --locked           # 再装上测试和 ruff 用的 dev 依赖
uv run --no-sync pytest -q
```

`install.sh` 只装运行依赖，不带 pytest，所以跑测试前要多一步 `uv sync --locked`。

- 改面板：在 `src/len_bot/web/frontend` 执行 `npm run build`，产物不进 Git。
- 改这个文档站：在 `website/` 执行 `npm ci`，再 `npm run dev` 本地预览；`npm run build` 会检查死链。

## 更多

- 代码约定、测试范围、数据格式和发行构建：[开发指南](https://github.com/lendevs/LenBot/blob/master/CONTRIBUTING.md)
- 内部结构：[架构说明](https://github.com/lendevs/LenBot/blob/master/developer/architecture.md)
- 问题和建议：[GitHub Issues](https://github.com/lendevs/LenBot/issues)
- 安全问题请按 [SECURITY](https://github.com/lendevs/LenBot/blob/master/SECURITY.md) 私下报告，不要公开提 Issue。
