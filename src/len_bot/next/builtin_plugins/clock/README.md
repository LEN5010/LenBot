# clock

群里发 `/时间`，插件按本场景时区直接回复当前日期、星期和时间。命令消息不叫醒大脑；回复进入消息日志，大脑下次醒来时能看到。

根配置：

```json
"plugins": {"clock": {"show_seconds": true}},
"scenes": {"group:10001": {"plugins": ["clock"]}}
```

也可在「能力 → 插件」打开 clock、保存配置并选择启用场景。`show_seconds` 默认开启；关闭后时间精确到分钟。无需模型或工作容器。
