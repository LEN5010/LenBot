# 静态插件目录

插件目录帮助用户了解插件的用途和安装位置。插件接口、配置和实际的运行能力仍然以安装后的 `plugin.toml` 为准。默认目录随宿主一起打包，内容见 [plugin_catalog.json](../src/len_bot/next/plugins/plugin_catalog.json)。

## 使用

在「插件 → 发现插件」中可以搜索插件的名称、用途、作者和能力，也可以按分类筛选。详情中包含用法、许可证、接口要求和源码位置。准备候选版本后，转到配置、应用和选择启用的群，不会自动对所有群启用。

在「目录来源」中填写 HTTP(S) JSON 地址，保存并读取。留空时恢复为随附的目录。对应的根配置如下。

```json
"plugin_catalog": {"url": "https://example.com/lenbot-plugins.json"}
```

远程目录只在明确刷新时读取，启动时和每次打开页面时都不联网。页面显示目录的来源和成功读取的时间。读取失败时显示原始错误，已经读取的同一来源的内容保留原来的时间。更换 URL 后，需要等待读取新的目录。目录快照只保存在当前进程中。

## 索引格式

顶层的 `version` 是索引格式的版本，目前为 1。每一项中的 `version` 是用于展示的插件版本。下面示例中的仓库和主页都是占位地址。

```json
{
  "version": 1,
  "entries": [{
    "name": "example_feed",
    "title": "示例订阅",
    "description": "按指定时刻发送订阅更新。",
    "authors": ["插件维护者"],
    "license": "MIT",
    "version": "1.0.0",
    "interface": 1,
    "category": "自动播报",
    "capabilities": ["无模型", "RSS"],
    "usage": ["填写订阅地址并保存。", "选择启用场景。"],
    "install": "git",
    "repository": "https://example.com/author/example_feed.git",
    "homepage": "https://example.com/author/example_feed",
    "ref": "v1.0.0"
  }]
}
```

- 同一个目录中的名称必须唯一。
- `capabilities` 和 `usage` 可以省略。`homepage`、`ref` 和 `requires_lenbot` 可以为空。
- `requires_lenbot` 是用于展示的宿主版本范围，实际的兼容性以安装清单为准。
- Git 条目需要一个独立的插件仓库，`ref` 支持标签、分支或提交。
- 所有条目都使用 `install: "git"`，业务插件独立安装和更新。
- 接口版本只是目录中的说明，实际的安装清单仍然按宿主的 v1 接口解析。

## 版本与发布

- 目录中的版本号只用于介绍。已安装版本和候选版本分别取自各自的清单，运行版本取自实际运行的插件。Git 提交和选定的 ref 单独显示。
- 指定了 ref 的安装使用 detached HEAD，并在实例的 `plugins/.installations/<name>.json` 中保存选择的 ref 和 commit。明确更新时重新获取。默认分支有新提交时，标签不会自动切换。
- 没有指定 ref 的安装跟随默认分支，更新时重新获取并准备候选版本。需要切换时，在更新框中填写新的标签、分支或提交。
- 第三方发布者先提供独立的 Git 仓库、清单和使用说明，再把条目加入自己选择的目录。私人站点和凭据保留在用户的配置中，个人仓库不会随主项目自动公开。

这只是一份分发索引，没有账号、评分、上传后台，也不增加新的权限层。手动填写仓库地址的安装和从目录安装使用同一个管理器。

## 官方插件发行准备

群聊总结、GSUID、A-SOUL、哔哩哔哩和[插件模板](https://github.com/lendevs/lenbot-plugin-template)都已经在 lendevs 组织下公开。随附的目录收录四个业务插件，模板作为开发的起点。

所有独立插件和模板都使用接口 1，`requires_lenbot` 为 `>=0.2,<1`。当前源码需要包含插件接口扩展的宿主开发提交，最低基线见各插件的 CHANGELOG。运行配置和业务数据库保持不变。

每个插件的 `CHANGELOG.md` 记录版本、最低接口能力、参数变化和数据迁移事项，标题格式为 `# X.Y.Z`。

插件仓库的 CI 和发行流程都调用宿主提供的可复用工作流（`.github/workflows/plugin-ci.yml` 和 `plugin-release.yml`）。打包统一使用宿主的 `scripts/package_plugin.py`，从 Git 中的运行文件生成 ZIP。ZIP 包含 prompts、skills、assets 和许可文件，本机的测试文件、环境和缓存不会进入包中。

只有维护者明确推送 `v<清单版本>` 标签后才会发行。标签必须与 `plugin.toml` 中的版本一致，发行说明取自 CHANGELOG 中对应版本的一节。

随附目录中的条目不手动编写，以各插件仓库的 `catalog-entry.json` 为准，用同步脚本写入并固定到提交。

```sh
uv run --no-sync python scripts/sync_plugin_catalog.py ../lenbot-plugin-asoul ../lenbot-plugin-bilibili@v1.1.0
```

参数是插件仓库在本机的检出目录，`@ref` 可选，默认为当前的 `HEAD`。脚本会核对条目与该提交中 `plugin.toml` 的名称、版本和接口是否一致，并要求这个提交已经推送到远端分支。

宿主 CI 中的 `official-plugins` 作业按目录中固定的提交检出每个 lendevs 插件，用当前的宿主运行这些插件的测试和打包检查。因此，如果宿主的改动导致目录中的插件无法运行，CI 会直接失败。

目前还没有发行。目录固定了四个公开插件已经核对过的开发提交，不会创建标签、Release 或发布镜像。更新和回退时，按插件的 CHANGELOG 选择与宿主匹配的版本，业务数据不会被清除。
