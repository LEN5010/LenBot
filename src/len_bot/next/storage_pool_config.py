"""One explicitly provisioned task filesystem, separate from retained deliveries."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .config_types import STRICT


class PoolMount(BaseModel):
    model_config = STRICT
    mount: Path

    @field_validator('mount')
    @classmethod
    def absolute_mount(cls, value: Path) -> Path:
        if not value.is_absolute() or value == Path('/'):
            raise ValueError('storage_pool.mount must name a dedicated absolute mount point')
        return value


class Ext4Pool(PoolMount):
    kind: Literal['ext4']
    image: Path

    @field_validator('image')
    @classmethod
    def absolute_image(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError('storage_pool.image must be an absolute image file path')
        return value

    @model_validator(mode='after')
    def separate_image(self):
        if self.image.is_relative_to(self.mount):
            raise ValueError('The ext4 image must stay outside its own mount point')
        return self


class APFSPool(PoolMount):
    kind: Literal['apfs']
    volume_uuid: str = Field(pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')


StoragePool = Annotated[Ext4Pool | APFSPool, Field(discriminator='kind')]
