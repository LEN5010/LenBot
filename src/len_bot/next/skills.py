"""Load explicit Agent Skills metadata for one host or task snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import stat
from typing import Literal

import yaml


BUILTIN_DIRECTORY = Path(__file__).resolve().parents[1] / "builtin_skills"
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class _FrontmatterLoader(yaml.SafeLoader):
    pass


# Pi's YAML parser treats words such as "on" and "no" as valid skill names,
# while PyYAML's YAML 1.1 boolean resolver would turn them into bool values.
_FrontmatterLoader.yaml_implicit_resolvers = {
    initial: [(tag, resolver) for tag, resolver in entries if tag != "tag:yaml.org,2002:bool"]
    for initial, entries in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_FrontmatterLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$", re.IGNORECASE), list("tTfF"),
)


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    source: Literal["builtin", "shared", "scene", "task", "plugin"]
    host_path: Path
    container_path: str
    disable_model_invocation: bool


def load_skill(path: Path, source: Literal["builtin", "shared", "scene", "task", "plugin"],
               container_path: str) -> Skill:
    file = path / "SKILL.md"
    if file.is_symlink() or not stat.S_ISREG(file.stat().st_mode):
        raise ValueError(f"{file}: SKILL.md must be a regular file, not a symbolic link")
    data = file.read_bytes()
    try:
        content = data.decode("utf-8").removeprefix("\ufeff")
    except UnicodeDecodeError as error:
        fragment = data[max(0, error.start - 30):error.end + 30]
        raise ValueError(f"{file}: invalid UTF-8: {error}; raw={fragment!r}") from error
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError(f"{file}: missing YAML frontmatter; raw={content[:200]!r}")
    closing = next((index for index, line in enumerate(lines[1:], 1)
                    if line.rstrip("\r\n") == "---"), None)
    if closing is None:
        raise ValueError(f"{file}: unclosed YAML frontmatter; raw={content[:200]!r}")
    raw_header = "".join(lines[1:closing])
    try:
        metadata = yaml.load(raw_header, Loader=_FrontmatterLoader)
    except yaml.YAMLError as error:
        raise ValueError(f"{file}: invalid YAML frontmatter: {error}; raw={raw_header[:200]!r}") from error
    if not isinstance(metadata, dict):
        raise ValueError(f"{file}: frontmatter must be an object; raw={raw_header[:200]!r}")
    name = metadata.get("name")
    description = metadata.get("description")
    disabled = metadata.get("disable-model-invocation", False)
    if (not isinstance(name, str) or len(name) > 64 or _NAME.fullmatch(name) is None
            or name != path.name):
        raise ValueError(f"{file}: name must match its directory and use 1..64 lowercase letters, "
                         f"digits or single hyphens; raw={raw_header[:200]!r}")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise ValueError(f"{file}: description must be nonblank text of at most 1024 characters; "
                         f"raw={raw_header[:200]!r}")
    if type(disabled) is not bool:
        raise ValueError(f"{file}: disable-model-invocation must be boolean; "
                         f"raw={raw_header[:200]!r}")
    return Skill(name, description, source, path, container_path, disabled)


def _directory(root: Path, source: Literal["builtin", "shared", "scene", "task", "plugin"],
               container_root: str, *, missing_ok: bool,
               public_browser: bool = False) -> tuple[Skill, ...]:
    if root.resolve(strict=False) != root:
        raise ValueError(f"{root}: skill directory must not traverse a symbolic link")
    try:
        children = sorted(root.iterdir(), key=lambda child: child.name)
    except FileNotFoundError:
        if missing_ok:
            return ()
        raise
    skills = []
    for child in children:
        if source == "builtin" and child.name == "public-browser" and not public_browser:
            continue
        if child.is_symlink():
            raise ValueError(f"{child}: skill directory must not be a symbolic link")
        if child.is_dir():
            skills.append(load_skill(child, source, f"{container_root}/{child.name}"))
    return tuple(skills)


def _unique(skills: tuple[Skill, ...]) -> None:
    names: dict[str, Path] = {}
    for skill in skills:
        previous = names.get(skill.name)
        if previous is not None:
            raise ValueError(f"Skill name {skill.name!r} is duplicated: {previous} and {skill.host_path}")
        names[skill.name] = skill.host_path


def load_catalog(directory: Path | None, scene: str, *, public_browser: bool = False) -> tuple[Skill, ...]:
    if directory is None:
        return ()
    catalog = (
        *_directory(BUILTIN_DIRECTORY, "builtin", "/shared/skills/builtin",
                    missing_ok=False, public_browser=public_browser),
        *_directory(directory / "shared", "shared", "/shared/skills/approved", missing_ok=True),
        *_directory(directory / "scenes" / scene, "scene", "/group/skills", missing_ok=True),
    )
    _unique(catalog)
    return catalog


def select_skills(catalog: tuple[Skill, ...], allowed: Literal["all"] | list[str]) -> tuple[Skill, ...]:
    _unique(catalog)
    if allowed == "all":
        return catalog
    if len(allowed) != len(set(allowed)):
        raise ValueError(f"Role skill names must not repeat: {allowed!r}")
    available = {skill.name: skill for skill in catalog}
    unknown = [name for name in allowed if name not in available]
    if unknown:
        raise ValueError(f"Role skills are not in this scene's catalog: {unknown!r}")
    return tuple(available[name] for name in allowed)


def load_plugin_skills(directory: Path, plugin: str) -> tuple[Skill, ...]:
    return _directory(directory, "plugin", f"/shared/skills/plugins/{plugin}", missing_ok=True)


def load_task_skills(task_workspace: Path) -> tuple[Skill, ...]:
    return _directory(task_workspace / "skills", "task", "/workspace/skills", missing_ok=True)


def merge_task_skills(selected: tuple[Skill, ...], task_skills: tuple[Skill, ...]) -> tuple[Skill, ...]:
    combined = selected + task_skills
    _unique(combined)
    return combined
