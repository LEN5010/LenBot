# 静态插件目录

目录帮助用户发现用途和安装位置；插件接口、配置与实际运行能力仍来自安装后的 `plugin.toml`。默认目录随宿主打包，内容见 [plugin_catalog.json](../src/len_bot/next/plugins/plugin_catalog.json)。

## 使用

「能力 → 插件 → 发现」可搜索名称、用途、作者和能力，按分类筛选。详情包含用法、许可证、接口要求和源码位置；准备候选后转到配置、应用与选群，不自动对所有群启用。

在「目录来源」填写 HTTP(S) JSON 地址，保存并读取；留空恢复随附目录。根配置对应：

```json
"plugin_catalog": {"url": "https://example.com/lenbot-plugins.json"}
```

远程目录在明确刷新时读取，不在启动或每次打开页面时联网。页面显示来源及成功读取时间。读取失败显示原错，已读取的同源内容保留原时间；换 URL 后等待读取新目录。目录快照只存在当前进程中。

## 索引格式

顶层 `version` 是索引格式版本，目前为 1；每项 `version` 是展示用插件版本。下面的仓库和主页是占位示例：

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

同一目录的名称唯一。`capabilities`、`usage` 可省略；`homepage`、`ref`、`requires_lenbot` 可为空；requires_lenbot 是展示用宿主版本范围，实际兼容以安装清单为准。Git 条目需要独立插件仓库，ref 支持标签、分支或提交。条目统一使用 `install: "git"`，业务插件独立安装与更新。接口版本只是目录说明，实际安装清单仍按宿主 v1 接口解析。

## 版本与发布

- 目录版本号是介绍，已安装与候选版本分别取自各自清单，运行版本取自实际运行的插件；Git 提交和选定 ref 单独显示。
- 指定 ref 的安装使用 detached HEAD，并在实例 `plugins/.installations/<name>.json` 保存选择和 commit。明确更新时重新获取它；标签不会因默认分支有新提交而自动切换。
- 未指定 ref 的安装跟随默认分支，更新重新获取并准备候选。需要切换时在更新框填写新的标签、分支或提交。
- 第三方发布者先提供独立 Git 仓库、清单与使用资料，再将条目加入其选择的目录。私人站点和凭据留在用户配置，个人仓库不随主项目自动公开。

这是一份分发索引，无账号、评分、上传后台或新权限层。手填仓库入口与目录安装使用同一个管理器。

## 官方插件发行准备

群总结和 GSUID 已有公开独立仓库；A-SOUL 与 B 站本机源码尚未公开，不加入随附目录。所有独立插件和模板使用接口 1、requires_lenbot >=0.2,<1，采用本次接口扩展的版本须等最终 0.2.0 基线。运行配置与业务库不变。

每个插件的 `CHANGELOG.md` 记录版本、最低接口能力、参数变化和数据迁移事项。`scripts/package.py` 从 Git 中的运行文件生成 ZIP，包含 prompts、skills、assets 和许可；本机测试文件、环境与缓存不进入包。`release.yml` 仅在维护者明确推送 v<清单版本> 标签后发布，不覆盖版本。

当前仍未发行，目录只收录已有公开插件仓库，并锁定已核对的开发提交；不会创建标签、Release 或发布镜像。未公开插件的条目仅保存在本机独立仓库 `catalog-entry.json` 中。CI 同样固定开发提交，不依赖未创建的 v0.2.0 标签。更新、回退按插件 CHANGELOG 选择与宿主匹配的版本，不清除业务数据。
