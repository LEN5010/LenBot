# 参与 LenBot 开发

工程规则以 [AGENTS.md](AGENTS.md) 为准。核心正在重写，现有核心冻结，只修阻断运行的缺陷。

## 流程

1. 缺陷用[缺陷模板](.github/ISSUE_TEMPLATE/bug.md)描述：在哪一步出错、错误原文、源码提交。
2. 沿真实的消息路径定位代码，只改这次需要的部分。新增表、状态、层级前先写清它解决的具体问题。
3. 遵守 AGENTS.md 的写代码与测试规则：不写兜底式防御代码；测试只写在外部协议边界、数据迁移、权限、配置校验和记忆后端接口上；其余功能在本机或测试群上机实测。
4. 按 [PR 模板](.github/PULL_REQUEST_TEMPLATE.md) 写清行为变化、核对范围和未确认项。编译、构建和实际运行结果分开写。

## 提交前

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
