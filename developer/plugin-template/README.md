# LenBot 插件模板

[English](README.en.md)

一个能直接运行的计数插件，用来起步写自己的 LenBot 插件。

## 开始

1. 在 GitHub 上点 Use this template 生成自己的仓库。
2. 改 `plugin.toml` 里的 `name`、`authors`、`version` 和 `license`。
3. 把命令和工具改成自己的名字，避免和别的插件重名。

## 示例做了什么

- `/计数`：读当前群的计数。
- `计数加一`：按配置里的 `step` 增加。
- `/计数清零`：清零。
- 工具 `counter_read`：返回当前群的计数，不发消息。

计数按群分开存，更新插件源码不会清掉。

## 测试

```sh
uv run --no-sync pytest -q tests
```

测试用宿主提供的 `PluginTest` 模拟消息和配置，不用启动 LenBot。CI 会安装 `.github/workflows/ci.yml` 里固定的宿主版本再跑测试，升级宿主时改这个版本。

## 发布

1. 改 `plugin.toml` 的版本号并提交。
2. 打 `v*` 标签，工作流会生成 ZIP 并挂到 GitHub Release。

用户可以在面板里填仓库地址用 Git 安装，也可以导入 Release 里的 ZIP。配置或数据格式有变化时写在 README 里；回退源码不会回退已经存下的数据。

接口、生命周期和配置说明见宿主仓库的 `developer/plugins-v1.md`。
