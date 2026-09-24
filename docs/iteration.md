# 当前任务

更新于 2026-09-24。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 `feat/s0-product-contract`，基线 `8a52054`；上批会话段已保存最终请求实际保留的历史摘要批次引用，仍为 `source_window_only`，其过程已归[历史记录](history/iteration-20260923-s2-s3.md#会话段历史摘要批次引用)。
- S5-01／S5-06：首个验收方向虽已选为 Linux 容器群报告与文件交付，但安装材料没有直说报告渲染所在进程和文件持久登记所需执行后端；OneBot 原配置标签也易被误读成两种网络动作。本批沿现有配置、适配器和部署材料补足操作边界，不改运行行为或真实配置。

## 本批交付与核对

- [Linux 部署前置](../deploy/linux/README.md#首个验收方向的报告与文件)把两条链分开：`group_summary` 在 LenBot 主进程渲染，worker 字体不能替代主进程可读字体；运营者须在已挂载路径提供经核对的字体并为 UID 10000 留读取权限。可下载文件须选 Gateway 后端、保留不可变产物、登记 `file_asset`，再按原授权和审查经 SnowLuma 上传；报告图片或工作产物均不等于文件上传回执。对应字段已加入[现场记录模板](../deploy/linux/release-evidence.template.md)。
- 阅读 `group_summary` 装载／渲染路径、主镜像与 worker 镜像、`file_delivery_facts` 与 `prepare_workspace_file`、OneBot `_action_payload` 和 `upload_response`：NapCat 的 `upload_group_file_data_file_id` 与 SnowLuma 的 `upload_group_file` 是原配置／回执标签；实际出站动作对两者均是 `upload_group_file`，只有真实 `data.file_id` 才确认为上传。对照 [NapCat 动作文档](https://napcat.apifox.cn/226658753e0)和 [SnowLuma 动作文档](https://snowluma.github.io/zh/docs/api/group-file/upload_group_file)的动作名称；文档存在不证明本机响应。同步[架构](architecture.md#onebot-文件上传协议)、[支持范围](support.md#当前入口矩阵)和[运行手册](operations.md#配置-onebot-文件上传)。
- 本批只改文档和协议标签旁的说明性注释，不改适配器分支、动作、配置、Schema、存储或发送行为。`git diff --check` 退出 0；未进行新的编译、构建或业务运行，沿用先前双架构构建记录，不将其计为本批通过。
- 未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；未读取真实根配置／业务库、启动服务、调用模型／平台或实发。没有本批实际业务失败原文。

## 待决定与接续

1. 字体／模板素材的使用和再分发边界仍待维护者决定；本批不复制或改授素材。首次组合的实际字体文件、目标两架构主机、Gateway 与 SnowLuma 只读挂载及上传回执均待获准人工观察，不把本机交叉构建或公开协议文档记为已验收。
2. S2 跨轮原生交换及压缩交接仍缺；回复片段持久边界待维护者答复。S1／S3／S4 同版人工核对，S5 数据期限、许可证、公开承诺、S6 候选与 S7 外部闭环均未完成。
3. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
