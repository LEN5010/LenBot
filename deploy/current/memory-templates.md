# OpenViking 记忆分类模板

模板配合宿主 `memory.openviking.memory_policy` 和服务 `memory.custom_templates_dir` 使用，不替换服务模型或通过 HTTP 注入未实现字段。服务级模板可能影响其他使用者，应在明确的独立服务范围内操作。

## 按真实角色生成

需要将不同角色的自我记忆／承诺存到 `memories/bot/<角色ID>/self.md`、`promises.md` 时，在已停机实例的根配置加入实际角色路径与新输出目录：

```json
{
  "persona_memory_export": {
    "destination": "exports/persona-memory-types",
    "personas": [{
      "persona": "personas/diana",
      "self_type": "lenbot_diana_self",
      "promises_type": "lenbot_diana_promises"
    }]
  }
}
```

执行 `uv run --no-sync python -m len_bot.next.export_persona_memory_templates`。ID 来自包内 `persona.yaml.id`，不是目录名；每个 ID 只选一份定义，类别名不覆盖已有类别。入口只创建新目录，不改角色、配置或服务，不调用模型；失败保留部分原件。

输出包含三份共享人物／事件／参与者承诺分类，以及每角色两份固定目录分类；`result.json` 列来源与建议白名单。安装输出的 YAML，不直接安装 `prompts/openviking_persona_memory` 占位蓝图或 result.json。角色路径仍在服务当前 `user_space` 内，不复制到所有 QQ peer；未知历史角色身份保持未知。

已有混合分类也可明确选用 `lenbot_portrait`、`lenbot_events`、`lenbot_bot`、`lenbot_commitments`：只复制对应 YAML，不装整个提示词目录。它们存于 `memories/lenbot`，Bot／承诺不按角色分区；不会自动拆分历史资料或自动与新角色分类混用。

## 服务加载与宿主采用

1. 停止相关抽取／服务写者，保留服务配置与数据备份。
2. 把所选 YAML 放到服务实际目录，设置服务自己的 `memory.custom_templates_dir` 并重启服务；此字段不是 LenBot 根配置。
3. 查看实际类别加载记录，错误按原文处理；文件复制不代表注册成功，不替换版本或退回混合分类。
4. 在面板保存 `memory.openviking.memory_policy`（或宿主停机后编辑根配置），使用 result.json 的实际类别白名单。例如：

```json
{
  "self": {"enabled": true},
  "peer": {"enabled": true},
  "working_memory": {"enabled": false},
  "memory_types": ["lenbot_portrait", "lenbot_events", "lenbot_participant_commitments", "lenbot_diana_self", "lenbot_diana_promises"]
}
```

整份策略为 `null` 沿服务默认；`memory_types: null` 选择服务全部类别，`[]` 不选类别。`peer_enabled:false` 的 Bot／承诺留在场景根，人物事实和事件可进入原生实际 QQ peer 路径；来源分区不替模型判断内容归属。

宿主创建会话后、提交原消息前回读保存策略；不一致或解析失败结束该批次，未知类别由原生服务报错。设置只影响后续批次，保存／重启不自动回填旧消息。模板生成、服务注册、接收回执、抽取完成和语义质量是不同结果；模板不审核真假、不执行承诺或创建任务。历史拆分、完整遗忘和双后端语义等价仍需独立处理。
