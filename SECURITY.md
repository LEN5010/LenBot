# 安全问题报告

[English](#reporting-security-issues)

发现安全问题时，请私下报告，不要提交公开的 Issue。

## 报告方式

- 首选方式是在仓库的 Security 页面点击 **Report a vulnerability**，通过 GitHub 私下提交。
- 也可以发送邮件到 [1649211052@qq.com](mailto:1649211052@qq.com)。

这是个人维护的项目，目前不承诺固定的回复时间或修复时间，收到报告后会尽快确认。

## 报告内容

- 受影响的版本或提交，以及运行方式（部署包、Docker 或源码）。
- 问题经过。请写明谁通过什么入口（面板、群聊、插件、任务等）做了什么操作，预期的结果是什么，实际发生了什么。
- 复现步骤。不确定的地方请注明。
- 问题是否导致了真实发送、外部请求或任务执行。

请不要附上以下内容。

- 密钥、登录令牌和 Cookie
- 完整的配置文件和数据库
- 聊天记录和媒体文件

面板导出的诊断材料不包含消息正文，但包含业务编号和时间，发送前请检查。确认问题后请停止操作，不要为了收集证据继续访问他人的数据。

## 范围

以下部分在范围内。

- LenBot 宿主
- 面板
- 内置插件
- 部署脚本和镜像配置

第三方插件和 OneBot 实现的问题，以及模型服务和其他独立服务的问题，请报告给它们各自的维护者。

插件和宿主运行在同一个进程中，拥有和宿主相同的权限。因此只应安装可信的插件，这一点本身不算漏洞。

---

## Reporting security issues

Please report security issues privately, not in a public issue.

- Preferred: open the repository's Security tab and click **Report a vulnerability** to submit privately through GitHub.
- Or email [1649211052@qq.com](mailto:1649211052@qq.com).

LenBot is maintained by one person, so there is no fixed response time yet. Reports are acknowledged as soon as possible.

A report should include:

- the affected version or commit, and how LenBot is run (package, Docker or source);
- what happened, through which entry point (panel, chat, plugin, task), and what was expected;
- steps to reproduce.

Do not send keys, tokens, cookies, full configuration files, databases, chat logs or media. Once an issue is confirmed, stop. Do not access other people's data to gather more evidence.

In scope: the LenBot host, the panel, built-in plugins, deployment scripts and image recipes. Problems in third-party plugins, OneBot implementations, model services or other separate services belong with their own maintainers. Plugins run in the host process with the host's permissions, so only trusted plugins should be installed. That by itself is not a vulnerability.
