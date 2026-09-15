# 工作空间与浏览器边界

本文按 `2c862c6` 的工具入口说明当前实现，部署/启用操作见 [运行手册](operations.md)，后续目标见 [完整计划](LenBot_社会Agent_完整实施计划_7a4152d.md)。具体环境是否已运行只看 [当前任务](iteration.md)，不从历史授权或样例推断当前开放状态。

## 当前 Python 路径

现行插件 ID 为 `workspace`，样例默认停用；旧 `python_workspace` 包保留兼容代码，不作为新配置入口。工具为 run_python、list_workspace_files、read_workspace_file 和 export_workspace_artifact。

WorkspaceService 从已有 work 的 scene、真实请求者和 job 取得工作区归属，不接受模型自填 owner。当前入口仍要求人类请求者，类型中出现 system/plugin 不表示它们已能使用该执行路径。

workspace 插件按根配置选择唯一执行后端：`worker` 仍由 LenBot 进程调用 Docker/Podman 兼容运行时；`gateway` 则经 `GatewayWorkspaceService` 把 `run_python` 交给独立 Worker Gateway，宿主不再启动容器。两者互斥，网关失败不会回落宿主 `docker run`。样例配置保持 `worker` 且 `gateway: null`；真实环境是否已切换只看根配置和 [当前任务](iteration.md)。

worker 路径的容器使用无网络、只读根、非 root、cap drop、no-new-privileges、pids/内存/CPU 和临时目录限制；未显式指定 container_user 时跟随启动进程 UID/GID，具体映射须由部署确认。宿主控制脚本与输入只读挂载，工作目录保存产物。同一工作的输入导出与执行串行；失败不回落宿主 Python 或任意 shell。

取消或超时按唯一容器名尝试 kill、回收客户端、移除并核对，返回 confirmed_stopped、confirmed_absent 或 unconfirmed。未确认记录阻止后续复用，不能把取消请求本身视作停止成功。外层工具时限覆盖内层执行及清理；取消继续向 Agent 传播，不改成普通工具错误后继续工作。

目录字节数和文件数在执行中轮询，退出后再核对；这是应用层限制，不是底层文件系统硬配额。超限产物保留元信息和 over_limit。宿主文件读取通过固定工作区描述符逐级 O_NOFOLLOW 打开，以 O_NONBLOCK 避免特殊文件阻塞，并对同一 fd 做 fstat；文本按范围读取，二进制读取受字节上限约束。图片导出须成功登记媒体才成为附件，普通文件沿已授权工作面板下载，均不自动发群。

工作恢复的累计 token、原上限和绝对期限已按 FX01—FX04 接入源码并在复审后修正回归（原合同见 [归档 fix 文档](archive/social-agent-c01-c10-fix.md)，实施与核对状态只看 [当前任务](iteration.md)）；容器有独立单次时限不代表整个工作恢复合同已经真实运行核对。

## 当前浏览器路径

`browser_agent` 样例默认停用，工具只在已有 work 下使用。当前 worker 在 LenBot 进程内运行 Playwright；Chromium sandbox 已启用，页面使用独立 BrowserContext 并禁用 Service Worker，但这不等于浏览器已具备独立 OS/网络隔离。

HTTP(S) 主机按配置白名单检查；`*` 表示允许任意公网主机，代码仍检查回环、私网、链路本地、保留和未指定地址。应用层 DNS/路由检查不能替代部署出口隔离。缺少 Playwright 或页面被网络规则拒绝时保留错误，不能推定完整网络边界已验收。

临时 page_ref 按工作作用域隔离，snapshot_revision 用于当前元素引用。DOM 返回的是文字覆盖；当前 snapshot 支持 text_offset/text_limit，但重新读取仍会取得页面快照，不能当作已实现持久、不可变快照续读。截图登记为场景媒体后返回 asset_registered=true、pixels_loaded=false，只有后续请求确实装入图片才计为像素已读。

交互默认关闭；启用后支持滚动及当前元素引用点击。工具没有提供专用登录凭据或点赞/收藏等账号动作入口，但通用 click 本身不是对任意网站副作用的完整识别与拦截。独立 worker、出口和账号动作边界尚未完成前，不以功能使用记录代替生产隔离验收。

浏览器工具返回观察与资产引用，不直接发消息；同进程插件代码的宿主可达性不能仅靠接口说明排除。结果表达仍经原提案、Gate、ActionQueue 和真实回执。

## 可选的 Gateway 后端

`run_python` 已能经 `GatewayWorkspaceService` 提交 `ExecutionRequest`：宿主先写自己的 `execution_runs` 行，再把脚本文本与 `input_files` 交给网关；超时或结果未知只查询同一执行 ID。列表、分页读取、导出和面板下载走网关产物 API。网关不按资产 ID 自行拉取资料。

源码接线不等于生产已切换。样例和未改根配置的环境仍走宿主 worker。不能宣称 LenBot 已移除容器运行时访问、两库已同步，或隔离已通过实机验收。正式放行仍需运营者改选 gateway、部署网关服务与镜像，并补离线证据；输入/媒体导入等后续功能不因此提前开放。

Gateway 源码已按 FX06—FX10 及其外部复审修订：状态机含 `termination_unconfirmed` 复核释放、启动确认与未确认启动的监视、产物先复制后登记与逐层 `O_NOFOLLOW` 打开、HTTP 分类、worker GID 权限失败记录、非 ASCII 下载文件名（原合同见 [归档 fix 文档](archive/social-agent-c01-c10-fix.md)）。
