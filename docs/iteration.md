# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 feat/s0-product-contract，基线 4347f47，开始时工作区干净；上一阶段插件等待计时已本地提交，属于实际进展。
- S1-04：原 HTTP 尝试已有 duration_ms、成功响应已有 latency_ms，但客户端跨尝试等待及失败／取消前的整体时长未单列。沿原传输记录补一个时长，不新建计时表、监控系统或调用。

## 本批交付与核对

ModelGateway 在原调用登记后的计时起点，分别于响应解析完成及异常／取消到达时记录 transport.client_elapsed_ms，随后才进入原 shield 结算。这使跨尝试等待、客户端内部重试间隔与本地解析被同一单调时长覆盖，不把逐 HTTP 尝试求和当总值；原成功 latency_ms、调用账起止和 HTTP 时长仍保持各自口径。

新增时长不含前置准入／登记、后续结算、执行槽位等待或消息发送；失败和取消仍保留原 usage、error_type、终态及费用不确定性，不证明上游停止。原 begin／end 调用事务与传输 trace 身份不变，没有增加重试或吞错逻辑。旧记录、登记前失败及未经过此网关的入口不补数值。

ActivityView 的原调用详情单列“客户端请求至解析／中断”，使用现有 formatDurationMs；未知显示未记录，不用当前时间或调用起止反推。原查询服务已经按 call_id／scene_id 读取完整受控传输对象，本批无需另加 API 或读取路径。

- 阅读 RecordedHttpClient 的请求／响应钩子与逐尝试时长、ModelGateway 的 body／usage／响应解析与取消结算，以及 end_model_call 原事务；成功与失败均在结算前取时，结算等待不混入客户端耗时。
- 核对查询服务 model_call／_call 原传输返回及实际 ActivityView 调用面板；MessageProgress 原调用账时间仍是另一口径，未批量改写指标或当成首字延迟。
- 源码定位出现 `rg: src/len_bot/web/frontend/src/views/CallsView.vue: IO error for operation on src/len_bot/web/frontend/src/views/CallsView.vue: No such file or directory (os error 2)`；随后按组件引用定位 ActivityView.vue，未新增猜测页面。
- uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/cognition/gateway.py 退出 0；原前端目录 npm run build 退出 0，491 个模块、1.65s，日志 /private/tmp/lenbot-client-elapsed-build.log。git diff --check 无格式错误，产物不进 Git。
- 无获准同版面板或本批实际失败／取消记录，页面和真实时长待人工验收；未启动服务、故意中断请求或制造错误。未运行测试、夹具、断言探针、截图、回放、故障注入、覆盖率、依赖安装或真实模型／平台调用；未读取业务库／根配置或实发。

## 待决定与接续

1. 继续 S1 的其他锁／工作排队和登记前阶段缺口，不把网关客户端时长当作所有等待已覆盖；请求材料与完整时间线仍待完善和同版人工观察。
2. S2 完整活动段／未登记依赖仍有源码工作；额外原生字段保存、分类期限、许可证／素材授权、精确支持组合和公开承诺待决定。
3. S6 的候选／现场／远端 CI／升级／外部迁移／发布及 S7 独立使用者／作者记录未完成，原 S3／S4／S5 待人工复核项保持。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
