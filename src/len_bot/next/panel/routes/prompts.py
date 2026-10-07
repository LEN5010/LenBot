"""Edit the framework prompts of this instance from the panel's advanced settings."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from string import Template

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from ...configuration.types import STRICT
from ... import prompt_files
from ...runtime.releases import current_version


class PromptText(BaseModel):
    model_config = STRICT
    text: str


class PromptSettings(BaseModel):
    model_config = STRICT
    keep_on_update: bool


def register_host_prompts(app: FastAPI, *, root: Path, user: Callable[[Request], str]) -> None:
    folder = prompt_files.directory(root)

    def summary() -> dict:
        state = prompt_files.load_state(folder)
        edited = set(prompt_files.edited(folder))
        return {'version': current_version(), 'edited_version': state['version'] if edited else None,
                'keep_on_update': state['keep_on_update'],
                'items': [{'name': name, 'edited': name in edited} for name in prompt_files.names()]}

    def known(name: str) -> None:
        if name not in prompt_files.names():
            raise HTTPException(404, f'没有名为 {name} 的框架提示词')

    @app.get('/api/host/prompts')
    async def overview(_: str = Depends(user)):
        try:
            return summary()
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get('/api/host/prompts/{name}')
    async def detail(name: str, _: str = Depends(user)):
        known(name)
        default = prompt_files.bundled(name)
        edited = folder / name
        return {'name': name, 'default': default,
                'text': edited.read_text(encoding='utf-8') if edited.is_file() else default,
                'edited': edited.is_file(),
                'placeholders': sorted(Template(default).get_identifiers())}

    @app.put('/api/host/prompts/{name}')
    async def save(name: str, body: PromptText, _: str = Depends(user)):
        known(name)
        try:
            if body.text == prompt_files.bundled(name):
                prompt_files.reset(root, name)
            else:
                prompt_files.save(root, name, body.text)
            return summary()
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.delete('/api/host/prompts/{name}')
    async def reset(name: str, _: str = Depends(user)):
        known(name)
        prompt_files.reset(root, name)
        return summary()

    @app.put('/api/host/prompt-settings')
    async def settings(body: PromptSettings, _: str = Depends(user)):
        try:
            state = prompt_files.load_state(folder)
            prompt_files.save_state(folder, {**state, 'keep_on_update': body.keep_on_update})
            return summary()
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
