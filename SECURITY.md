# 安全问题报告

[English](#reporting-security-issues)

发现安全问题时请私下报告，不要开公开 Issue。

## 怎么报告

- 首选：在仓库的 Security 页面点 **Report a vulnerability**，通过 GitHub 私下提交。
- 也可以发邮件到 [1649211052@qq.com](mailto:1649211052@qq.com)。

这是个人维护的项目，暂时不承诺固定的回复或修复时间，收到后会尽快确认。

## 报告里写什么

- 受影响的版本或提交，以及运行方式（部署包、Docker 或源码）。
- 谁通过什么入口（面板、群聊、插件、任务等）做了什么，本来应该怎样，实际发生了什么。
- 复现步骤。不确定的地方请注明。
- 是否导致了真实发送、外部请求或任务执行。

请不要附上密钥、登录令牌、Cookie、完整配置、数据库、聊天记录或媒体文件。面板导出的诊断材料不含正文，但含有业务编号和时间，发送前请检查。确认问题后请停止操作，不要为了收集证据继续访问别人的数据。

## 范围

LenBot 宿主、面板、插件公共接口、部署脚本和镜像配方都在范围内。第三方插件、OneBot 实现、模型服务和其他独立服务的问题，请报告给它们各自的维护者。

插件和宿主运行在同一个进程里，拥有和宿主一样的权限。只安装你信任的插件，这一点不算漏洞。

---

## Reporting security issues

Please report security issues privately rather than in a public issue.

- Preferred: open the repository's Security tab and click **Report a vulnerability** to submit privately through GitHub.
- Or email [1649211052@qq.com](mailto:1649211052@qq.com).

LenBot is maintained by one person, so there is no fixed response time yet; reports are acknowledged as soon as possible.

Include the affected version or commit, how you run it (package, Docker or source), what happened through which entry point (panel, chat, plugin, task), what you expected, and steps to reproduce. Do not send keys, tokens, cookies, full configuration files, databases, chat logs or media. Once you have confirmed an issue, stop rather than accessing other people's data to gather more evidence.

In scope: the LenBot host, panel, public plugin interface, deployment scripts and image recipes. Report problems in third-party plugins, OneBot implementations, model services or other separate services to their maintainers. Plugins run in the host process with the host's permissions; only install plugins you trust, and this by itself is not a vulnerability.
