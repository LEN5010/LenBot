"""Framework prompts: the bundled files, optionally replaced per instance from the panel.

Edited copies live in ``state/prompts/`` of the instance. ``.state.json`` there records the LenBot
version the edits were made against and whether they survive an upgrade; without that permission a
new version moves them aside and the bundled prompts of the new version apply again. Edits are read
once at startup, so a saved change applies after the next restart, the same as the root config.
"""

from __future__ import annotations

import json
import logging
import time
from functools import cache
from pathlib import Path
from string import Template

from .runtime.logs import log_event
from .runtime.releases import current_version

BUNDLED = Path(__file__).resolve().parents[1] / "prompts"
STATE = ".state.json"
logger = logging.getLogger(__name__)
_running: dict[str, str] = {}


def directory(root: Path) -> Path:
    return root / "state" / "prompts"


@cache
def names() -> list[str]:
    return sorted(path.name for path in BUNDLED.glob("next_*.md"))


def bundled(name: str) -> str:
    if name not in names():
        raise KeyError(f"没有名为 {name} 的框架提示词")
    return (BUNDLED / name).read_text(encoding="utf-8")


def read_prompt(name: str) -> str:
    """The prompt text in effect: the instance's edited copy loaded at startup, otherwise the bundled file."""
    return _running[name] if name in _running else bundled(name)


def edited(folder: Path) -> list[str]:
    return [name for name in names() if (folder / name).is_file()]


def _read_edits(folder: Path) -> dict[str, str]:
    return {name: (folder / name).read_text(encoding="utf-8") for name in edited(folder)}


def pending(root: Path) -> bool:
    """Whether the saved edits differ from the ones this process started with."""
    return _read_edits(directory(root)) != _running


def load_state(folder: Path) -> dict:
    file = folder / STATE
    if not file.exists():
        return {"version": current_version(), "keep_on_update": False}
    state = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(state.get("version"), str) or not isinstance(state.get("keep_on_update"), bool):
        raise ValueError(f"{file} 格式不对：需要字符串 version 和布尔值 keep_on_update")
    return state


def _write(file: Path, text: str) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(file.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(file)


def save_state(folder: Path, state: dict) -> None:
    _write(folder / STATE, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def check(name: str, text: str) -> None:
    """Reject an edit that would fail when the host fills in the template."""
    if not text.strip():
        raise ValueError("提示词不能为空；要用默认内容请恢复默认")
    expected = Template(bundled(name)).get_identifiers()
    if not expected:
        return  # the host reads this file as plain text, so `$` has no special meaning
    template = Template(text)
    if not template.is_valid():
        raise ValueError("有无法识别的 $ 写法；要写字面的 $ 请写成 $$")
    found = template.get_identifiers()
    missing = [f"${item}" for item in expected if item not in found]
    unknown = [f"${item}" for item in found if item not in expected]
    if missing or unknown:
        raise ValueError("占位符必须与默认内容一致" + (f"，缺少 {'、'.join(missing)}" if missing else "")
                         + (f"，不认识 {'、'.join(unknown)}" if unknown else ""))


def save(root: Path, name: str, text: str) -> None:
    check(name, text)
    folder = directory(root)
    state = load_state(folder)
    _write(folder / name, text)
    save_state(folder, {**state, "version": current_version()})


def reset(root: Path, name: str) -> None:
    bundled(name)
    (directory(root) / name).unlink(missing_ok=True)


def activate(root: Path) -> None:
    """Load this instance's edits for the process, first applying the upgrade rule when the version changed."""
    global _running
    folder = directory(root)
    _running = {}
    changed = edited(folder)
    if not changed:
        return
    state = load_state(folder)
    version = current_version()
    if state["version"] == version or state["keep_on_update"]:
        if state["version"] != version:
            log_event(logger, "prompt_overrides_kept", "框架提示词的修改基于旧版本，按设置继续使用",
                      level=logging.WARNING, edited_version=state["version"], version=version, prompts=changed)
        _running = _read_edits(folder)
        return
    archive = folder / ".replaced" / f"{state['version']}-{time.strftime('%Y%m%d-%H%M%S')}"
    archive.mkdir(parents=True)
    for name in changed:
        (folder / name).replace(archive / name)
    save_state(folder, {**state, "version": version})
    log_event(logger, "prompt_overrides_replaced", "版本已更新，框架提示词恢复为新版本的默认内容",
              level=logging.WARNING, edited_version=state["version"], version=version, prompts=changed,
              archive=str(archive))
