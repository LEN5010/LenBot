# 可选服务

只聊天的话，有 OneBot 和一个聊天模型就够了。下面这些用到再加，都在面板里配置。

## 模型用途

模型页先添加服务商，再给各个用途绑定模型。

| 用途 | 做什么 | 必需 |
|---|---|---|
| mind | 群聊主脑：判断、说话、调工具 | 是 |
| vision | 看图 | 否 |
| memory | 后台整理记忆、写目录摘要 | 否 |
| learner | 学说法和黑话，判断回复效果 | 否 |
| worker | 后台任务里的 Pi | 否 |
| asr | 语音转写 | 否 |

上下文窗口、输出上限和超时照服务商文档填。模型拒绝请求时直接报错，不会自动缩小参数或换模型重试。

## 向量检索

记忆默认用全文检索。想按意思检索，准备一个 OpenAI 兼容的 embeddings 服务。用 Ollama 的话：

```sh
docker compose -f deploy/current/services.compose.yaml up -d embeddings
docker exec lenbot-embeddings ollama pull bge-m3:567m
```

然后在面板里添加 `openai-embeddings` 服务商，地址 `http://127.0.0.1:11434/v1`，模型 `bge-m3:567m`，1024 维。换向量模型后要停机[重建索引](./memory#重建索引)。

## 语音转写

自动转写群里的语音需要一个 OpenAI 兼容的转写服务，绑定到 `asr` 用途，再在**群聊 → 设置**里打开「自动转写语音消息」。本地部署 whisper.cpp 的方法见仓库里的 [ASR 说明](https://github.com/lendevs/LenBot/blob/master/deploy/current/asr.md)。

## 账号浏览

让后台任务使用你自己登录的浏览器，需要单独的守护进程、浏览器扩展和配对，见[浏览器配套组件](https://github.com/lendevs/LenBot/blob/master/deploy/browser/README.md)。普通的公开网页浏览在任务镜像里就能用，不需要这些。

## 代理软件的 fake-ip 模式

Clash、Surge 这类代理开了 fake-ip 后，所有域名都会解析成 `198.18.x.x` 一类的保留地址。LenBot 读网页、取图片和任务出网时会拒绝保留地址，报错里能看到 `私有或保留地址`。

在**能力 → 网页**的「代理的假 IP 网段」里填上代理实际用的网段，对应配置文件里的：

```json
"network": {
  "fake_ip_networks": ["198.18.0.0/15"],
  "public_dns_url": "https://dns.google/resolve"
}
```

之后按域名连接、解析结果又落在这个网段时，LenBot 会通过 `public_dns_url` 查真实的 A／AAAA 记录，确认是公网地址后直接连过去。

- `public_dns_url` 要是 DNS JSON 接口（接受 `name`、`type` 参数，返回 `Status`、`Answer`），连不上 Google 的话换一个同协议的服务。
- 查询失败，或者查到的真实地址是内网，这次请求直接报错。
- 直接写保留 IP 的地址仍然会被拦。
