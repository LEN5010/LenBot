# LenBot 网站

介绍首页和用户文档，用 [VitePress](https://vitepress.dev/) 构建，发布在 GitHub Pages 的 `/LenBot/` 下。

```sh
npm ci
npm run dev      # 本地预览
npm run build    # 输出到 .vitepress/dist，链接失效时构建失败
```

## 截图

截图来自一个临时演示实例：假的 OneBot、编造的群和消息、模拟发送，不调用模型，不涉及真实账号。

```sh
uv run --no-sync python website/scripts/demo_instance.py /tmp/lenbot-demo   # 保持运行
uv run --no-project --with playwright python website/scripts/screenshots.py # 另开终端
```

第一次截图前需要 `uv run --no-project --with playwright playwright install chromium-headless-shell`。

## 部署

`.github/workflows/pages.yml` 手动运行：默认只构建检查，勾选 deploy 才发布到 GitHub Pages。

## 许可

站点内容随仓库以 AGPL-3.0-only 发布。构建产物包含 VitePress 和 Vue 的运行代码，二者都是 MIT 许可，许可文本在 `node_modules/vitepress/LICENSE` 和 `node_modules/vue/LICENSE`。
