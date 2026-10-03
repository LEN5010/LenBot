"""Send only registered task deliverables through an explicitly mapped OneBot path."""

from __future__ import annotations

import asyncio
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from .messages import UploadResult
from ..storage.store import encode
from ..work.store import TaskStore


class SendFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    task_id: int = Field(gt=0, strict=True)
    file_id: int = Field(gt=0, strict=True)


SEND_FILE_TOOL = {"type": "function", "function": {
    "name": "send_file",
    "description": "把当前场景任务已登记的交付文件上传到本场景 QQ 群或私聊。"
    "仅接受 task_id 和 file_id，不接受路径；成功回执只表示上传接口返回 file_id，"
    "不证明接收方 QQ 客户端已收到或下载。未知结果不自动重发。",
    "parameters": SendFileArguments.model_json_schema(),
}}


async def execute_send_file(
    records: TaskStore,
    scene: str,
    args: SendFileArguments,
    *,
    local_root: Path,
    visible_root: str,
    upload: Callable[[str, str, str], Awaitable[UploadResult]],
    on_update: Callable[[], None],
) -> str:
    """Record one attempt before I/O and return its actual final event as JSON text."""
    file = records.get_file(scene, args.task_id, args.file_id)
    path = Path(file.path)
    root = local_root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    relative = resolved.relative_to(root)
    source = path.lstat()
    if not stat.S_ISREG(source.st_mode):
        raise ValueError(f"Registered task file is not a regular file: {file.path}")
    if source.st_size != file.size:
        raise ValueError(f"Registered task file size changed: {file.path}; "
                         f"registered={file.size}, actual={source.st_size}")
    platform_path = str(PurePosixPath(visible_root).joinpath(*relative.parts))

    event_id = records.start_file_upload(file, platform_path)
    on_update()
    try:
        result = await upload(scene, platform_path, file.name)
    except asyncio.CancelledError as error:
        records.finish_file_upload(scene, file.task_id, event_id, UploadResult(
            "unconfirmed", None, f"{type(error).__name__}: {error}", None,
        ))
        on_update()
        raise
    except Exception as error:
        result = UploadResult(
            "unconfirmed", None, f"{type(error).__name__}: {error}", None,
        )
    event = records.finish_file_upload(scene, file.task_id, event_id, result)
    on_update()
    return encode({"task_id": file.task_id, **event})
