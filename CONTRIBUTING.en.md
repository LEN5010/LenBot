# Contributing

[中文](CONTRIBUTING.md)

If you only want to write a plugin, you can skip this page and start from the [plugin guide](developer/plugins-v1.en.md) or the [plugin template](https://github.com/lendevs/lenbot-plugin-template) instead.

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
| `plugins/` | Plugin runtime and installation; business plugins live in separate repositories |
| `tools/`, `media/` | Web, skills, MCP and other tools; image and voice handling |
| `panel/` | Panel backend and the first-run wizard |
| `maintenance/` | Commands run while stopped: data upgrades, reindexing, plugin dependencies, storage pools, test copies |
| `trials/` | Isolated trial chats in the panel |

Elsewhere:

- `src/len_bot/plugin.py`, `plugin_testing.py`, `text_cards.py`, `image_assets.py`: public interfaces for plugins; plugins import only from these.
- `src/len_bot/prompts/`: prompts.
- `src/len_bot/builtin_skills/`: task skills.
- `src/len_bot/web/frontend/`: the Vue panel.
- `src/len_bot/eval/`: expression replay.
- `tests/`: tests.
- `docker/next-worker/`: the task image.
- `deploy/`: deployment material; `deploy/updater/` is the updater shared by packages and Docker.
- `website/`: documentation site (VitePress); preview with `cd website && npm ci && npm run dev`.
- `changelogs/`: one release notes file per version.
- `scripts/`: install and packaging scripts.
- `examples/`: the example persona and public replay cases.

Maintenance commands are run as `python -m len_bot.next.maintenance.<module>`. Import from concrete modules; `__init__.py` files do not re-export.

## Writing code

- Solve the problem at hand. Do not swallow exceptions and return defaults, guess field names, or check for cases the types already rule out. When a model or service call fails, do not automatically retry with another model, service or parameters.
- Parse external data (platform messages, model and service responses, configuration files) once at the entry point. If parsing fails, raise an error that includes the raw snippet.
- Catch exceptions only at the boundary of one chat turn, one tool or plugin call, or one task; log the original error and end that unit. Tool errors go back to the model verbatim.
- Leave contextual judgement (who a request belongs to, what a reference points to, who is replying to whom) to the model. The host keeps only real identities and the state needed to execute. When you add a table, a state or a layer, explain in the PR what problem it solves.
- Log through a module `logging.getLogger(__name__)` and record events with `log_event(logger, 'event', **fields)` from `runtime/logs.py`; do not `print`. Pass exceptions as `error=`; database error columns keep one line `Type: message` (`error_text`). New long-running entry points (background jobs, external callbacks) bind correlation IDs with `log_context`.
- Runtime settings come only from `lenbot.config.json` in the repository root and are saved by the panel while running. Do not add overrides through environment variables, dotenv, command-line flags or the database.

## Tests

```sh
uv run --no-sync pytest -q
uv run --no-sync python -m compileall -q src/len_bot
uv run --no-sync ruff check src/len_bot scripts deploy/updater deploy/package
```

Tests cover only external protocol boundaries (parsing OneBot, Pi RPC, model and memory service responses), data migrations, permissions and configuration validation, using anonymized real samples. Do not mock call sequences, test private functions or snapshot prompts.

When changing prompts or persona expression, compare replies before and after with the [expression replay](examples/replay/). When you find a bad reply, write the situation down as a replay case before changing prompts, rather than piling prohibitions into the prompt. General situations go in `examples/replay/`; cases taken from real group chats stay on your machine and are not committed.

## Data format

The business database starts from public baseline v1. To change the schema, create the new structure directly in `storage/schema.py`, add an upgrade step from the previous version to `UPGRADES` in `maintenance/migrate.py`, and bump `FORMAT_VERSION` in `storage/store.py`. The memory job database, local memory index and root configuration work the same way; their numbers are listed in the [release guide (zh)](deploy/releasing.md#兼容编号). Upgrades run only from the maintenance command while stopped; the runtime has no old/new compatibility branches.

## Build and release

```sh
uv run --no-sync python scripts/build_release.py /tmp/lenbot-release
```

This rebuilds the panel in a copy and produces the sdist, the wheel, the Linux/macOS/Windows packages and the release manifest, without starting or uploading anything. Releases go through the [Release workflow](.github/workflows/release.yml); see the [release guide (zh)](deploy/releasing.md).

[CI](.github/workflows/ci.yml) compiles, runs tests, builds the panel and packages. Passing CI does not mean panel interactions or real chats were checked.

## Submitting changes

- Use the [bug template](.github/ISSUE_TEMPLATE/bug.md) and the [PR template](.github/PULL_REQUEST_TEMPLATE.md).
- Run `git diff --check` before committing. Never commit real configuration, databases, credentials, personal personas or build output.
- Commit messages say what behavior changed, what you checked and what is still unconfirmed.
- When behavior or interfaces change, update the matching docs in the README, `developer/` or `deploy/`.
- Authors are people. Do not add tool or model attribution to commits or PRs, such as `Co-Authored-By:` lines pointing to machine identities or `Generated with …` footers.

## License

Code contributed to this repository is licensed under [AGPL-3.0-only](LICENSE); the plugin template is GPL-3.0-only. Sources and licenses of third-party material are listed in [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md); update it when you bring in third-party code or assets.
