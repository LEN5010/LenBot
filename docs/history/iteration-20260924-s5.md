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
