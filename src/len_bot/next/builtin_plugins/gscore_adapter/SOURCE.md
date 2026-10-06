# 协议来源与支持范围

2026-09-28 对照当前 GSUID Core 的 [models.py](https://github.com/Genshin-bots/gsuid_core/blob/master/gsuid_core/models.py)、[segment.py](https://github.com/Genshin-bots/gsuid_core/blob/master/gsuid_core/segment.py) 和 [bot.py](https://github.com/Genshin-bots/gsuid_core/blob/master/gsuid_core/bot.py)，以及 [参考适配器入口](https://github.com/KimigaiiWuyi/astrbot_plugin_gscore_adapter/blob/master/main.py)。只依据协议编写本接口，不依赖或复制旧宿主的claim、trace或自动重连机制。

- 发送身份为onebot和当前真实 bot_id 对应的 OneBot 账号；/gs只提交命令文字、实际@及引用reply_id，user_pm固定普通用户6，不把QQ群管理员提升为Core超级用户。Core自己的权限与账号状态仍在Core管理。
- 目标只接明确启用的group/direct；支持text、at、base64://图片、Core当前URL转码分支的裸base64、link://HTTP(S)图片及仅供尺寸提示的image_size。不把本地路径当宿主路径，不自动改服务/改图片格式重试。不支持文件、音频、合并转发、撤回/禁言等控制，不把它们扁平化成成功文字。
- MessageSend.echo可以没有，正常帧仍发送；有值时，recall_message_id.data.id回传实际确认的平台ID列表，无确认则null。当前Core支持列表并会展开；它是取回实际发送ID，不是业务成功事务。协议错误无法解析整帧时记录原文片段，不猜echo补回执。
- 启动一次连接，断连停止常驻读取并显露错误；/gs连接是明确的新连接请求，不重放旧命令。若第一次启动就失败，此插件由宿主标为失败，在 Core 就绪后点击该插件的「重载」。不保证Core自身不会在重连后重送帧；本适配器不创建假身份或按正文去重。

HTTP图片可来自运营者信任的Core提供的地址，单次读取，限时限量；没有把源站Cookie或宿主环境代理带过去。根配置的Core令牌仅用于指定WebSocket端点，不发给图片地址。完整实际联调仍需运营者使用自己的Core版本与游戏插件确认。
