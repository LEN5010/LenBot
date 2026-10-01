"""One native Docker mount report before reconciling a recorded task container."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class ContainerMount(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore')
    type: str = Field(alias='Type')
    source: str = Field(alias='Source')
    destination: str = Field(alias='Destination')
    writable: bool = Field(alias='RW')


MOUNTS = TypeAdapter(list[ContainerMount])


def require_task_mounts(raw: str, *, workspace: Path, home: Path, control: Path) -> None:
    try:
        mounts = MOUNTS.validate_json(raw, strict=True)
    except ValidationError as error:
        raise ValueError(f'Invalid Docker task mounts: {error}; raw={raw[:1000]!r}') from error
    expected = {'/workspace': (str(workspace), True), '/home/agent': (str(home), True),
                '/run/lenbot': (str(control), False)}
    present: set[str] = set()
    for mount in mounts:
        if mount.destination == '/inputs':
            expected['/inputs'] = (str(control.parent / 'inputs'), False)
        if mount.destination not in expected:
            continue
        if mount.destination in present:
            raise ValueError(f'Duplicate task mount destination {mount.destination!r}; raw={raw[:1000]!r}')
        present.add(mount.destination)
        source, writable = expected[mount.destination]
        if mount.type != 'bind' or mount.source != source or mount.writable != writable:
            raise ValueError(f'Recorded container belongs to a different task/instance mount: '
                             f'expected {mount.destination!r} source={source!r} RW={writable}; '
                             f'actual={mount.model_dump()!r}')
    if missing := set(expected) - present:
        raise ValueError(f'Recorded container lacks its current task mounts: {sorted(missing)!r}; raw={raw[:1000]!r}')
