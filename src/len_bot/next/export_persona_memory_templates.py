"""Export native categories with real, fixed persona directories; never install or enable them."""

from __future__ import annotations

from pathlib import Path
from string import Template
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError
import yaml

from .config import HostConfig, LabConfig, load_instance_config
from .instance_lock import instance_lock
from .memory_openviking import _segments
from .persona import load_persona
from .store import encode


PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'
COMMON = ('lenbot_portrait', 'lenbot_events', 'lenbot_participant_commitments')


class ContentField(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    name: Literal['content']
    type: Literal['string']
    merge_op: Literal['patch']
    description: str


class NativeCategory(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    memory_type: str
    description: str
    directory: str
    filename_template: str
    enabled: Literal[True]
    stage: Literal['user']
    peer_enabled: bool
    operation_mode: Literal['upsert']
    content_template: str
    embedding_template: str
    fields: list[ContentField]


def read_definition(path: Path) -> NativeCategory:
    raw = path.read_text(encoding='utf-8')
    try:
        return NativeCategory.model_validate(yaml.safe_load(raw))
    except (yaml.YAMLError, ValidationError) as error:
        raise ValueError(f'Invalid native category definition {path}: {error}; raw={raw[:1000]!r}') from error


def substitute(value: object, identity: dict[str, str]) -> object:
    # Parse YAML before substituting scalar values; a role name cannot inject YAML keys.
    if isinstance(value, str):
        return Template(value).substitute(identity)
    if isinstance(value, dict):
        return {key: substitute(item, identity) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, identity) for item in value]
    return value


def export_templates(config: HostConfig | LabConfig) -> dict:
    settings = config.persona_memory_export
    if settings is None:
        raise ValueError('Configure persona_memory_export explicitly in the root lenbot.config.json')
    destination = settings.destination
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f'Native role template export never overwrites an existing destination: {destination}')
    if destination == config.database or config.database.is_relative_to(destination):
        raise ValueError('Template destination must be separate from the declared business database and its directory')
    definitions = {}
    for name in COMMON:
        category = read_definition(PROMPTS / 'openviking_memory' / (name + '.yaml'))
        if category.memory_type != name:
            raise ValueError(f'Common category asset name differs from its native type: {name!r}, actual={category.memory_type!r}')
        definitions[name] = category
    self_base = read_definition(PROMPTS / 'openviking_persona_memory' / 'self.yaml')
    promises_base = read_definition(PROMPTS / 'openviking_persona_memory' / 'promises.yaml')
    sources, used = [], set()
    for item in settings.personas:
        persona = load_persona(item.persona)
        if (len(_segments(persona.id)) != 1 or not persona.id.strip()
                or '{' in persona.id or '}' in persona.id):
            raise ValueError(f'Role ID must be one literal native directory component, without template syntax: '
                             f'{persona.id!r}; package={item.persona}')
        if persona.id in used:
            raise ValueError(f'Choose one definition per actual role ID, not two aliases: {persona.id!r}')
        used.add(persona.id)
        identity = {'persona_id': persona.id, 'persona_name': persona.name}
        directory = 'viking://user/{{ user_space }}/memories/bot/' + persona.id
        types = []
        for base, name, filename in ((self_base, item.self_type, 'self.md'),
                                     (promises_base, item.promises_type, 'promises.md')):
            if base.peer_enabled or base.filename_template != filename:
                raise ValueError(f'Role category blueprint must have its fixed filename and no peer target: {base!r}')
            category = NativeCategory.model_validate(substitute(base.model_dump(), identity))
            category = category.model_copy(update={'memory_type': name, 'directory': directory})
            definitions[name] = category
            types.append({'memory_type': name, 'directory': directory, 'filename': filename})
        sources.append({'persona_id': persona.id, 'name': persona.name, 'package': str(item.persona), 'types': types})
    # Every input is parsed before creating any output. Writes are exclusive, not a multi-file transaction.
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o700)
    files = []
    for name, category in definitions.items():
        path = destination / (name + '.yaml')
        with path.open('x', encoding='utf-8', newline='') as stream:
            stream.write(yaml.safe_dump(category.model_dump(), allow_unicode=True, sort_keys=False))
        files.append(str(path))
    result = {'destination': str(destination), 'files': files, 'personas': sources,
              'suggested_memory_policy': {'self': {'enabled': True}, 'peer': {'enabled': True},
                                          'working_memory': {'enabled': False}, 'memory_types': list(definitions)},
              'notice': 'Local YAML files only. No service registration, extraction, model/config switch or old data conversion. '
                        'Fixed role directories do not prove semantic attribution; unknown historical role IDs stay unknown. '
                        'Copy only YAML files to the service after explicit authorization. Failed output is kept; no retry or overwrite.'}
    with (destination / 'result.json').open('x', encoding='utf-8') as stream:
        stream.write(encode(result))
    return result


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit('Role template export takes no overrides; configure the sole root file')
    with instance_lock(Path.cwd()):
        print(encode(export_templates(load_instance_config(Path.cwd()))))


if __name__ == '__main__':
    main()
