# 参与 LenBot 开发

工程规则以 [AGENTS.md](AGENTS.md) 为准；唯一运行核心在 `src/len_bot/next/`。

只开发插件可直接从[公开插件指南](developer/README.md)和[可复制示例](developer/examples/counter/)开始，无需先了解核心实现。

## 源码职责

| 位置 | 职责 |
|---|---|
| `src/len_bot/next/host*.py` | 启动、装配与面板 HTTP 接口 |
| `next/chat.py`、`attention.py`、`context.py` | 会话执行、唤醒与历史压缩 |
| `next/chat_context.py`、`chat_tools.py`、`chat_expression.py` | 请求材料、工具发现与分派、表达与场景发送出口 |
| `next/memory*.py`、`tasks*.py`、`worker*.py` | 双记忆后端、任务调度与容器通信 |
| `next/task_files.py`、`task_browser.py`、`task_inputs.py`、`task_materials.py` | 任务文件、账号浏览协作与输入资料 |
| `next/import_*.py`、`migrate*.py`、`archive_*.py`、`transfer_memory.py`、`export_persona_memory_templates.py` | 显式离线维护 |
| `src/len_bot/prompts/`、`builtin_skills/` | 提示词与任务方法 |
| `src/len_bot/web/frontend/` | Vue 面板；`web/static/dist/` 为忽略的构建产物 |
| `src/len_bot/eval/`、`tests/` | 行为回放／边界与迁移检查 |
| `docker/next-worker/`、`deploy/current/`、`scripts/` | 任务镜像、部署、安装与分发构建 |

表中 `next/` 均指 `src/len_bot/next/`。保留命名空间，不为改名搬动模块；历史格式工具不依赖旧运行时。双记忆后端、独立任务和平台边界是当前需求。

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

[CI](.github/workflows/ci.yml) 编译、构建并生成 sdist/wheel，不证明运行或页面交互正确。改前端后按 AGENTS.md 实际操作页面；本次明确跳过的检查应记录为未执行。

独立打包到源码树外的新目录：

```sh
uv run --no-sync python scripts/build_release.py /tmp/lenbot-release
```

该命令在副本中重建面板、核对源码包和 wheel，可能联网取依赖；不替换运行面板、不启动服务，不执行安装、镜像构建、上传或发布。分发边界见 `pyproject.toml` 与 `scripts/build_release.py`，修改时同步所需组件和说明。
