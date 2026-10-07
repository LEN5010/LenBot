"""Portable, redacted diagnostic exports built from stored records and the host log."""
from __future__ import annotations

from io import BytesIO
import json
import re
from zipfile import ZipFile, ZIP_DEFLATED

from .logs import credentials, redact


def diagnostic_value(value):
    """Exclude known multimodal payloads from the text diagnostic projection."""
    if isinstance(value, list):
        return [diagnostic_value(item) for item in value]
    if isinstance(value, dict):
        if value.get("type") in {"image", "image_url", "input_image", "input_audio"}:
            return {"type": value["type"], "omitted": "binary media excluded from diagnostic export"}
        return {key: diagnostic_value(item) for key, item in value.items()}
    if isinstance(value, str):
        return re.sub(r"(?:data:[^\s;,]+;base64,|base64://)[A-Za-z0-9+/=]+",
                      "[binary media omitted]", value)
    return value


def diagnostic_zip(payload: dict, config, *, plugin_redactor=None) -> bytes:
    text = json.dumps(diagnostic_value(payload), ensure_ascii=False, indent=2, allow_nan=False)
    if plugin_redactor is not None:
        text = plugin_redactor(text)
    text = redact(text, credentials(config), identities=True)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("diagnostic.txt", text)
        archive.writestr("README.txt", "诊断资料，不是可直接回放的评测快照。已隐去配置凭据与至少5位的数字（可能同时遮盖时间、消息号等）。\n"
                         "正文仍可能含昵称、私人事实和第三方返回内容；附件、原图、语音及文件原件不包含在此包中。\n")
    return buffer.getvalue()
