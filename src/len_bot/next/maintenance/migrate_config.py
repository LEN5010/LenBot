"""Offline upgrade of root (and trial) configuration files from prices to token limits.

Run while stopped, before the database migrations, which read the configuration.
Money limits cannot be turned into token counts, so a configured one stops the
upgrade with its value and the file is left unchanged.
"""

from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile

from ..instance_lock import instance_lock
from ..config import CONFIG_VERSION


def _without_prices(source: dict, path: Path) -> dict:
    result = json.loads(json.dumps(source))
    models = result["models"]
    models.pop("prices", None)
    for binding in models["roles"].values():
        if binding is not None:
            binding.pop("price", None)
    limits = result.get("limits")
    if limits is not None:
        configured = {key: limits[key] for key in ("daily_model_cost", "scene_daily_model_cost")
                      if limits.get(key) not in (None, {})}
        if configured:
            raise ValueError(f"{path}: money limits cannot become token limits; set limits.daily_tokens / "
                             f"scene_daily_tokens by hand and remove {json.dumps(configured, ensure_ascii=False)}")
        for key in ("currency", "daily_model_cost", "scene_daily_model_cost"):
            limits.pop(key, None)
    worker = result.get("worker")
    if worker is not None and "max_cost" in worker:
        if worker["max_cost"] is not None:
            raise ValueError(f"{path}: worker.max_cost {worker['max_cost']!r} cannot become a token limit; "
                             "set worker.max_tokens by hand and remove max_cost")
        del worker["max_cost"]
    return result


def migrate_config(path: Path) -> bool:
    """Rewrite one configuration file atomically; keep the original beside it. Returns whether it changed."""
    source = json.loads(path.read_text(encoding="utf-8"))
    format_version = source.get('config_version', 0)
    if format_version not in (0, CONFIG_VERSION):
        raise ValueError(f'{path}: unsupported configuration format {format_version}; target={CONFIG_VERSION}')
    upgraded = _without_prices(source, path)
    tokens_changed = upgraded != source
    upgraded['config_version'] = CONFIG_VERSION
    if upgraded == source:
        return False
    backup = path.with_name(path.name + ('.pre-tokens.bak' if tokens_changed else '.pre-config-v0.bak'))
    with backup.open("x", encoding="utf-8") as copy:
        copy.write(path.read_text(encoding="utf-8"))
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(upgraded, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return True


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("Configuration migration takes no arguments; stop the instance and run from its root")
    root = Path.cwd()
    with ExitStack() as locks:
        locks.enter_context(instance_lock(root))
        trials = sorted((root / ".runtime" / "chat-tests").glob("*/lenbot.config.json"))
        for path in trials:
            locks.enter_context(instance_lock(path.parent))
        for path in (root / "lenbot.config.json", *trials):
            changed = migrate_config(path)
            print(f"{path}: {'prices removed; original kept as ' + path.name + '.pre-tokens.bak' if changed else 'already current'}")


if __name__ == "__main__":
    main()
