# Contributing

[中文](CONTRIBUTING.md)

Rules for code, tests, documentation and commits live in [AGENTS.md](AGENTS.md) (Chinese) and apply to people and tools alike. If you only want to write a plugin, start from the [plugin guide](developer/plugins-v1.en.md) or the [plugin template](https://github.com/lendevs/lenbot-plugin-template) instead.

## Local development

You need [uv](https://docs.astral.sh/uv/) and Node.js 22.

```sh
./scripts/install.sh       # uv sync and build the panel
uv sync --locked           # add the dev group when you want to run tests
uv run --no-sync len-bot   # start; opens the setup wizard if there is no configuration
```

The repository root is your local instance: `lenbot.config.json`, the database, `state/` and `personas/` live there and are ignored by Git. Development and local use share one checkout started with `uv`; there is no separate install.

After changing the frontend, run `npm run build` in `src/len_bot/web/frontend`. The output in `web/static/dist/` is not committed. `npm run dev` works for frontend work too.

### Trying things on a copy

To leave the data you use alone, stop the bot and make a test instance:

```sh
uv run --no-sync python -m len_bot.next.maintenance.test_copy . /tmp/lenbot-test --panel-port 8089
cd /tmp/lenbot-test && uv run --project /path/to/LenBot --no-sync len-bot
```

Only the configuration and the instance files it references (database, memory, personas, task directories and so on) are copied, not the source code. The copy uses simulated delivery, has no OneBot connection and listens on the given panel port; absolute paths pointing into the original instance are moved into the copy. Models, plugins and task services still run in the copy, and model calls still use tokens.

## Source layout

The runtime core is in `src/len_bot/next/`. The entry point is `len_bot.next.host`; the `len-bot` command starts it through `launcher.py`. See the [architecture overview (zh)](developer/architecture.md).

| Path (under `src/len_bot/next/`) | Responsibility |
|---|---|
| `host.py`, `launcher.py`, `instance_lock.py` | Host assembly, launcher, instance lock |
| `config.py`, `configuration/` | Root configuration and per-domain settings |
| `runtime/` | Platform and scene runtime, lifecycle, logging, retention |
| `platform/` | Platform identities, unified messages and the OneBot adapter |
| `chat/` | Scene sessions, attention, context, expression, tool dispatch, reminders |
| `models/` | Model requests, budgets and usage |
| `persona/`, `learning/` | Persona packages; learning phrasing, slang, stickers and reply effects |
| `memory/` | Local memory text, index, recall and background curation |
| `work/`, `browser/` | Background tasks (Pi containers) and browser cooperation |
| `storage/` | Database schema and codecs |
| `plugins/`, `builtin_plugins/` | Plugin runtime, installation and built-in plugins |
| `plugin.py`, `plugin_testing.py`, `text_cards.py`, `image_assets.py` | Public interfaces for plugins |
| `tools/`, `media/` | Web, skills, MCP and other tools; image and voice handling |
| `panel/` | Panel backend and the first-run wizard |
| `maintenance/` | Commands run while stopped: data upgrades, reindexing, plugin dependencies, storage pools, test copies |
| `trials/` | Isolated trial chats in the panel |

Elsewhere:

- `src/len_bot/prompts/`: prompts.
- `src/len_bot/builtin_skills/`: task skills.
- `src/len_bot/web/frontend/`: the Vue panel.
- `src/len_bot/eval/`: expression replay.
- `tests/`: tests.
- `docker/next-worker/`: the task image.
- `deploy/`: deployment material.
- `scripts/`: install and packaging scripts.
- `examples/`: the example persona and public replay cases.

Maintenance commands are run as `python -m len_bot.next.maintenance.<module>`. Import from concrete modules; `__init__.py` files do not re-export.

## Tests

```sh
uv run --no-sync pytest -q
uv run --no-sync python -m compileall -q src/len_bot
```

Tests cover external protocol boundaries, data migrations, permissions and configuration validation only; see [AGENTS.md](AGENTS.md#测试与报告).

When changing prompts or persona expression, compare replies before and after with the [expression replay](examples/replay/). When you find a bad reply, write the situation down as a replay case before changing prompts.

## Data format

The business database starts from public baseline v1. To change the schema, create the new structure directly in `storage/schema.py`, add an upgrade step from the previous version to `UPGRADES` in `maintenance/migrate.py`, and bump `FORMAT_VERSION` in `storage/store.py`. Upgrades run only from the maintenance command while stopped; the runtime has no old/new compatibility branches.

## Build and release

```sh
uv run --no-sync python scripts/build_release.py /tmp/lenbot-release
```

This rebuilds the panel in a copy and produces the sdist, the wheel and the Linux/macOS packages, without starting or uploading anything. Releases go through the [Release workflow](.github/workflows/release.yml); see the [release guide (zh)](deploy/releasing.md).

[CI](.github/workflows/ci.yml) compiles, runs tests, builds the panel and packages. Passing CI does not mean panel interactions or real chats were checked.

## Submitting changes

- Use the [bug template](.github/ISSUE_TEMPLATE/bug.md) and the [PR template](.github/PULL_REQUEST_TEMPLATE.md).
- Run `git diff --check` before committing. Never commit real configuration, databases, credentials, personal personas or build output.
- Commit messages say what behavior changed, what you checked and what is still unconfirmed.

## License

Code contributed to this repository is licensed under [AGPL-3.0-only](LICENSE); `developer/examples/counter/` and the plugin template are GPL-3.0-only. Sources and licenses of third-party material are listed in [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md); update it when you bring in third-party code or assets.
