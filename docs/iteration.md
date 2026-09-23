# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 feat/s0-product-contract，基线 7b999cf，开始时工作区干净；上一阶段已存资料定位续读已本地提交。
- S1-02：上段资料目录虽有可见 R 编号，原逐请求清单只记该消息的角色、类别和长度。段状态下一轮会改写，不能用当前段或整轮最终引用表反推某次请求实际保留了哪些 R。复用原请求位置旁路，只补定位字段，不复制动态正文或建立新表。

## 本批交付与核对

在 saved_result_locators 消息保留仅本次装配所需的私有目录文字及 R→资料 ID 对照；最终请求裁剪和合法 Hook 后，只有目录正文完全未变且未省略时，才把可用 R 放入本轮可解析表。进入模型网关前由原 model_messages 把私有旁路移走，并在现有 RequestLocation 中给出 retained、omitted 或 changed_after_declaration。格式 v4 的原调用登记逐消息只记录保留的 R、原工具名、装配时可用性与可用项的 result_id；省略／改变则无编号清单。旧格式不补造，资料正文、调用参数、像素和旧阅读资格不复制。

调用详情页面沿原滚动表显示上段编号与旧资料链接，并明确这是目录装配时位置，不代表正文已读、当前仍有效或模型已收到。旧 v1—v3 页仍按原格式显示，v4 仍可看原固定提示、工具定义和图像块；未改变其他状态投影或权限。该版本提升的是逐请求定位解释范围，不是完整请求还原或 S2 原生交换续接。

- 阅读 Segment 目录构造、AgentLoop 最终 Hook／裁剪、Context.model_messages、ModelGateway.prepare_request_record、原调用详情组件以及工具资料二次读取边界；改动仅在目录声明、原清单结构和现有展示组件。
- uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/cognition/context.py src/len_bot/cognition/request_record.py src/len_bot/cognition/social_core.py 退出 0；原前端目录 npm run build 退出 0，491 个模块、最终 1.71s，日志 /private/tmp/lenbot-request-locator-build.log；git diff --check 无格式错误。构建产物不进 Git。
- 没有获准的同版服务与面板，实际请求记录、真实旧资料续读、列表页面与窄屏仍待人工验收；编译和构建不当作运行通过。未运行测试、夹具、断言探针、自动截图、回放、故障注入、覆盖率、依赖安装、服务或真实模型／平台调用；未读取真实配置／业务库或实发。
- 本批无源码定位、编译构建或实际业务失败原文。

## 待决定与接续

1. S1-02 动态插件定义、其他动态材料和完整请求可还原范围仍缺；S1-04 尚有未登记的等待，S1-03／05／06 的同版人工观察未完成。不以格式 v4 视为完整请求存档。
2. S2 原生交换、固定材料基线和压缩交接仍未完成；额外原生字段及必要回复片段保存边界待维护者确认。本批不新增这些字段。
3. 已选 Linux 容器群报告与文件交付方向、私下报告邮箱为 Git 作者邮箱；精确支持版本与发布承诺、分类保留期限及许可证／素材授权未定。S3／S4／S5 待同版人工验收，S6／S7 现场与外部记录仍需后续授权。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
