# 参与开发

[English](CONTRIBUTING.en.md)

写代码、测试、文档和提交的约束统一写在 [AGENTS.md](AGENTS.md)，人和工具都按它来。只写插件的话不用读这里，从[插件开发](developer/README.md)或[插件模板](https://github.com/lendevs/lenbot-plugin-template)开始就行。

## 本机开发

需要 [uv](https://docs.astral.sh/uv/) 和 Node.js 22。

```sh
./scripts/install.sh       # uv sync，并构建面板
uv sync --locked           # 想跑测试时，再装上 dev 组
uv run --no-sync len-bot   # 启动；没有配置时打开首次向导
```

仓库根目录就是本机实例：`lenbot.config.json`、数据库、`state/`、`personas/` 都放在这里，`.gitignore` 已经排除。开发和本机运行是同一份代码，用 `uv` 直接启动，不需要另外安装。

改了前端后在 `src/len_bot/web/frontend` 执行 `npm run build`，构建产物 `web/static/dist/` 不进 Git。开发前端时也可以用 `npm run dev`。

### 在副本上试

不想动正在用的数据时，先停掉 Bot，复制一份测试实例：

```sh
uv run --no-sync python -m len_bot.next.maintenance.test_copy . /tmp/lenbot-test --panel-port 8089
cd /tmp/lenbot-test && uv run --project /path/to/LenBot --no-sync len-bot
```

命令只复制配置和配置里引用到的实例文件（数据库、记忆、角色、任务目录等），不复制源码。副本改成模拟发送、不连 OneBot，面板换到指定端口；指向原实例内的绝对路径也会改到副本里。模型、插件和任务服务在副本里照常执行，会产生费用。

## 源码结构

运行核心在 `src/len_bot/next/`，入口是 `len_bot.next.host`，`len-bot` 命令通过 `launcher.py` 启动它。整体设计见[架构](developer/architecture.md)。

| 位置（相对 `src/len_bot/next/`） | 职责 |
|---|---|
| `host.py`、`launcher.py`、`instance_lock.py` | 宿主装配、启动器、实例锁 |
| `config.py`、`configuration/` | 根配置和各领域配置 |
| `runtime/` | 平台与场景运行、生命周期、日志、数据保留 |
| `platform/` | 平台身份、统一消息，以及 OneBot 适配器 |
| `chat/` | 场景会话、注意力、上下文、表达、工具分派、提醒 |
| `models/` | 模型请求、预算和用量 |
| `persona/`、`learning/` | 角色包，以及从群聊学习说法、黑话、表情和回复效果 |
| `memory/` | 本地记忆的正文、索引、召回和后台整理 |
| `work/`、`browser/` | 后台任务（Pi 容器）和浏览器协作 |
| `storage/` | 数据库结构和编解码 |
| `plugins/`、`builtin_plugins/` | 插件运行、安装和内置插件 |
| `plugin.py`、`plugin_testing.py`、`text_cards.py`、`image_assets.py` | 给插件用的公开接口 |
| `tools/`、`media/` | 网页、技能、MCP 等工具，图片和语音处理 |
| `panel/` | 面板后端和首次配置向导 |
| `maintenance/` | 停机后执行的维护命令：数据升级、重建索引、插件依赖、存储池、测试副本 |
| `trials/` | 面板里的隔离试聊 |

其他目录：

- `src/len_bot/prompts/`：提示词。
- `src/len_bot/builtin_skills/`：任务技能。
- `src/len_bot/web/frontend/`：Vue 面板。
- `src/len_bot/eval/`：表达回放。
- `tests/`：测试。
- `docker/next-worker/`：任务镜像。
- `deploy/`：部署资料。
- `scripts/`：安装和打包脚本。
- `examples/`：示例角色和公开回放用例。

维护命令统一用 `python -m len_bot.next.maintenance.<模块>` 调用。包内直接从具体模块导入，`__init__.py` 不集中重导出。

## 测试

```sh
uv run --no-sync pytest -q
uv run --no-sync python -m compileall -q src/len_bot
```

测试只覆盖外部协议边界、数据迁移、权限和配置校验，具体范围见 [AGENTS.md](AGENTS.md#测试与报告)。

修改提示词或角色表达时，用[表达回放](examples/replay/)对照改动前后的回复。发现坏回复，先把情境写成回放用例，再改提示词。

## 数据格式

业务数据库从公开基线 v1 开始。改表结构时，在 `storage/schema.py` 直接建新结构，同时在 `maintenance/migrate.py` 的 `UPGRADES` 里加一步从上一版升级，并提高 `storage/store.py` 的 `FORMAT_VERSION`。升级只在停机时由维护命令执行，运行时不写新旧兼容分支。

## 构建与发布

```sh
uv run --no-sync python scripts/build_release.py /tmp/lenbot-release
```

这条命令在副本里重建面板，生成源码包、wheel 和 Linux／macOS 部署包，不启动、不上传。正式发布走 [Release 工作流](.github/workflows/release.yml)，步骤见[发行指南](deploy/releasing.md)。

[CI](.github/workflows/ci.yml) 会编译、跑测试、构建面板并打包。CI 通过不代表页面交互和真实聊天也没问题。

## 提交

- 问题和改动分别用[缺陷模板](.github/ISSUE_TEMPLATE/bug.md)和 [PR 模板](.github/PULL_REQUEST_TEMPLATE.md)。
- 提交前跑 `git diff --check`。真实配置、数据库、凭据、个人角色和构建产物都不要提交。
- 提交说明写清行为变化、核对了什么、还有什么没确认。
