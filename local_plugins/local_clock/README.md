# 业务时钟

本地业务插件，读取 LenBot 的实际运行时时钟和根配置的业务时区。实现只在本目录；发现、模型、资料与发送通过[公共插件接口](../../docs/plugins.md)。依赖仅为项目已有的 Pydantic 和 Python 标准库。

描述符明确声明 `api_version=2`，与插件自身 `version='1.0.0'` 分开。宿主只接收匹配的接口世代；省略字段或声明其他世代会终止发现，即使插件停用也不跳过。接口迁移不修改真实配置，也不表示已获准开放本插件。

| 入口 | 行为 |
|---|---|
| `local_time_now` | 供 conversation/work 读取日期、星期、时间和时区，不发送 |
| 精确消息“现在几点” | 消费命令，调用同一读取工具，直接提交一条时间文字，不调用模型 |
| 精确消息“时间简报” | 消费命令，读取真实时钟资料，由配置的既有模型路由调用 respond 组织一条说明 |

根配置的 `plugin_directories` 包含 `local_plugins`，`plugins.local_clock` 保存本插件的 enabled/config。全局参数包括工具超时、简报使用的 model_role，以及模型/工具次数、上下文和输出额度。time_now 不使用这些模型额度。

目标群的 `plugins.local_clock` 需要 enabled/config；config.commands 可填写 `time_now`、`time_brief`。没有开放的命令不被本插件消费。业务时区沿根 time 配置，时区改变后正常重启；本插件不从系统环境猜一个时区，不请求网络、不建立定时轮询。

`local_time_now` 明确声明 `ordered=False`：每次只读取当前时钟，没有依赖兄弟调用的共享写入。其他未声明顺序的插件工具默认有序；本工具与有序工具同处一轮响应时也按该轮顺序执行，不承诺并行。

简报的 respond 使用当前合同：普通消息以 `messages[].source` 关联本次命令的真实已读 M，`next=end`；成功回应由宿主派生，不填写 `sources.status=replied`。`sources` 仅声明 `silent` 或 `incomplete` 及原因，不能用沉默掩盖尚未完成的要求。读取资料 ID 不自动等同实际阅读，来源号由本次输入提供，不在代码中固定。

读取、Agent、提交或发送失败沿原记录保留，不改用另一来源或自动重发。日期和时间是读取时的快照；简报在稍后的送达时可能已有数秒差异。
