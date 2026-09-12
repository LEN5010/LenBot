# 工作空间与浏览器边界

`workspace` 与 `browser_agent` 都默认停用，并且不能替代 LenBot 的 AgentLoop。旧 `python_workspace` 导入路径仅保留兼容代码，不在示例配置中启用。

Python 工具只接受已有 work 的宿主归属，不接受模型自由填写 owner。`run_python`、`list_workspace_files`、`read_workspace_file` 和 `export_workspace_artifact` 使用显式配置的 Docker/Podman 兼容运行时、无网络、只读根文件系统、非 root 用户、降权 capability、进程、内存、CPU、临时目录和产物大小上限；运行时不可用时返回 `unsupported`，不会回退到宿主 Python 或任意 shell。工作文件保留在插件数据目录下，读取必须带工作 ID 和相对路径；控制面板通过同一工作归属读取普通文件，图片仍使用现有媒体资产链。

浏览器工具只访问配置的 HTTP(S) 主机白名单，并以 work 的临时 page_ref 与 snapshot_revision 关联页面和元素引用。DOM 观察和截图分别声明覆盖范围，截图才表示读取像素。交互默认关闭；启用后仍只提供显式的滚动或当前观察版本的元素点击，不提供登录凭据、下载后执行、购买、发帖、删除等业务写操作。Playwright 未安装或页面不满足白名单时返回受控错误。

两个插件都没有 Bot 配置、SQLite、OneBot 或发送能力；要把结果发到群里仍须经过原 Agent 提案和发送链。
