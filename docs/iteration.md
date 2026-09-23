# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，当前行为见[文档入口](README.md)，已交付记录见[历史入口](history/README.md)。

## 当前批次

- 分支 `feat/s0-product-contract`，本批基线 `a0d07c8`，开始时工作区干净。
- 本轮已本地提交 `a0d07c8`：独立插件接口世代、14 个描述符、面板版本展示及升级说明；源码编译和前端构建完成，真实装载与页面待人工验收，明细见[本日记录](history/iteration-20260923-s2-s3.md)。
- 继续沿已确认宿主所有权方向推进会话段设计；本批仅文档，没有添加尚未接通的类型、表、配置或运行字段。

## 会话段设计与实际核对

[设计与迁移约定](plan/s2-01-conversation-segment.md)区分场景、段、执行与工作。先使用原 scene_sessions.state_json 的可空当前段，保存有序事件／资料范围、摘要引用、稳定编号和必要完整原生交换；不复制原话，不以 traces 代替运行状态，不增加 transcript 表。段更新归 Actor，原提交、观察截点、待处理来源、工作／等待预算保持独立。

核对发现：普通 SocialCognitionCore 每轮新建上下文，未接 exchange_checkpoint；S1 清单没有完整原生响应；TurnReferences.snapshot 混合显示编号和业务资格，不能整份恢复授权；AgentLoop 回调并未覆盖所有成功终结。设计明确这些实际缺口，没有以一个段 ID 宣称持续上下文已实现。

- 源码阅读覆盖 SceneSession、SceneActor、EventStore 的状态保存、SocialCognitionCore、AgentLoop、TurnReferences、ModelProfile、JobStore.save_job_exchange、runtime/work_context 和 history_batches 提交。
- 旧 Session 模型 extra='forbid'：未来段字段写入后，不能声称旧程序无需迁移可直接回退；设计已要求同批备份或另行授权的定向离线转换。本批未写运行状态，因此尚未产生兼容变化。
- 查找时误用路径得到 `rg: src/len_bot/cognition/core.py: IO error for operation on src/len_bot/cognition/core.py: No such file or directory (os error 2)`、`sed: src/len_bot/cognition/work_context.py: No such file or directory`；按实际文件列表转读 social_core.py 和 runtime/work_context.py。属于路径读取失败，不是业务运行失败。
- 本批仅文档，未重新运行编译或前端构建；上一代码提交的构建仍是 491 modules、1.59s。git diff --check 无格式错误。未新增、修改或运行测试、探针、夹具、自动截图、回放或压力任务。
- 没有同版开发面板，未启动服务、读业务库、保存配置、调用模型／平台或发送。本批设计不构成运行验收。

## 接续与未确认

1. S2-01 的固定材料版本依据和完整交换持久表示仍需沿实际渲染补齐，再接 S2-02；先解决普通对话，不让插件表达或工作写主段。压缩交接继续原知识版本和 Actor 互斥。
2. S3-01 已交付待复核；S3-02 仍需按真实能力收窄内部上下文。公开支持／弃用期限未自行决定。
3. S1-02 还缺其他提示及插件定义版本、动态材料与资料页完整定位；S1-04 还缺首次／后续请求分类及部分等待／失败计时。S1-03／05 源码核对已交付，S1-06 与同版页面、请求持久化、缓存覆盖及运行状态待人工验收。
4. 许可证、字体／素材授权、数据保留和公开支持承诺仍待维护者决定。人物资料、五张常服与 19 张表情仍待人工采用；真实图片关联、成果复用、文件交付及纠正后的采用待业务记录。
5. 300k／128k 仍是候选容量，根配置未改；既往诊断令牌轮换与部署条件未复验。授权阶段性本地提交，不授权推送、合并、部署或真实发送。总体工作未全部完成，继续可实施项。
