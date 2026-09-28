"""Selected GSUID Core MessageSend contract; no generated frame identities."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class TextPart(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    type: Literal['text']
    data: str = Field(min_length=1)


class AtPart(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    type: Literal['at']
    data: str = Field(pattern=r'^(?:all|[1-9][0-9]*)$')


class ImagePart(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    type: Literal['image']
    data: str = Field(min_length=1)


class ImageSize(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    type: Literal['image_size']
    data: tuple[Annotated[int, Field(gt=0)], Annotated[int, Field(gt=0)]]


class Frame(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True, hide_input_in_errors=True)
    bot_id: Literal['onebot']
    bot_self_id: str = Field(pattern=r'^[1-9][0-9]*$')
    msg_id: str = ''
    target_type: Literal['group', 'direct']
    target_id: str = Field(pattern=r'^[1-9][0-9]*$')
    content: list[Annotated[TextPart | AtPart | ImagePart | ImageSize, Field(discriminator='type')]] = Field(min_length=1)
    echo: str | None = None

    @property
    def scene(self) -> str:
        return ('group:' if self.target_type == 'group' else 'private:') + self.target_id


def parse_frame(raw: str | bytes) -> Frame:
    try:
        return Frame.model_validate_json(raw)
    except ValidationError as error:
        raise ValueError(f'Invalid Core MessageSend: {error}; raw={raw[:500]!r}') from error
