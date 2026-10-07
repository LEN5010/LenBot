"""Actual request sender for privileged tools: the model picks a message, the host reads its sender."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import LabConfig
    from ..platform.messages import ChatMessage
    from ..storage.store import Store

SOURCE_DESCRIPTION = '发起这项请求的真实消息 ID；从聊天记录选择，宿主据此读取实际发送者，不能填写账号 ID'


def source_message(store: Store, config: LabConfig, platform_id: str) -> ChatMessage:
    message = store.find_message(config.scene, platform_id)
    if (message is None or message.is_self or message.recalled or message.send_status != 'received'
            or message.sender.uid in config.attention.other_bot_ids):
        raise ValueError(f'source_message_id 不是当前场景的真实请求消息：{platform_id!r}')
    if message.sender.uid in config.permissions.blacklist:
        raise PermissionError(f'账号 {message.sender.uid} 在当前场景黑名单中')
    return message


def with_requester(store: Store, config: LabConfig, arguments: dict) -> dict:
    """Replace the model-facing source_message_id with the host-read requester account."""
    if 'requester' in arguments:
        raise ValueError('requester 由宿主按 source_message_id 读取，不能直接填写账号')
    values = dict(arguments)
    source = values.pop('source_message_id', None)
    if source is not None:
        if not isinstance(source, str):
            raise ValueError(f'source_message_id 必须是消息 ID 字符串：{source!r}')
        values['requester'] = source_message(store, config, source).sender.uid
    return values


def source_schema(schema: dict, *, description: str = SOURCE_DESCRIPTION) -> dict:
    """Model-facing schema: the internal requester field becomes source_message_id."""
    properties = dict(schema['properties'])
    requester = properties.pop('requester')
    nullable = 'anyOf' in requester
    field = {'type': 'string', 'minLength': 1, 'description': description}
    properties['source_message_id'] = ({'anyOf': [field, {'type': 'null'}], 'default': None} if nullable else field)
    required = ['source_message_id' if name == 'requester' else name for name in schema.get('required', [])]
    return {**schema, 'properties': properties, **({'required': required} if required else {})}
