# 2026-09-23 会话段与插件合同记录

当前状态只见[产品路线](../plan/README.md)，当前交接见[当前任务](../iteration.md)。以下过程与未确认项限定原批次。

## 独立插件接口世代

阶段提交：`a0d07c8`。

### 当前批次

- 分支 `feat/s0-product-contract`，本批基线 `cf3f88d`，开始时工作区干净。
- 用户已确认宿主管理会话段、原话归事件存储；插件接口世代与自身版本分离，身份／范围／预算归宿主。决定见[原记录](plan/s0-02-product-positioning-and-decisions.md)。
- 本批实施独立插件接口世代，不修改真实配置或业务数据，不更改插件自身版本及原工作／出站版本核对。

### 本批实现与核对

现有 version 只能标明插件自身版本，不能判断宿主 API 合同。本批在原 PluginSpec 增加必填 api_version，以整数 1 声明接口世代；原目录发现取得描述符后明确拒绝类型或世代不匹配。缺少字段在描述符构造时失败。核对发生在 Python 包导入之后、插件配置解析及实例创建之前，不是恶意代码隔离。没有新表、重复描述符、配置来源或兼容层。

13 个内置插件与业务时钟均补字面值声明；Host 原状态投影新增 api_version，查询层继续原脱敏路径，插件列表及详情分开显示自身版本和接口世代。所属开发、架构、升级／回退说明同步；公共入口未增删，内部耦合收窄留待后续，稳定支持期未自行决定。

- uv compileall 编译 catalog、host、13 个内置描述符及 local_clock，退出 0。
- 前端 npm run build 退出 0，491 modules，1.59s；日志 `/private/tmp/lenbot-s3-api-build.log`。git diff --check 无格式错误。
- 源码沿 ConfigStore.load → PluginCatalog.discover → RootConfig.model_validate 核对，沿 Host.status_snapshot → query_service.plugins → PluginsView 核对字段；没有执行插件导入来制造运行验收。
- 读取时猜测 `src/len_bot/plugins/spec.py` 得到 `No such file or directory`，实际类型在 catalog.py；两个不存在的文档 glob 分别得到 `zsh:1: no matches found: docs/plan/s3*`、`zsh:1: no matches found: docs/*产品*`，之后读取真实任务卡与盘点。均为路径查找失败，不是业务运行失败。
- 没有同版开发面板，实际页面、外部插件迁移、装载拒绝和正常插件生命周期均待人工验收，不把编译／构建视为运行通过。
- 未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入或压力任务；未启动服务、读业务库、保存配置、调用模型／平台或发送。提交不含构建产物和运行数据。

### 接续与未确认

1. 继续 S2-01：先沿 scene_sessions、执行 episode、历史压缩与请求装配确认最小持久会话段边界，再实施；不复制整份 transcript，不改变数据保留政策。
2. S3-02 的公共上下文收窄仍须沿实际调用能力拆除内部依赖，不把本批世代字段当作该项已完成。
3. S1-02 仍缺其他提示及插件定义版本、动态材料与工具资料页完整定位；S1-04 仍缺首次／后续请求分类、未记录的入场等待与失败阶段。S1-03／S1-05 源码核对和定向展示已交付；S1-06 与其他同版页面、请求持久化、缓存覆盖和调用状态仍待人工验收。
4. 许可证、字体／素材授权、数据保留、对外支持承诺仍待维护者决定。人物资料、五张常服及 19 张表情仍待人工采用；真实图片关联、成果复用、文件交付和纠正后的采用待真实业务记录。
5. 300k／128k 仍是候选容量，未改根配置；既往诊断令牌轮换与部署条件未复验。仅授权阶段性本地提交，未推送、合并、部署或实发。

## 会话段最小状态设计

阶段提交：`81fda02`。

### 当前批次

- 分支 `feat/s0-product-contract`，本批基线 `a0d07c8`，开始时工作区干净。
- 本轮已本地提交 `a0d07c8`：独立插件接口世代、14 个描述符、面板版本展示及升级说明；源码编译和前端构建完成，真实装载与页面待人工验收，明细见[本日记录](history/iteration-20260923-s2-s3.md)。
- 继续沿已确认宿主所有权方向推进会话段设计；本批仅文档，没有添加尚未接通的类型、表、配置或运行字段。

### 会话段设计与实际核对

[设计与迁移约定](plan/s2-01-conversation-segment.md)区分场景、段、执行与工作。先使用原 scene_sessions.state_json 的可空当前段，保存有序事件／资料范围、摘要引用、稳定编号和必要完整原生交换；不复制原话，不以 traces 代替运行状态，不增加 transcript 表。段更新归 Actor，原提交、观察截点、待处理来源、工作／等待预算保持独立。

核对发现：普通 SocialCognitionCore 每轮新建上下文，未接 exchange_checkpoint；S1 清单没有完整原生响应；TurnReferences.snapshot 混合显示编号和业务资格，不能整份恢复授权；AgentLoop 回调并未覆盖所有成功终结。设计明确这些实际缺口，没有以一个段 ID 宣称持续上下文已实现。

- 源码阅读覆盖 SceneSession、SceneActor、EventStore 的状态保存、SocialCognitionCore、AgentLoop、TurnReferences、ModelProfile、JobStore.save_job_exchange、runtime/work_context 和 history_batches 提交。
- 旧 Session 模型 extra='forbid'：未来段字段写入后，不能声称旧程序无需迁移可直接回退；设计已要求同批备份或另行授权的定向离线转换。本批未写运行状态，因此尚未产生兼容变化。
- 查找时误用路径得到 `rg: src/len_bot/cognition/core.py: IO error for operation on src/len_bot/cognition/core.py: No such file or directory (os error 2)`、`sed: src/len_bot/cognition/work_context.py: No such file or directory`；按实际文件列表转读 social_core.py 和 runtime/work_context.py。属于路径读取失败，不是业务运行失败。
- 本批仅文档，未重新运行编译或前端构建；上一代码提交的构建仍是 491 modules、1.59s。git diff --check 无格式错误。未新增、修改或运行测试、探针、夹具、自动截图、回放或压力任务。
- 没有同版开发面板，未启动服务、读业务库、保存配置、调用模型／平台或发送。本批设计不构成运行验收。

### 接续与未确认

1. S2-01 的固定材料版本依据和完整交换持久表示仍需沿实际渲染补齐，再接 S2-02；先解决普通对话，不让插件表达或工作写主段。压缩交接继续原知识版本和 Actor 互斥。
2. S3-01 已交付待复核；S3-02 仍需按真实能力收窄内部上下文。公开支持／弃用期限未自行决定。
3. S1-02 还缺其他提示及插件定义版本、动态材料与资料页完整定位；S1-04 还缺首次／后续请求分类及部分等待／失败计时。S1-03／05 源码核对已交付，S1-06 与同版页面、请求持久化、缓存覆盖及运行状态待人工验收。
4. 许可证、字体／素材授权、数据保留和公开支持承诺仍待维护者决定。人物资料、五张常服与 19 张表情仍待人工采用；真实图片关联、成果复用、文件交付及纠正后的采用待业务记录。
5. 300k／128k 仍是候选容量，根配置未改；既往诊断令牌轮换与部署条件未复验。授权阶段性本地提交，不授权推送、合并、部署或真实发送。总体工作未全部完成，继续可实施项。
