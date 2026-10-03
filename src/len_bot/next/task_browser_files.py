"""Record a browser-produced task file and its source page, without copying it."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .task_resources import ResourceFileRef, ResourceLocation, TaskResources
from .tasks_config import WorkerSettings
from .tasks_store import Task, TaskStore


class BrowserOutput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    path: str
    kind: Literal['download', 'screenshot', 'pdf']
    page_url: str = Field(min_length=1)
    page_title: str
    download_url: str | None = None

    @field_validator('path')
    @classmethod
    def output_path(cls, value: str) -> str:
        ResourceLocation.relative_path(value)
        if not value.startswith('out/browser/'):
            raise ValueError(f'Browser output must be within out/browser/: {value!r}')
        return value

    @classmethod
    def parse(cls, raw: bytes) -> 'BrowserOutput':
        try:
            return cls.model_validate_json(raw)
        except ValidationError as error:
            raise ValueError(f'Invalid browser file result: {raw[:500]!r}; {error}') from error


def record_browser_output(settings: WorkerSettings, records: TaskStore, item: Task,
                          output: BrowserOutput) -> dict:
    reference = ResourceFileRef(scope='workspace', task_id=item.id, path=output.path)
    opened = TaskResources(settings, records).open(item.scene, reference)
    with opened.stream:
        result = {'name': opened.name, 'size': opened.size, 'mime_type': opened.mime_type,
                  'path': f'/workspace/{output.path}', 'reference': reference.model_dump(),
                  'source': output.model_dump(exclude={'path'})}
    records.add_event(item.scene, item.id, 'browser_file', result)
    return result
