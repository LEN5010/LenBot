# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 feat/s0-product-contract，基线 5241384，开始时工作区干净；上一阶段方法维护槽位等待已本地提交，属于实际进展。
- S1-02：独立插件 result_only 的源码固定通知缺少当次请求组件定位。只复用原消息与 _PromptComponent，动态输出 Schema 仍留在明确缺档范围。

## 本批交付与核对

plugin_interactions 在原 result_only developer 消息上标记 plugin_agent.result_only_notice，修订 1，内容直接引用既有 RESULT_ONLY_NOTICE 常量；不复制另一份提示文本。context.model_messages 在原最终裁剪后将 _prompt_components 交给既有 _RequestLocation，剥离私有字段；ModelGateway 原 request_record 比较最终内容，匹配才在原调用事务内保存固定片段。合法 Hook 修改或移除片段不能将旧声明冒充本次材料。

respond 分支不追加这条通知，也不因此扩大插件发送资格。动态插件指令、return_result 的插件输出 Schema、模型回复和媒体字节不进入新增快照；不替旧调用补造版本，组件变更后由维护者递增修订。无表、协议、配置、预算、工具或行为路径变化。

- 阅读插件 Agent 的 input_mode、output_mode、result_only 通知、prepare_request／Context.model_messages、请求记录对私有组件的处理和网关原调用登记边界。只有固定常量进入快照；该工作不涉及用户尚未确认的原生续接字段保存。
- uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/runtime/plugin_interactions.py 退出 0；git diff --check 无格式错误。无前端改动，未重复构建；无获准同版环境，实际请求记录与页面仍待人工验收。
- 无本批源码定位、编译或实际业务失败原文。未运行测试、夹具、断言探针、自动截图、回放、故障注入、覆盖率、依赖安装、服务或真实模型／平台调用；未读取真实配置／业务库或实发。

## 待决定与接续

1. S1-02 仍有动态定义、其他提示／材料与完整请求可还原范围的缺口，静态片段不能替代它们；S1-04 工作选中前、部分锁与同版体验仍需核对。
2. S2 完整活动段／未登记依赖与 S2-03 压缩交接仍未完成；额外原生字段保存边界、分类保留、许可证／素材授权、精确支持组合及公开承诺待决定，不据本批固定提示推导决定。
3. S6 候选／现场／远端 CI／升级／外部迁移／发布及 S7 独立使用者／作者记录未完成，原 S3／S4／S5 待人工复核项保持。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
