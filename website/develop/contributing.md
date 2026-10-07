# 参与开发

开发环境、代码约定、测试和发行构建见仓库里的[开发指南](https://github.com/lendevs/LenBot/blob/master/CONTRIBUTING.md)，内部结构见[架构说明](https://github.com/lendevs/LenBot/blob/master/developer/architecture.md)。

```sh
git clone https://github.com/lendevs/LenBot.git
cd LenBot
./scripts/install.sh
uv run --no-sync pytest -q
```

改面板时在 `src/len_bot/web/frontend` 执行 `npm run build`。改这个网站时在 `website/` 执行 `npm run dev` 预览。

问题和建议请提到 [GitHub Issues](https://github.com/lendevs/LenBot/issues)。发现安全问题时按 [SECURITY](https://github.com/lendevs/LenBot/blob/master/SECURITY.md) 私下报告。
