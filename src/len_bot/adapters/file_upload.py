"""One explicitly selected file-upload implementation, not a generic OneBot promise."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from len_bot.actions.models import DeliveryResult, DeliveryStatus

# Config/receipt labels, not alternate action names: both selected adapters send
# upload_group_file and require an actual data.file_id before recording success.
PROTOCOLS = {
    'napcat': 'upload_group_file_data_file_id',
    'snowluma': 'upload_group_file',
}


class FileUploadConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    implementation: Literal['napcat', 'snowluma']
    version: str | None = Field(default=None, min_length=1, max_length=80,
        description='可选的现场版本记录；协议准入不按实现的发行版本判定')
    protocol: Literal['upload_group_file_data_file_id', 'upload_group_file']
    deployment_verified: bool = Field(default=False,
        description='运营已核对所选实现、文件动作与仅资产目录的只读挂载；真实上传成功只从 FILE_UPLOADED 的 file_id 派生，本标记不要求先有成功上传')
    export_mount_path: Literal['/lenbot-files'] = '/lenbot-files'

    @model_validator(mode='after')
    def matching_protocol(self):
        expected = PROTOCOLS[self.implementation]
        if self.protocol != expected:
            raise ValueError(f'{self.implementation} 必须使用 protocol={expected}，不能改用其他实现或自动回退')
        return self


class UploadEnvelope(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    status: str
    retcode: int
    data: dict | None = None


class UploadIdentity(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    file_id: str = Field(min_length=1, max_length=1024)


def upload_response(data, transport, protocol):
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
        file_receipt={'protocol': protocol, 'status': response.status,
                      'retcode': response.retcode, 'file_id': identity.file_id})
