# macOS 本地语音转写

使用官方 `whisper.cpp v1.9.4` 的 `whisper-server`，多语模型为 `large-v3-turbo-q5_0`，模型约 547 MiB。安装数据放在忽略目录 `state/services/asr`，不加入 LenBot 的 Python 依赖。

`start-asr.sh` 固定模型、回环地址与原生 HTTP 路径，直接接入现有 `audio/transcriptions` 客户端，不加代理或改聊天模型，也不自动重启。

## 安装

在源码根操作，需要 macOS Apple Silicon、Xcode 命令行工具、uv 和已安装的项目环境：

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

运行需保留模型、`build/bin/` 和原许可材料。Metal 内核已嵌入二进制；压缩包、`build-tools/` 及 `build/` 中除 `bin/` 外的中间物可清理，重建时重新安装构建工具。

## 启动与接入

在一个单独终端启动，Ctrl-C 停止；此命令不会启动 LenBot：

```sh
./deploy/current/start-asr.sh
```

仅监听 `127.0.0.1:18171`。v1.9.4 默认 `no_context=true`，不接受 `--no-context` 参数；脚本用 `--no-fallback` 禁止温度递增回退。在面板模型页配置：

| 项目 | 值 |
| --- | --- |
| 服务商名称 | `local-asr` |
| 接口类型 | `openai-audio` |
| 接口地址 | `http://127.0.0.1:18171/v1` |
| 密钥 | `local-whisper-no-auth` |
| 模型名 | `large-v3-turbo-q5_0` |
| 语言 | `zh`；留空沿服务的自动语言识别 |
| 超时 | `120` 秒 |
| 价格 | 不填；该服务不返回用量计量 |

密钥是非秘密占位：原生服务不鉴权，LenBot 契约要求非空字符串；该值不是访问控制，服务只部署于回环。

服务固定加载一份模型，不提供 `/v1/models`，请求的 `model` 不切换模型。部署名称和 ASR 绑定须一致。接口接收 multipart `file/language/response_format=json` 并返回 `text`；宿主发送 OneBot 提供的 WAV，不启用 ffmpeg 自动转换，也不调用聊天接口。

保存后重启 LenBot；场景须允许语音转写，角色开放 `transcribe`。下载安装和服务启动不等于 QQ 语音转写成功；真实音频质量与端到端交付尚待验收。

来源：[服务参数](https://github.com/ggml-org/whisper.cpp/blob/v1.9.4/examples/server/README.md)、[模型列表](https://github.com/ggml-org/whisper.cpp/blob/v1.9.4/models/README.md)。
