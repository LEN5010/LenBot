# 参与开发

[English](CONTRIBUTING.en.md)

只编写插件时不需要阅读本文，请从[插件开发](developer/README.md)或[插件模板](https://github.com/lendevs/lenbot-plugin-template)开始。

## 本机开发

需要 [uv](https://docs.astral.sh/uv/) 和 Node.js 22。

```sh
./scripts/install.sh       # uv sync，并构建面板
uv sync --locked           # 需要运行测试时，再安装 dev 组
uv run --no-sync len-bot   # 启动；没有配置时打开首次向导
```

仓库根目录就是本机的实例目录。`lenbot.config.json`、数据库、`state/` 和 `personas/` 都放在这里，并且已经被 `.gitignore` 排除。开发和本机运行使用同一份代码，用 `uv` 直接启动，不需要另外安装。

修改前端后，在 `src/len_bot/web/frontend` 执行 `npm run build`。构建产物 `web/static/dist/` 不提交到 Git。开发前端时也可以使用 `npm run dev`。

从 Git 检出运行 `len-bot` 时，启动器会检查前端内容是否变化；缺少面板或内容变化时先执行 `npm ci` 和 `npm run build`，未变化时直接启动。发布包使用打包时构建好的面板，启动不需要 Node.js。

### 在副本上测试

不希望改动正在使用的数据时，先停止 Bot，再复制一份测试实例。

```sh
uv run --no-sync python -m len_bot.next.maintenance.test_copy . /tmp/lenbot-test --panel-port 8089
cd /tmp/lenbot-test && uv run --project /path/to/LenBot --no-sync len-bot
```

命令只复制配置和配置中引用到的实例文件（数据库、记忆、角色、任务目录等），不复制源码。副本改为模拟发送，不连接 OneBot，面板使用指定的端口。指向原实例内部的绝对路径也会改为指向副本。模型、插件和任务服务在副本中照常执行，模型调用照常消耗 token。

## 源码结构

运行核心位于 `src/len_bot/next/`，入口是 `len_bot.next.host`，`len-bot` 命令通过 `launcher.py` 启动它。整体设计见[架构](developer/architecture.md)。

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
| `plugins/` | 插件运行与安装；业务插件位于独立仓库 |
| `tools/`、`media/` | 网页、技能、MCP 等工具，图片和语音处理 |
| `panel/` | 面板后端和首次配置向导 |
| `maintenance/` | 停机后执行的维护命令，包括数据升级、重建索引、插件依赖、存储池、测试副本 |
| `trials/` | 面板里的隔离试聊 |

其他目录如下。

| 位置 | 内容 |
|---|---|
| `src/len_bot/plugin.py`、`plugin_testing.py`、`text_cards.py`、`image_assets.py` | 提供给插件的公开接口，插件只从这里导入 |
| `src/len_bot/prompts/` | 提示词 |
| `src/len_bot/builtin_skills/` | 任务技能 |
| `src/len_bot/web/frontend/` | Vue 面板 |
| `src/len_bot/eval/` | 表达回放 |
| `tests/` | 测试 |
| `docker/next-worker/` | 任务镜像 |
| `deploy/` | 部署资料。`deploy/updater/` 是部署包和 Docker 共用的更新器 |
| `website/` | 文档站（VitePress），用 `cd website && npm ci && npm run dev` 在本地预览 |
| `changelogs/` | 每个版本一份版本说明 |
| `scripts/` | 安装和打包脚本 |
| `examples/` | 示例角色和公开的回放用例 |

维护命令统一用 `python -m len_bot.next.maintenance.<模块>` 调用。包内代码直接从具体的模块导入，`__init__.py` 不集中重新导出。

## 写代码

- 只解决需要解决的问题。不吞掉异常并返回默认值，不猜测字段，不为类型已经排除的情况增加检查。模型或服务调用失败时，不自动更换模型或服务，也不修改参数重试。
- 外部数据（平台消息、模型和服务的响应、配置文件）在入口处解析一次。解析失败时直接报错，错误信息中带上原始片段。
- 异常只在以下边界捕获，即一轮对话、一次工具或插件调用、一个任务。记录原文后结束这一轮。工具的错误原文交还给模型。
- 请求的归属、指代以及谁在回应谁，这类语境判断交给模型。宿主只保存真实身份和执行所需的状态。新增数据表、状态或层级时，请在 PR 中写清它解决的问题。
- 日志使用模块级的 `logging.getLogger(__name__)`，事件使用 `runtime/logs.py` 中的 `log_event(logger, '事件名', **字段)`，不使用 `print`。异常传给 `error=`，数据库的错误列保存一行 `Type: message`（`error_text`）。新的长流程入口（新的后台任务、新的外部回调）用 `log_context` 绑定能关联起各条记录的 ID。
- 运行参数只来自根目录的 `lenbot.config.json`，运行期间由面板保存。不增加环境变量、dotenv、命令行参数或数据库中的覆盖项。

## 测试

```sh
uv run --no-sync pytest -q
uv run --no-sync python -m compileall -q src/len_bot
uv run --no-sync ruff check src/len_bot scripts deploy/updater deploy/package tests
```

测试只覆盖以下内容，样本使用脱敏后的真实数据。

- 外部协议边界，包括 OneBot、Pi RPC，以及模型服务和记忆服务的响应解析
- 数据迁移
- 权限
- 配置校验

不 mock 调用过程，不测试私有函数，不对提示词做快照。

修改提示词或角色表达时，用[表达回放](examples/replay/)对比改动前后的回复。发现不好的回复时，先把情境写成回放用例，再修改提示词，不在提示词中堆积禁令。通用的情境写进 `examples/replay/`，来自真实群聊的用例只保留在本机，不提交。

## 数据格式

业务数据库从公开基线 v1 开始。修改表结构时，按以下步骤操作。

1. 在 `storage/schema.py` 中直接建立新的结构。
2. 在 `maintenance/migrate.py` 的 `BUSINESS` 中增加一步从上一版的升级。
3. 提高 `storage/store.py` 中的 `FORMAT_VERSION`。

记忆处理库（`migrate_memory_jobs.py`）、本地记忆索引（`migrate_local_memory.py`）和根配置的做法相同，各自的编号见[发行指南](deploy/releasing.md#兼容编号)。升级只在停机时由维护命令执行，运行时的代码不写新旧格式的兼容分支。

三个数据库的升级都由 `maintenance/migrations.py` 执行。

- 升级前后各做一次完整性检查。
- 每一步使用一个事务，并在同一个事务中修改 `user_version`。
- 删除文件这类数据库以外的操作由步骤返回，等这一步提交后再执行。
- 单独运行迁移命令时，在数据库旁边保留一份输入格式的副本 `<文件>.v<格式>.bak`。`upgrade apply` 已经有完整的快照，不再另外保存。

存入数据库的 JSON 正文按 dataclass 严格解码（`ChatMessage`、`Sender`、`Segment`、`Task`、`TaskFile`、`Schedule`，以及模型调用和记忆任务中的记录）。为这些类型增加、删除或修改字段，就等于修改了数据格式。这时需要同时提高格式编号，并在升级步骤中改写已有的正文。

`tests/fixtures/history/` 存放由旧格式代码写出的小型实例。`tests/test_history_migrations.py` 把它们升级到当前格式，要求表结构与全新建立的数据库一致，并且所有正文都能解码。

提高任何一个格式编号之前，先在 `scripts/build_history_fixtures.py` 中登记改动前的最后一个提交，再运行这个脚本生成新的样本（`uv run --no-sync python scripts/build_history_fixtures.py`）。同一输入重新生成的文件逐字节相同。

## 构建与发布

```sh
uv run --no-sync python scripts/build_release.py /tmp/lenbot-release
```

这条命令在副本中重新构建面板，生成源码包、wheel、Linux／macOS／Windows 部署包和发行清单，不启动程序，也不上传。正式发布使用 [Release 工作流](.github/workflows/release.yml)，步骤见[发行指南](deploy/releasing.md)。

[CI](.github/workflows/ci.yml) 会编译代码并运行测试，然后构建面板并打包。测试在 Python 3.13 和 3.14 上各运行一遍（[check.yml](.github/workflows/check.yml)）。3.13 是 `pyproject.toml` 中的最低版本，3.14 用来提前发现新解释器上的问题。同一个工作流还用 actionlint 检查所有 workflow 文件。单元测试只在 Linux 上运行，Windows 和 macOS 只有安装冒烟测试覆盖。CI 通过不代表页面交互和真实聊天也没有问题。

## 提交

- 问题和改动分别用[缺陷模板](.github/ISSUE_TEMPLATE/bug.md)和 [PR 模板](.github/PULL_REQUEST_TEMPLATE.md)。
- 提交前运行 `git diff --check`。以下内容都不要提交。
  - 真实的配置和数据库
  - 凭据
  - 个人角色
  - 构建产物
- 提交说明中写清行为的变化、核对过的内容和尚未确认的事项。
- 行为或接口有变化时，同步更新 README、文档站 `website/`、`developer/` 或 `deploy/` 中对应的说明。中文是主版本，有英文版的文件（README、CONTRIBUTING、部署概览、插件接口）需要一起修改。修改了 `website/` 后，在其中运行一次 `npm run build`，有失效链接时构建会失败。
- 作者只写人。提交信息和 PR 中不加入工具或模型的署名，例如指向机器身份的 `Co-Authored-By:` 或 `Generated with …`。

## 许可证

提交到本仓库的代码按 [AGPL-3.0-only](LICENSE) 授权，插件模板按 GPL-3.0-only 授权。第三方材料的来源和许可写在 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md) 中，引入新的第三方代码或素材时请一并更新。
