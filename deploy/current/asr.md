# macOS 本地语音转写

本文使用官方 `whisper.cpp v1.9.4` 的 `whisper-server`，多语言模型为 `large-v3-turbo-q5_0`，模型文件约 547 MiB。安装数据放在 Git 忽略的目录 `state/services/asr` 中，不加入 LenBot 的 Python 依赖。

`start-asr.sh` 固定了模型和回环地址，以及原生 HTTP 路径，可以直接接入现有的 `audio/transcriptions` 客户端。脚本不设置代理，不修改聊天模型，也不会自动重启服务。

## 安装

在源码根目录中操作。需要以下环境。

- macOS，Apple Silicon
- Xcode 命令行工具
- uv
- 已经安装好的项目环境

```sh
mkdir -p state/services/asr/models
uv venv state/services/asr/build-tools --python .venv/bin/python
uv pip install --python state/services/asr/build-tools/bin/python 'cmake==4.1.3'
curl --fail --location --output state/services/asr/whisper.cpp-v1.9.4.tar.gz \
  https://github.com/ggml-org/whisper.cpp/archive/refs/tags/v1.9.4.tar.gz
tar -xzf state/services/asr/whisper.cpp-v1.9.4.tar.gz -C state/services/asr
curl --fail --location --output state/services/asr/models/ggml-large-v3-turbo-q5_0.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin
state/services/asr/build-tools/bin/cmake \
  -S state/services/asr/whisper.cpp-1.9.4 -B state/services/asr/build \
  -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF \
  -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON -DBUILD_SHARED_LIBS=OFF
state/services/asr/build-tools/bin/cmake --build state/services/asr/build \
  --target whisper-server --parallel 6
```

运行时需要保留模型文件和 `build/bin/`，以及原有的许可文件。Metal 内核已经嵌入二进制文件。压缩包和 `build-tools/` 可以删除，`build/` 中 `bin/` 以外的中间文件也可以删除。重新构建时，需要再次安装构建工具。

## 启动与接入

在一个单独的终端中启动，按 Ctrl-C 停止。这条命令不会启动 LenBot。

```sh
./deploy/current/start-asr.sh
```

服务只监听 `127.0.0.1:18171`。v1.9.4 默认 `no_context=true`，不接受 `--no-context` 参数。脚本使用 `--no-fallback` 禁止温度递增回退。

在面板的模型页按下表配置。

| 项目 | 值 |
| --- | --- |
| 服务商名称 | `local-asr` |
| 接口类型 | `openai-audio` |
| 接口地址 | `http://127.0.0.1:18171/v1` |
| 密钥 | `local-whisper-no-auth` |
| 模型名 | `large-v3-turbo-q5_0` |
| 语言 | `zh`。留空时使用服务的自动语言识别 |
| 超时 | `120` 秒 |

这里的密钥只是一个占位值，并不保密。原生服务不做鉴权，但 LenBot 要求密钥是非空字符串。这个值不起访问控制作用，服务只部署在回环地址上。

服务固定加载一份模型，不提供 `/v1/models`，请求中的 `model` 不会切换模型。部署时使用的名称必须与 ASR 绑定中的一致。接口接收 multipart 格式的 `file/language/response_format=json`，返回 `text`。宿主发送 OneBot 提供的 WAV 文件，不启用 ffmpeg 自动转换，也不调用聊天接口。

### OneBot 语音响应容量

使用真实发送（`delivery` 为 `onebot`）并启用 ASR 后，WebSocket actions 的 `get_record` 会收到包含完整 WAV 的 base64 JSON。`onebot.max_frame_bytes` 限制的是整条入站消息解压后的大小，因此不能只按 WAV 的字节数设置。

所需的最小值为 `4 * ceil(audio.max_bytes / 3) + 65536`，额外的 64 KiB 留给 JSON 元数据。默认的音频上限 16 MiB 对应 **22,435,160 bytes**。

配置中没有写 `max_frame_bytes` 时，加载配置时会按这个公式计算一个有限的默认值，并且至少保留原来 1 MiB 的消息容量。显式填写的值不会被自动调大，小于所需值时会报告配置冲突。

首次配置向导和面板的连接设置在保存时会把上限写入文件。因此即使从未手动调整过，旧实例也可能已经保存了 1 MiB。启用 ASR 之前，请先在面板的「连接 QQ → 高级设置」中增大「单条数据上限」。如果旧配置已经因此无法加载，请停止 LenBot，在根目录的 `lenbot.config.json` 中调整这个字段后再启动。

增大 `audio.max_bytes` 时，也要同步调整已保存的 `max_frame_bytes`。另外两种做法是减小允许的音频大小，或者显式配置已有的 HTTP actions（`action_transport: "http"` 和 `http_url`）。系统不会自动切换通道。没有启用 ASR，或者使用模拟发送和 HTTP actions 时，不执行这项容量检查，消息上限的含义保持不变。

保存后重启 LenBot。场景必须允许语音转写，角色需要开放 `transcribe`。完成下载安装并启动服务，并不代表 QQ 语音转写已经成功。真实音频的识别质量和端到端发送尚未验收。

参考资料：[服务参数](https://github.com/ggml-org/whisper.cpp/blob/v1.9.4/examples/server/README.md)，[模型列表](https://github.com/ggml-org/whisper.cpp/blob/v1.9.4/models/README.md)。
