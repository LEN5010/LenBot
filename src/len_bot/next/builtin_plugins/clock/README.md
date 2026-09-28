# clock

群里发 `/时间`，插件按本场景时区直接回复当前日期、星期和时间。命令消息不叫醒大脑；回复进入消息日志，大脑下次醒来时能看到。

根配置：

```json
"plugins": {"clock": {"show_seconds": true}},
"scenes": {"group:10001": {"plugins": ["clock"]}}
```

由旧 `local_plugins/local_clock` 迁来。旧版的读取工具没有迁移，因为每次大脑请求已经带当前时间；旧版由插件内 Agent 组织的时间简报也没有迁移。
