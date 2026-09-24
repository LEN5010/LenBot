# 2026-09-24 首选安装方向与文件协议边界

任务状态只见[产品路线](../plan/README.md)，当前交接见[当前任务](../iteration.md)。本页仅归档对应批次的源码阅读与文档结果，不是现场安装或上传记录。

阶段提交：`5145cf1`。

## 批次范围与交付

分支 `feat/s0-product-contract`，基线 `8a52054`。S5-01／S5-06 已选择 Linux 容器群报告与文件交付，但原安装材料没有直说报告渲染所在进程与持久文件所需后端；原 OneBot 配置标签容易被误读为两个网络动作。本批只改文档和标签旁说明性注释，不改适配器分支、动作、配置、Schema、存储或发送行为。

- [Linux 部署前置](../../deploy/linux/README.md#首个验收方向的报告与文件)分开报告与文件：`group_summary` 在 LenBot 主进程渲染，worker 字体不能替代主进程可读字体；运营者须按素材使用边界提供挂载内字体及 UID 10000 的读取权限。可下载文件另需 Gateway 后端和不可变产物，经 `file_asset` 登记后按原授权、审查及 SnowLuma 文件配置上传。报告图片或工作产物均不等于上传回执。
- [现场记录模板](../../deploy/linux/release-evidence.template.md)补主进程字体、Gateway／worker 和分链回执位置；[架构](../architecture.md#onebot-文件上传协议)、[支持范围](../support.md#当前入口矩阵)和[运行手册](../operations.md#配置-onebot-文件上传)统一原配置标签含义。
- 阅读 `group_summary` 装载／渲染路径、主镜像与 worker 镜像、`file_delivery_facts`、`prepare_workspace_file`、OneBot `_action_payload` 和 `upload_response`。NapCat `upload_group_file_data_file_id` 与 SnowLuma `upload_group_file` 是配置／回执标签；两者实际出站动作均为 `upload_group_file`，成功仍须真实 `data.file_id`。[NapCat](https://napcat.apifox.cn/226658753e0)与[SnowLuma](https://snowluma.github.io/zh/docs/api/group-file/upload_group_file)公开文档列出动作，但不证明本机响应。

`git diff --check` 退出 0；未进行新的编译、构建或业务运行，之前的双架构镜像构建未计为本批通过。未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；未读取真实根配置／业务库、启动服务、调用模型／平台或实发。没有本批实际业务失败原文。

## 未确认

字体／模板素材的使用和再分发边界待维护者决定；本批未复制或改授素材。目标两架构主机、实际字体文件、Gateway、SnowLuma 只读挂载、真实报告／文件回执均待获准人工观察；模型／部署组合、数据期限、许可证与公开承诺仍未确定。S2 完整活动段、S6 候选与 S7 外部闭环亦未完成。仅阶段性本地提交，不推送、合并、部署或实发。

## 文件上传配置标签与界面动作

阶段提交：`6ce0b77`。分支 `feat/s0-product-contract`，基线 `ae97090`。原设置页只读字段和能力卡把 NapCat／SnowLuma 的内部 `protocol` 标签称为“协议”，容易把复合标签误认为另一个网络动作。当前出站代码对两种实现均只调用 `upload_group_file`；本批只改能力卡原部署列表、系统设置／连接页与本群文件前置文案，分别显示配置／回执标签、实际动作及平台回执，不改配置值、上传动作、资格或状态。

同步[运行手册](../operations.md#配置-onebot-文件上传)与路线 S5-02 证据。首次从前端目录执行 Python 编译时误用仓库相对路径，原输出 `Can't list 'src/len_bot/web/capability_status.py'`；尽管命令返回 0，该次没有编译目标文件。随后从仓库根目录正确编译退出 0；前端 `npm run build` 在最终文案后退出 0，末次 1.64 秒；`git diff --check` 退出 0。构建产物不进 Git，未获准打开同版页面或调用平台，实际显示、保存与回执待人工验收。未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；无业务运行失败原文。

维护者同批确认 S2 宿主可在当前段保存原事件／已存观察无法还原的必要原生 assistant 工具调用片段及工具回执已有资料引用，不改变数据保留期限；这是后续 S2 设计与实施输入，不代表本批界面改动已经实现该能力。

## 文件上传现场版本改为可选记录

分支同上，基线 `ab724c7`。维护者已明确 SnowLuma 按 OneBot 协议而非具体发行版本判读；原 `FileUploadConfig.version`、设置页开关及连接页提示仍要求精确版本才能标部署核验，与该方向不一致。本批沿现有配置字段将 `version` 改为可空、可选的现场记录，保留旧值读取；部署核验改为明确核对实现、`upload_group_file` 动作和资产目录只读挂载。版本编辑不再重置该开关；换实现仍需重新人工核对。连接页继续只读展示平台报告，但不比较版本作为准入。原实现／标签匹配、授权、文件审查、`data.file_id` 回执与 unknown 不重传边界均不变。

同步[配置步骤](../operations.md#配置-onebot-文件上传)、[支持矩阵](../support.md#当前入口矩阵)、[Linux 前置](../../deploy/linux/README.md#onebot-文件与可选项)与发布模板。前端 `npm run build` 退出 0，491 个模块、1.72 秒；从前端目录误执行仓库相对 Python 编译路径，命令虽退出 0 但原输出三行 `Can't list 'src/len_bot/…'`，**不计为编译**；随后在仓库根目录正确编译三个改动模块退出 0，`git diff --check` 退出 0。均只是静态／构建结果，不是页面、配置保存、协议或上传验收。未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；未读取真实根配置／业务库、启动服务、调用模型／平台或实发。没有本批业务运行失败原文。

## SnowLuma 公开响应示例的歧义

基线 `ec159b7`。只读核对 [SnowLuma 上传群文件文档](https://snowluma.github.io/zh/docs/api/group-file/upload_group_file)：页面列出 `upload_group_file`、`group_id/file/name`，响应形状标 `{ file_id: string }`，但同页成功示例写 `data: null`。这两处不能同时证明真实连接会返回 `file_id`。原适配器保持严格确认：无真实 `data.file_id` 时结果为 unknown，不把 `status=ok` 单独解释为文件已到群，也不自动补传。本批只在支持页与原发布模板标出歧义和现场需记录的脱敏响应，不改上传器或真实配置。

`git diff --check` 退出 0；没有本批业务失败原文。没有访问本机 SnowLuma、上传文件、启动服务或执行仓库禁止的验证任务。双架构目标、实际报告与文件回执仍待获准正常操作；如现场响应持续缺少身份，需基于真实协议另行决定，不预设替代回执。
