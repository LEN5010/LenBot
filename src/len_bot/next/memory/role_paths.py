"""Actual Bot-role file targets; content and conversational references remain model decisions."""


def require_bot_path(path: str, persona_ids: set[str], *, native: bool = False) -> None:
    parts = path.split('/')
    if native:
        if len(parts) >= 4 and parts[0] == 'peers' and parts[2:4] == ['memories', 'bot']:
            raise ValueError('原生Bot记忆放在场景根memories/bot/<实际角色ID>/，不能置于人物peer目录')
        if parts[:2] != ['memories', 'bot']:
            return
        position = 2
    else:
        if parts[0] != 'bot':
            return
        position = 1
    if len(parts) <= position + 1 or parts[position] not in persona_ids:
        raise ValueError(f'Bot记忆文件须位于本场景已知实际角色ID的目录下；path={path!r}，'
                         f'actual_persona_ids={sorted(persona_ids)!r}。未知旧身份不按昵称或当前角色补猜。')
