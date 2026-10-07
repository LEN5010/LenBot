# 第三方组件与来源

LenBot 的原创代码采用 AGPL-3.0-only，插件模板和计数示例采用 GPL-3.0-only，见 [NOTICE](NOTICE)。第三方依赖和独立服务保留各自的许可证，不因被 LenBot 使用而改成 AGPL 或 GPL。

## 依赖的许可证清单

构建时会自动收集实际安装的依赖的许可证资料，随发行物一起分发：

| 位置 | 内容 | 怎么查看 |
|---|---|---|
| 面板 | `npm ci` 后实际 `node_modules` 里各包的元数据和许可证原文 | 已部署的面板访问 `/assets/licenses/frontend/index.json`，再按清单里的相对路径查看原文 |
| 宿主镜像 | Python 依赖、面板依赖的许可证资料 | 镜像内 `/usr/share/lenbot/licenses/` |
| 任务镜像 | 项目声明、实际安装的 Python 包、全局 npm 工具和浏览器包的许可证资料 | 镜像内对应的许可证目录 |

说明：

- 清单读取的是各包自己声明的元数据（License-Expression、License、许可证分类和随包文件），不按包名推测。
- 包没有声明许可证或没有许可证文件时，清单会明确记为缺项，不当作公共领域，也不替它补许可证。
- 面板清单包含只在构建时使用、没有打进最终 JavaScript 的包。
- 镜像里的操作系统包、浏览器和其他独立工具有各自的版权说明，这些清单不能覆盖整台机器上的所有软件。宿主镜像里的 Docker 客户端来自 Debian 的 `docker-cli` 包，版权说明保留在 `/usr/share/doc/docker-cli/copyright`。

## 面板随附的资料

- **图标**：使用 `@mdi/js` 7.4.47 的 SVG 图标和 Vuetify 的 `mdi-svg` 图标集，不分发图标字体。`@mdi/js` 的原 LICENSE（Pictogrammers Free License：图标采用 Apache 2.0，代码采用 MIT）随包保留。
- **字体**：样式里只列出字体族名称，使用系统已安装的字体，不下载也不打包字体文件。

## 独立服务

下面这些服务由用户单独安装和运行，LenBot 只通过它们的接口连接：

| 项目 | 用途 | 说明 |
|---|---|---|
| [Pi](https://github.com/earendil-works/pi) | 后台任务容器里的工作进程 | 任务镜像安装 npm 包 `@earendil-works/pi-coding-agent`，许可证资料随任务镜像保留 |
| BrowserSkill | 账号浏览的守护进程、浏览器扩展和文件助手 | 独立部署；源码包只附带[远程文件补丁和配方](deploy/current/browserskill-files.md)，不含完整上游代码 |
| OneBot v11 实现 | 登录 QQ、收发消息 | 由用户自选并单独运行 |

## 设计参考

LenBot 在设计时参考了这些开源项目的思路，在此致谢：

- [MaiBot](https://github.com/Mai-with-u/MaiBot)：群聊参与、表达学习和注意力。
- [AstrBot](https://github.com/AstrBotDevs/AstrBot)：工具循环、插件和管理面板。

这里列的是设计思路上的参考。如果发现仓库里有直接取自这些项目、却没有保留原版权说明的代码，请提 Issue，我们会补上。

## 不随项目分发的内容

用户自己的角色、知识、聊天记录和私人插件不属于本项目，不随源码或发行物分发，也不因本项目的许可证而获得再分发许可。`examples/personas/companion/` 中的示例角色和表情由维护者原创，随项目以 AGPL-3.0-only 分发。

## 获取源码

源码包含 LICENSE、NOTICE、依赖锁文件和构建材料。通过网络向他人提供 LenBot 服务时，按 AGPL-3.0 的要求，需要让用户能获取你实际运行版本的完整对应源码。
