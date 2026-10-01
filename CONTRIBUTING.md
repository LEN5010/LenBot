# 参与 LenBot 开发

工程规则以 [AGENTS.md](AGENTS.md) 为准。核心正在重写，现有核心冻结，只修阻断运行的缺陷。

## 从哪里开始

| 目录／文件 | 职责与修改边界 |
|---|---|
| `src/len_bot/next/` | 当前核心。新业务只在这里按实际职责修改，不向冻结旧业务包加功能 |
| `next/host*.py` | 宿主启动、运行装配和面板 HTTP 接口 |
| `next/chat.py`、`attention.py`、`context.py` | 对话、唤醒节奏、上下文 |
| `next/memory*.py`、`tasks*.py`、`worker*.py` | 记忆、任务调度和容器通信，各用已有入口和业务状态 |
| `next/import_*.py`、`export_*.py`、`migrate*.py`、`archive_*.py`、`transfer_memory.py`、`rollback_messages.py` | 显式离线维护，不从运行宿主自动调用 |
| `src/len_bot/prompts/`、`builtin_skills/` | 提示词与任务方法，和 Python 执行逻辑分开 |
| `src/len_bot/web/frontend/` | Vue 面板源码；`web/static/dist/` 是忽略的构建产物 |
| `src/len_bot/eval/` | 回放评测；开发是否暂缓按当次任务范围，不混入业务运行 |
| `docker/next-worker/` | 当前任务镜像、扩展和浏览器脚本 |
| `deploy/current/`、`scripts/` | 当前部署／维护说明、安装与分发构建工具 |
| `tests/` | 边界与迁移检查；不以覆盖率替代运行证据 |

表中 `next/` 简写均指 `src/len_bot/next/`。现阶段保留这个命名空间，不为命名整齐搬动所有模块。冻结的 `cognition/`、`runtime/`、旧插件和旧配置样例继续原位保留，待全部场景不再依赖旧后台后再清理。

`deploy/linux/` 混有旧部署材料和当前仍使用的宿主 Dockerfile；不要整目录当成当前模板。准确用途见[部署导航](deploy/README.md)。旧 `containers/` 与当前 `docker/next-worker/` 不互换。

## 文档如何维护

- 仓库首页只保留概览和入口；具体操作放在 [deploy/current](deploy/current/README.md)，不在多个入口复制长命令。
- 本机设计以 `docs/roadmap/` 为准，阶段状态只在 M21；`docs/iteration.md` 记当前观察、错误与未确认项，历史记录移到 `docs/archive/`，不改写历史结果。
- `docs/`、个人角色、真实配置、数据库和产物不进 Git；外部使用必需的说明不能只放在本机文档里。
- 移动说明后修正相对链接；旧运行手册和迁移原件保留，不因“过时”直接删除。

## 流程

1. 缺陷用[缺陷模板](.github/ISSUE_TEMPLATE/bug.md)描述：在哪一步出错、错误原文、源码提交。
2. 沿真实的消息路径定位代码，只改这次需要的部分。新增表、状态、层级前先写清它解决的具体问题。
3. 遵守 AGENTS.md 的写代码与测试规则：不写兜底式防御代码；测试只写在外部协议边界、数据迁移、权限、配置校验和记忆后端接口上；其余功能在本机或测试群上机实测。
4. 按 [PR 模板](.github/PULL_REQUEST_TEMPLATE.md) 写清行为变化、核对范围和未确认项。编译、构建和实际运行结果分开写。

## 构建与提交

```sh
# 项目根目录；只编译，不启动服务、不重新安装依赖
uv run --no-sync python -m compileall -q src/len_bot
# 改了前端时构建面板；产物不进 Git
(cd src/len_bot/web/frontend && npm run build)
git diff --check
git diff --cached --check
```

[CI](.github/workflows/ci.yml) 做同样的编译和构建，外加 sdist 与 wheel 打包。CI 通过只说明能编译和构建，不代表运行正确。

构建产物、运行数据、真实配置和凭据不进 Git。提交、推送、合并、发布和部署分别需要维护者授权。

独立生成对应源码与 wheel（目标必须是源码树外的新目录）：

```sh
uv run --no-sync python scripts/build_release.py /tmp/lenbot-release
```

该命令在独立源码副本中重建面板，核对源码包和 wheel，不替换运行中的面板或启动服务；依赖获取可能需要网络。只生成本地文件，不执行安装、镜像构建、上传或发布。当前 wheel 的安装边界在 `pyproject.toml` 声明，分发核对在 `scripts/build_release.py`；修改两者时同时核对实际共享组件和所需说明。
