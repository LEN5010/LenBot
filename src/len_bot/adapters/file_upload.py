"""One explicitly configured NapCat protocol, not a generic OneBot promise."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from len_bot.actions.models import DeliveryResult, DeliveryStatus


class FileUploadConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    implementation: Literal['napcat']
    version: str = Field(min_length=1, max_length=80, description='运营核对的实际版本')
    protocol: Literal['upload_group_file_data_file_id']
    deployment_verified: bool = Field(default=False, description='已核对实际版本、file_id 回执与仅资产目录的只读挂载')
    export_mount_path: Literal['/lenbot-files'] = '/lenbot-files'


class UploadEnvelope(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    status: str
    retcode: int
    data: dict | None = None


class UploadIdentity(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    file_id: str = Field(min_length=1, max_length=1024)


def upload_response(data, transport):
    try:
        response = UploadEnvelope.model_validate(data)
        if response.status == 'failed':
            return DeliveryResult(status=DeliveryStatus.REJECTED, transport=transport,
                error_code=str(response.retcode), error='OneBot 明确拒绝文件上传')
        if response.status != 'ok' or response.retcode != 0:
            raise ValueError('not a confirmed upload')
        identity = UploadIdentity.model_validate(response.data)
    except (ValidationError, ValueError):
        return DeliveryResult(status=DeliveryStatus.UNKNOWN, transport=transport,
            error_code='file_receipt_unconfirmed', error='上传请求未取得已配置协议要求的真实 file_id，保留占用且不自动重传')
    return DeliveryResult(status=DeliveryStatus.SENT, transport=transport, file_id=identity.file_id,
        file_receipt={'protocol': 'upload_group_file_data_file_id', 'status': response.status,
                      'retcode': response.retcode, 'file_id': identity.file_id})
