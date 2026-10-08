"""Read wake markers retained in historical mind requests for offline upgrades and replay."""


def request_wake(request: dict) -> tuple[str, str]:
    if 'snapshot_expired_at' in request:
        return 'unknown', ''
    for message in reversed(request['messages']):
        content = message.get('content')
        if message['role'] != 'user' or not isinstance(content, str):
            continue
        if content.startswith('<当前场景状态与参考>'):
            continue
        for marker, channel in (
            ('[直接唤醒：', 'direct'), ('[点名：', 'named'), ('[对话继续]', 'focus'),
            ('[群里在聊；', 'ambient'), ('[恢复未结束的对话]', 'resume'),
            ('[定时唤醒]', 'schedule'), ('[插件 ', 'plugin'),
        ):
            if content.startswith(marker):
                return channel, content
        return 'unknown', content
    return 'unknown', ''
