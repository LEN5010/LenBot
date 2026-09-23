# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 feat/s0-product-contract，基线 a4e3e89，开始时工作区干净；上一阶段维护固定合同已提交，属于实际进展。
- S1-02：普通工作最终请求中已知图片资产未进入逐位置记录。原窗口只返回 current_pixel_assets 集合，不能对应每条消息的具体位置；在原工作上下文模块补局部映射函数，不新增表、状态或媒体读取路径。

## 本批交付与核对

新增 request_image_assets 复用 _image_contexts 对原工作清单的读取，将 image_manifest 的图片序号转换为 content 的实际块位置。只消费当前位置的 image_url 和已同步的 block_index，不用 URL、文件名、全轮资产集合或未保留目录项猜映射。它不改窗口内容、清单、资产、已读状态、模型资格或容量限制。

InformationJobRunner 原 finalize_request 在来源核对、图片同步和容量检查之后，对发送副本逐消息填写已有 _RequestLocation.image_assets；tool_presentations 同时保留。原网关继续剥离私有信息，将位置和资产 ID 写到既有请求记录。被移出或 included_elsewhere 的目录项不会被登记成本位置像素；不保存 base64、读取文件或发起下载，旧记录不回填。

- 阅读工作图片清单生成、同步时的序号重排／移出／异处保留、原 finalize_request、prepare_request_record 的图像位置投影，以及现有 RequestRecordDetails 的图像缺定位计数。两种序号不同，不能直接把 block_index 当作 content 索引。
- 局部函数的业务缺口是最终请求逐位置关联，原集合无法表达；未改变 synchronize_image_window 返回合同、工作归属或重启恢复路径，也未增加新的图片协议。
- 源码初查包含不存在的 tools/media.py，输出被截断；随后按实际工作模块及导入定位，不据缺失路径新增文件或推导能力。无本批实际业务失败原文。
- uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/runtime/work_context.py src/len_bot/runtime/job_runner.py 退出 0；git diff --check 无格式错误。无前端改动，未重跑前端构建；实际像素位置记录与页面计数仍待同版人工观察。
- 未运行测试、夹具、断言探针、自动截图、回放、故障注入、覆盖率、依赖安装、服务或模型／平台调用；未读取真实配置／业务库、图片字节或实发。

## 待决定与接续

1. 继续 S1 的请求定位展示与耗时缺口；当前图片逐位置数据已登记，原界面主要展示图片数量和缺定位数量，详细可读性仍可沿现有组件完善。
2. S2 完整活动段／未登记依赖仍有源码工作；额外原生字段保存、分类期限、许可证／素材授权、精确支持组合与公开承诺仍待决定。
3. S6 候选／现场／远端 CI／升级／外部迁移／发布及 S7 独立使用者／作者记录未完成；原 S3／S4／S5 待人工复核项保持，不以静态记录代替现场。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
