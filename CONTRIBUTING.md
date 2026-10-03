# 参与 LenBot 开发

工程规则以 [AGENTS.md](AGENTS.md) 为准；唯一运行核心在 `src/len_bot/next/`。

只开发插件可直接从[公开插件指南](developer/README.md)和[可复制示例](developer/examples/counter/)开始，无需先了解核心实现。

## 源码职责

`src/len_bot/next/` 按已有业务职责分包，解决运行、面板、离线命令混放在一个目录中的定位成本；不新增服务层、状态或兼容转发模块。

| 位置（相对 `src/len_bot/next/`） | 职责 |
|---|---|
| `host.py`、`launcher.py`、`instance_lock.py` | 唯一宿主装配、薄启动器和实例互斥；入口不变 |
| `config.py`、`configuration/` | 唯一根配置装配、领域配置与字段类型 |
| `runtime/` | 平台与场景运行装配、生命周期、日志和保留策略 |
| `chat/` | 群会话、注意力、上下文、表达、工具分派、安排和回想 |
| `models/`、`platform/` | 模型请求／预算与 OneBot 消息／发送协议 |
| `persona/`、`learning/` | 人工角色资料与表达／黑话／表情／反馈学习 |
| `memory/` | 双记忆后端、召回、抽取与处理状态 |
| `work/` | Pi 生命周期、单次执行、容器通信、任务文件与资料 |
| `browser/` | 账号浏览协议、字节传输与任务浏览器协作 |
| `storage/` | 聊天数据库、schema／codec 与真实存储池；领域查询仍归各自包 |
| `plugins/`、`builtin_plugins/` | 插件运行／安装／目录与随程序分发的内置插件 |
| `plugin.py`、`text_cards.py`、`image_assets.py` | 已供外部插件使用的公共接口，保留原导入路径 |
| `tools/`、`media/` | 网页／技能／MCP 等工具与图像／语音处理；TTS 只预留接口 |
| `panel/app.py`、`panel/routes/` | 面板装配和按业务划分的 HTTP 路由 |
| `panel/setup.py`、`panel/auth.py`、`panel/task_models.py` | 首次配置、登录与任务响应模型 |
| `maintenance/` | 显式离线迁移、导入／导出、归档、重建索引、依赖恢复与存储池维护 |
| `trials/` | 隔离试聊装配、试聊面板和回放适配，不是另一个生产核心 |

包内直接从具体模块导入；`__init__.py` 不集中重导出，不增加旧路径别名。维护命令统一使用 `python -m len_bot.next.maintenance.<模块>`，试聊入口为 `len_bot.next.trials.lab`；部署脚本和操作文档同步使用这些路径。生产入口仍是 `len_bot.next.host`，历史格式工具不依赖旧运行时。

目录之外：`src/len_bot/prompts/` 放提示词，`builtin_skills/` 放任务方法；`web/frontend/` 放 Vue 面板（业务请求在 `src/host/api/`），忽略的构建产物在 `web/static/dist/`。行为回放入口在 `src/len_bot/eval/`，边界与迁移检查在 `tests/`；任务镜像、部署和构建分别在 `docker/next-worker/`、`deploy/`、`scripts/`。

## 修改与记录

- 沿真实消息路径定位问题；新增表、状态或层级前说明现有结构不足。不写兜底、静默降级或未经批准的重试。
- 测试范围遵循 AGENTS.md；构建、实际执行、文件生成与平台送达分开记录。缺陷和变更分别使用[缺陷模板](.github/ISSUE_TEMPLATE/bug.md)、[PR 模板](.github/PULL_REQUEST_TEMPLATE.md)。
- 首页只放概览；操作集中在 [deploy/current](deploy/current/README.md)。移动说明时同步链接，不重复复制长命令。
- 本机设计在 `docs/design/`，未完成项只在 `docs/next.md`，近期观察写 `docs/iteration.md`。已完成内容简述，过时计划与重复操作说明删除；外部安装必需的信息不能只放本机文档。
- `docs/`、个人角色、真实配置、数据库、凭据和构建产物不进 Git。提交、推送、合并、发布与部署分别服从维护者授权。

## 构建与提交

运行依赖以根锁文件为准；`dev` 组仅含边界测试所需工具。按本次修改范围执行：

```sh
uv run --no-sync python -m compileall -q src/len_bot
(cd src/len_bot/web/frontend && npm run build)
git diff --check
git diff --cached --check
```

[CI](.github/workflows/ci.yml) 编译、构建并生成 sdist/wheel，不证明运行或页面交互正确。页面使用按本轮集中测试阶段安排，构建结果与实际操作分开。

独立打包到源码树外的新目录：

```sh
uv run --no-sync python scripts/build_release.py /tmp/lenbot-release
```

该命令在副本中重建面板、核对源码包和 wheel，再从同一 wheel 生成 Linux／macOS 部署包，可能联网取依赖；不替换运行面板、不启动服务，不执行安装、镜像构建、上传或发布。分发边界见 `pyproject.toml` 与 `scripts/build_release.py`，修改时同步所需组件和说明。

完整候选／发布使用 [Release 工作流](.github/workflows/release.yml)，参数、组件版本及发布动作见[发行说明](deploy/releasing.md)。默认手动候选不推镜像或创建 Release；推送版本标签会真正发布预发布版本。
