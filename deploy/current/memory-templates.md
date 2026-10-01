# 原生记忆分类模板

这份接线针对当前原生会话 `memory_policy` 和服务的 `memory.custom_templates_dir`，不是向 HTTP 添加一个未实现的提示词字段。宿主和服务模型都不会被这份模板自动替换；服务全局模板可能影响同服务的其他使用者，因此先由运营者确认独立服务的范围。

## 服务端先准备

1. 停止相关宿主抽取和服务端写者，保留原服务配置及数据备份。
2. 使用下述旧混合分类时，只复制 `lenbot_portrait.yaml`、`lenbot_events.yaml`、`lenbot_bot.yaml`、`lenbot_commitments.yaml` 到服务机器上的实际目录。按需选择分类，不将所有提示词目录当服务模板目录。
3. 在服务自己的配置中明确设置 `memory.custom_templates_dir` 为该目录，按服务本身方式重启。此项不是 LenBot 根配置字段，也不使用环境变量去覆盖 LenBot 运行参数。
4. 查看服务实际类别加载记录；上游注册器会记录加载错误，宿主不把目录存在或复制成功当作类别已经注册。不支持这些类别的服务不要选择对应名称；失败不自动换版本或服务。

四个 `memory_type` 为 `lenbot_portrait`、`lenbot_events`、`lenbot_bot`、`lenbot_commitments`。服务保留其内置类别；这些模板位于独立 `memories/lenbot` 子目录，不覆盖默认文件，也不自动转换旧资料。

## 宿主明确采用

通过记忆配置页保存，或在宿主已停机时编辑其唯一根配置的 `memory.openviking.memory_policy`：

```json
{
  "self": {"enabled": true},
  "peer": {"enabled": true},
  "working_memory": {"enabled": false},
  "memory_types": [
    "lenbot_portrait", "lenbot_events", "lenbot_bot", "lenbot_commitments"
  ]
}
```

`null` 保持服务默认；`memory_types: null` 表示服务全部启用类别，`[]` 表示不选择类别，不暗中替换成上述四类。人物与场景根是来源范围，Bot/承诺类别的 `peer_enabled: false` 将其留在场景根；人物事实与事件可按原生实际 peer QQ 路径组织。新设置只作用于后续新会话批次；保存配置不执行抽取，重启也不会自动把旧消息全量回填。

显式采用后，宿主创建带策略的原生会话，并在加入原消息之前 GET 实际会话元数据核对保存策略。不一致或解析失败结束该批次，保留原回包；未知类别由原生服务按自身提交边界报错。模型费用、提交结果和任务完成沿既有记忆处理记录，不把接收回执当成记忆已生效。

## 当前范围

### 按实际角色ID准备固定目录

若需要不同角色的自我记忆与承诺分别放在 `memories/bot/<实际角色ID>/self.md` 和 `promises.md`，在宿主已停机时将以下准备参数加入唯一根配置，使用已有的真实角色包路径和新输出目录：

```json
{
  "persona_memory_export": {
    "destination": "exports/persona-memory-types",
    "personas": [
      {
        "persona": "personas/diana",
        "self_type": "lenbot_diana_self",
        "promises_type": "lenbot_diana_promises"
      }
    ]
  }
}
```

这是示例；目录名不决定角色ID，入口从包中读取实际 `persona.yaml.id`，可明确选择旧角色包。每个ID只选一份定义，类别名全局唯一且不覆盖已有共享类别；非法路径或模板符号报原值，不改名。然后在同一实例根执行：

```sh
python -m len_bot.next.export_persona_memory_templates
```

入口先解析全部输入，再独占创建输出目录，不覆盖任何现有目录、角色、根配置或服务文件，不调用模型或连接记忆服务。输出包含三份共享人物／事件／非助手参与者承诺分类和每角色两份固定目录定义，`result.json` 列实际文件、角色来源和建议白名单。失败保留已写文件，不自动重跑或换路径。

将**输出目录里的 YAML**按前述授权、备份和服务加载流程安装，不要部署 `prompts/openviking_persona_memory` 的占位蓝图，也不要将 result.json 当分类文件。确认实际类别加载后，明确采用 result.json 的建议白名单；它不含旧混合 Bot／承诺分类，不把缺失类别换成旧分类。固定角色目录只沿服务当前 user_space 分区，不复制到各QQ peer目录；类名是原生服务实际类别键，不是人物或消息别名。

业务34捕获的persona_id随归档进入输入，模型仍判断具体指代和归属；未知旧身份保持未知。模板导出只是准备文件，不是服务注册、语义质量验收或已完成旧资料拆分。此入口本轮未执行，服务加载和真实抽取也未验证。

旧四分类记录事实、冲突来源、已发生事件、助手说法和承诺状态；它不审核真假、不执行承诺、不创建提醒或工作任务。旧混合分类仍不提供角色 ID 分区；新导出代码提供目录固定的角色专用类别，但实际加载/内容归属未验证。默认资料的语义转换、历史副本删除与完整遗忘依然是独立事项。

目前只有本机上游源码合同核对、配置/请求接线和模板文件；按本轮安排未做服务模板加载、真实 HTTP/模型抽取或语义评测。不将其称为完整群画像或两个记忆后端等价性已经通过。
