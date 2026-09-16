# 固定媒体片段 worker

在项目根执行 `docker build -f containers/media/Dockerfile -t lenbot-media:local .`，由 Gateway 的 `media` 镜像引用选择。Bot 不解码，不持有 Docker socket；容器只复制协议、固定 ffmpeg 入口及依赖，不复制根配置、Cookie、数据库或工作资料。运行使用非 root UID/GID、只读根文件系统、受限内存/PID 和现有工作卷限制。构建及 Linux 运行证据尚待目标环境取得。

Gateway 需要已实际核验的代理网络，配置例中的 `deployment_verified: false` 不能直接改成已验收。根据平台实际返回的公开 CDN 域配置 resource 类；示例给出 bilivideo.com/cn/net 子域。媒体工具本身只允许 HTTPS 平台域、禁用重定向和自动登录。临时 URL 只在宿主/控制文件间传递，模型只提供 bvid、cid、start_ms、end_ms、frames、audio。

`segment_worker` 用固定本地 HTTP Range 入口向 ffmpeg 提供两条已选择轨道，代理承担真实网络出口。响应体逐块计数，包括探测、索引和关键帧超取；超过上限停止读取并返回 partial/error。内核、TLS 和代理开销另在原执行出口事件里记录。不能保证30秒片段只下载30秒数据；单次请求上限128次，片段最长300秒、最多12帧。

视频经过容器/轨道/时长/像素核对后按间隔抽帧，时间点来自准确 seek 后解码 PTS，记录为原视频毫秒坐标。音频保存16kHz单声道 WAV 及实际结束时间。帧间画面和区间外内容未覆盖。已完成文件经 Gateway 原产物目录回收；取消按 execution_id 终止容器，取消不写成分析完成。每次只清理固定 worker 自己的输出名，历史执行快照仍由 Gateway 保存。

`media_analysis` 默认未配置，依赖根配置 `workspace.gateway`；可选 `transcription` 默认 null，使用同一个 ProviderRegistry。运营者需要明确填写已核对模型及 `openai_verbose_json` 协议、每音频秒的工作额度估算和正文上限。供应商原始 usage 保留，时长与本地 token 估算不冒充账单。模型工作绑定 `supports_vision` 默认 false，只有确认支持后才装配采样帧。

协议依据：[FFmpeg seek/duration 参数](https://ffmpeg.org/ffmpeg.html)、[FFmpeg HTTP/Range 协议](https://ffmpeg.org/ffmpeg-protocols.html)、[OpenAI 音频转写请求](https://developers.openai.com/api/reference/python/resources/audio/subresources/transcriptions/methods/create)。不是所有转写模型都支持 verbose_json 和分段时间戳；不自动替换型号或重试不确定请求。
