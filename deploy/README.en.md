# Deployment

[中文](README.md)

LenBot can be run in three ways. All three run the same program and use the same kind of instance directory. Step-by-step guides for new users are on the [documentation site (zh)](https://lendevs.github.io/LenBot/).

| Option | Suited for | Requires | Guide |
|---|---|---|---|
| Release package | Day-to-day use on Linux, macOS or Windows | uv | [Package (zh)](package/README.md) |
| Docker | Servers, NAS, or Windows when background tasks are needed | Docker | [Docker (zh)](current/docker.md) |
| Source | Development, or using the latest code on the main branch | uv, Node.js 22 | below |

With any option, the first start prints a link to a local web wizard. The wizard has six steps:

1. Panel account and port.
2. Connect OneBot and read the bot's own account from the platform (the connection must work first).
3. The owner's QQ number and the time zone.
4. The chat model, which must pass a test call.
5. A persona (Xiaoran by default) and the first group or private chat.
6. Official plugins, which you can skip.

After saving, LenBot starts and the page switches to the panel. For the first run, choose simulated delivery, try a few messages in the panel's chat test, and switch to real delivery once the replies look right. Each step is explained in [first setup (zh)](https://lendevs.github.io/LenBot/guide/first-setup) on the documentation site.

## The instance directory

The instance directory holds everything for one bot:

| What | Where |
|---|---|
| Runtime configuration (with secrets) | `lenbot.config.json` |
| Chat database and memory job database | `database` from the configuration, plus the matching `.memory.sqlite3` |
| Persona packages | `personas/<persona id>/` |
| Memory, logs, plugin data, task directories | under `state/` and wherever the configuration points |

`lenbot.config.json` is the only runtime configuration; environment variables and command-line arguments are not read. While LenBot is running, change settings in the panel, which shows a notice when a restart is needed. Stop LenBot before editing the file by hand.

To back up, stop LenBot and copy the whole instance directory, plus any task directories outside it that the configuration points to.

## Running from source

```sh
git clone https://github.com/lendevs/LenBot.git
cd LenBot
./scripts/install.sh
uv run --no-sync len-bot
```

When running from source, the repository checkout is the instance directory.

Package and Docker installs include an updater, so upgrades are done from the panel and a failed upgrade can be restored there; see [update and restore (zh)](https://lendevs.github.io/LenBot/guide/update). A source checkout has no updater and is updated by hand:

```sh
# stop the bot first (Ctrl-C)
git pull
./scripts/install.sh
uv run --no-sync python -m len_bot.next.maintenance.migrate_config
uv run --no-sync python -m len_bot.next.maintenance.migrate
uv run --no-sync python -m len_bot.next.maintenance.migrate_memory_jobs
uv run --no-sync python -m len_bot.next.maintenance.migrate_local_memory
uv run --no-sync python -m len_bot.next.maintenance.plugin_dependencies
uv run --no-sync python -m len_bot.next.maintenance.doctor
uv run --no-sync len-bot
```

Back up the instance before upgrading. The maintenance commands run in this order:

1. `migrate_config` upgrades the root configuration.
2. `migrate` upgrades the business database.
3. `migrate_memory_jobs` upgrades the memory job database.
4. `migrate_local_memory` upgrades the local memory index.
5. `plugin_dependencies` restores the dependencies of installed plugins.
6. `doctor` checks the result. Start LenBot once every line reports `ok` or `disabled`.

Steps that are already current change nothing. Upgrading the local index from format 2 to 3 clears old derived summaries and keeps the source text and change history; the summaries are regenerated the next time memory curation is explicitly run.

On macOS, LenBot can also be started by double-clicking [`current/start.command`](current/start.command). For running as a system service, see [optional services (zh)](current/README.md#作为系统服务运行).

## Optional capabilities

For chat alone, OneBot and one chat model are enough. The following can be added as needed; see [optional services (zh)](current/README.md).

- Vector memory search, using an embeddings service such as Ollama
- Voice transcription, using local [ASR](current/asr.md)
- Background tasks, which need Docker and the task image, optionally with a [storage pool](current/task-storage.md)
- Account browsing, which needs the [browser components](browser/README.md)

Daily operation and the maintenance of personas, plugins and tasks are covered in [operations (zh)](current/operations.md). Releasing a new version is described in the [release guide (zh)](releasing.md).
