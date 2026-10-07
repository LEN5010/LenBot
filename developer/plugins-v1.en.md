# Plugin interface v1

[中文](plugins-v1.md)

A plugin is a Python package containing `plugin.toml`, `__init__.py`, and exactly one `Plugin` subclass. Start by clicking Use this template on the [plugin template repository](https://github.com/lendevs/lenbot-plugin-template). Plugins run in the host process and share its Python environment.

```toml
name = "counter"
version = "1.0.0"
interface = 1
requires_lenbot = ">=0.2,<1"
requires_python = ">=3.13"
platforms = ["linux", "darwin", "win32"]
reload = "plugin"
authors = ["Plugin maintainer"]
license = "GPL-3.0-only"
description = "A separate counter for each scene"
```

All fields above are required. The installed directory must match `name`. Versions use Python packaging's version rules; compatibility ranges are explicit specifiers checked against the actual host and Python versions. Platform names are Python's `sys.platform` values; host packages exist for Linux (`linux`), macOS (`darwin`) and Windows (`win32`). `reload` is `plugin` or `host`. Optional fields are `repository`, `homepage`, `dependencies`, `config`, and `data_version` (a positive integer, default 1, numbering the shape of the plugin's data directory and KV; see Data versions).

Interface 1 is the supported generation and is frozen from 0.2.0: later changes only add (new optional parameters, methods, optional manifest fields) and never change existing signatures or behavior; a breaking change requires a new generation. Public imports are `len_bot.plugin` (including the `ChatMessage`, `Notice`, `Sender` and `Segment` message types it exports), `len_bot.plugin_testing`, plus the `len_bot.image_assets` and `len_bot.text_cards` helpers documented below. Modules under `len_bot.next` are internal and not library APIs.

## Handlers and lifecycle

```python
from len_bot.plugin import Plugin, Invocation, command

class Example(Plugin):
    @command("hello", "Reply without a model call")
    async def hello(self, ctx: Invocation, args: str):
        await ctx.reply(f"Hello {args}")
```

| Decorator | Handler arguments after self | Behavior |
| --- | --- | --- |
| `command(name, description)` | `ctx: Invocation, args: str` | `/name` and remaining arguments |
| `fullmatch(text, description)` | `ctx: Invocation` | Exact plain text after trimming |
| `regex(pattern, description, priority=0)` | `ctx: Invocation, match` | Full regular expression match |
| `on_notice(type)` | `ctx: Invocation, notice` | Platform notice type or type.sub_type |
| `tool(name, description, summary=..., needs_source=False)` | `ctx: Invocation`, typed named arguments | Model-discovered tool; text or native JSON result |
| `background(every="5m")` | `ctx: PluginContext` | Wait after completion; minimum interval 10 seconds |

Methods are async and use one decorator each. Command, exact-text, then regex matching takes precedence; regex priority sorts descending. At most one handler consumes a message, without waking the chat model. Returning text records a result; sending requires `ctx.reply`. A model selecting a plugin tool and the tool itself calling a model are separate actions.

`start()` acquires resources; `stop()` releases them. Register background coroutines through `self.ctx.start_task(name, coroutine)`. The host cancels and waits for owned calls before stop. Keep blocking operations out of the event loop. A handler error ends that call; the host writes the original error to the run log (`plugin_error`) and does not retry. Use `self.ctx.log` (a standard `logging.Logger`) for the plugin's own records; they land in the host's `logs/lenbot.jsonl` with the plugin name and the current scene, turn and tool call IDs, and the panel log page filters by plugin.

## Configuration and data

The panel builds a form from `[config.field]` declarations, so operators never write JSON. Each field needs `type` and `description`. A field with `default` is optional; otherwise required. Supported types: string (`multiline = true` for a text area), secret, integer, number, boolean, string_list (one item per line), `scene` (one configured group, picked by name), `scene_list` (several), `path` (absolute path), `url` (http or https) and object_list. Optional attributes: `label` (display name, defaults to the key), `group` (section heading, top-level fields only), `placeholder`, `options` for string/integer/number as plain values or `{ value = "auto", label = "Auto" }`, and `minimum`/`maximum` for numbers. Object lists can declare one level of child fields of any type except secret and object_list; each row is shown as a card. Undeclared object lists fall back to JSON editing. `path` and `url` may stay empty only when declared with `default = ""`. Scene values are checked against the host's configured scenes when saving and loading; `scene` cannot have a default and `scene_list` defaults only to `[]`. Unknown or invalid values are rejected.

Validated values are in `ctx.config`. Runtime configuration, enabled state and scenes live only in root `lenbot.config.json`. Secrets are masked in the panel; null preserves an existing secret when saving. Do not use KV or environment variables as alternate runtime configuration.

`ctx.get_kv`, `set_kv`, `delete_kv` operate on JSON business values in the plugin's own `data_dir/kv.sqlite3`. KV is isolated by plugin; include the scene in keys for per-scene data. ### Data versions

The host records `.lenbot-data.json` in the data directory with the `data_version` the data was written with; existing data without it counts as 1, and a new empty directory takes the manifest's current version. When the manifest's `data_version` is higher than the record, before `start()` the host copies the whole data directory to `plugins.data_directory/.backups/<name>-v<old>-<time>`, calls `await plugin.migrate_data(from_version)` (the plugin is loaded and may use `ctx.data_dir` and KV, but has not started), then records the new version. If the migration raises, the data directory is put back as it was and the plugin is marked failed. Raising `data_version` without implementing `migrate_data` fails. A manifest `data_version` lower than the record is refused: an older plugin never reads newer data, so going back also needs the matching data backup.

## Context capabilities

Scene IDs are qualified, for example `onebot:group:80001`; accounts use `onebot:70001`. `Invocation.message` contains the actual triggering sender; a tool or scheduled call may have no message. Never invent a requester.

| Capability | Public call |
| --- | --- |
| Send | `await ctx.reply(text)`, `reply_image(bytes, description)`, `reply_parts(parts)` |
| Message parts | `Text(text)`, `Image(bytes, description)`, `Mention("onebot:70001")` |
| History | `ctx.recent_messages(limit=20)`, current scene only, up to 100 |
| Time range | `ctx.messages_between(after, before, offset=0, limit=200)`, Unix times with `after <= time < before`, oldest first, up to 500 per page; page with offset |
| Memory | `await ctx.memory(arguments)` through the permitted scene service |
| Wake chat | `await ctx.emit_event(text)` |
| One model request | `await ctx.generate(prompt, role="mind", system=None)` |
| Work | `await ctx.delegate(goal, deliverable, context="", materials=())` |
| Time | `ctx.now()`, `ctx.timezone()` |
| Owner permission | `ctx.require_owner()` |

`generate` uses an explicitly configured mind or learner binding, without tools, history injection, automatic sending or changing providers on error. `delegate` requires an actual non-self triggering message; the host uses its sender's task permissions. A returned task record confirms submission, not delivery.

`self.ctx` is a `PluginContext`; scene-specific capabilities require a scene argument. Calls are limited to enabled scenes. `self.ctx.scenes` lists enabled scenes whose chat is on: when an operator switches a group's chat off in the panel, that group drops out of the list, commands and notices there are not dispatched, and sending or emitting events to it raises an error. Sends return `Sent` with sent, failed, unconfirmed, simulated or partial status. Only sent means platform confirmation.

Register five-field cron jobs in start with `self.ctx.cron(name, expression, handler, scene=..., timezone=...)`. The handler receives an Invocation without a message. Use an IANA timezone. Restart calculates the next run from the current time and does not replay missed occurrences. Plugin `skills/<name>/SKILL.md` resources are read-only for workers and remain subject to persona skill permissions.

## Installation, updates and restart

Git uses complete HTTP(S) or ssh:// repository URLs and local Git credentials. The repository root is the plugin root. Select a branch, tag or commit; an omitted ref follows the recorded default branch. ZIP accepts files at archive root or inside a single top-level directory with a direct manifest. Paths, duplicate files, symlinks and a 200 MiB extracted limit are checked. ZIP preparation can be offline; dependency installation may need network access.

Both sources prepare a candidate without importing it, stopping the current plugin or changing dependencies. Save candidate configuration, then explicitly apply it. New plugins begin disabled; enable them and select scenes after source application. The panel shows installed, candidate and actual running versions separately.

Everything lives under the instance `plugins/` directory: managed source is `plugins/<name>`, staging is `plugins/.candidates/<name>`, source/application records are `plugins/.installations/<name>.json`, and plugin data defaults to `plugins/.data/<name>`. `plugins.paths` defaults to `["plugins"]`, so a plugin copied into `plugins/` by hand is discovered too. Builtin and manual directories are never taken over. Switching repositories or Git/ZIP source requires explicit source replacement. Git application rejects local changes, including untracked files except host-generated __pycache__.

If dependencies are unchanged and reload=plugin, applying stops only that plugin, switches files and reloads it. Dependency changes or reload=host require an explicit host restart. After clean host shutdown, the launcher runs `maintenance.apply_plugins` once with the instance lock: validate selected candidates, resolve combined requirements, switch source, then start the host. Existing installed packages constrain resolution; conflicts preserve the original error. Failure ends that launcher run, without automatic retry or environment rollback. Ordinary startup never consumes candidates.

For direct host runs, stop the instance, run `python -m len_bot.next.maintenance.apply_plugins` from its root, then explicitly start it. Rebuilt environments can restore installed plugin requirements with `maintenance.plugin_dependencies`. Use the target interpreter and uv.

Applying a new version moves the replaced source to `plugins/.previous/<name>`; the panel can go back to it once. If the new version fails to load, start or migrate its data, the host returns to the previous source automatically; if the new version had already migrated the data, the pre-migration backup is restored too. Going back by hand from the panel changes only the source, not the data. Canceling a candidate preserves installed source. Canceling an unapplied first install removes its configuration entry. Disabling preserves configuration and data. Uninstall removes source and enabled configuration but retains data and shared packages. Data deletion is a separate action after the plugin stops.

The discovery page uses a [static catalog](plugin-catalog.md). The first release does not include a marketplace backend.

## Local tests and publishing

```python
from pathlib import Path
from len_bot.plugin_testing import PluginTest

async def check():
    async with PluginTest(Path("counter"), config={"step": 2}) as bot:
        assert await bot.message("计数加一")
        assert bot.deliveries[-1].text == "本群计数：2"
        result = await bot.tool("counter_read", {})
```

PluginTest runs real lifecycle, matching, tool validation, scene permissions and on-disk KV in a temporary installation. Specify scenes and owners in the constructor; message accepts scene and sender. Deliveries contain captured parts, reply_to and simulated status. events() exposes actual stored plugin events. Handler/start failures fail the call. Context exit stops the plugin and deletes its temporary installation. To test a data migration, pass the old data: `PluginTest(package, data=Path('tests/data-v1'), data_version=1)` runs `migrate_data` before `start()` as a real upgrade does.

The local harness disables model calls by default; explicitly pass models for protocol tests through the host request and usage path. Memory and worker calls raise an explicit error. A plugin's own external network calls still execute.

Publish a standalone repository containing source, manifest, documentation and a license. Release ZIPs contain the same root package. Set your own name and authors, update version and compatibility ranges, document configuration/data changes, and test the published package. The [plugin template](https://github.com/lendevs/lenbot-plugin-template) calls LenBot's reusable workflows: `plugin-ci.yml` tests and packages the plugin against host `master` by default (pass `host_ref` to pin), and `plugin-release.yml` attaches an importable ZIP to each `v*` tag after checking the tag matches the manifest version.

**License**: the LenBot host is AGPL-3.0-only; the plugin template and the counter example are GPL-3.0-only. Plugins run in the host process, so GPL-3.0 is the recommended plugin license; section 13 of GPLv3 permits combining GPLv3 works with AGPLv3 works. If you choose another license, check its compatibility with GPLv3 and AGPLv3 yourself.

## Model-facing tool contract (unreleased 0.2.0 development)

Use `@tool(name, description, summary="...", needs_source=False)`. The explicit summary appears in discovery and search, while the full description and `Annotated[..., Field(description=..., examples=...)]` schema accompany the callable tool. Without a summary the full description remains unchanged. README and docstrings are not loaded automatically.

An optional `[model] instructions = "prompts/tools.md"` names a package-relative shared guide. It is loaded once after an allowed tool is discovered, refreshed on plugin reload and withdrawn on disable, removal or discovery reset after compaction.

Tools return text or native JSON values; the host serializes non-string results. Tuples, arbitrary objects, non-string dictionary keys and non-finite floats are invalid. Explain whether a result is data, an actual delivery receipt or background work started. Only a `sent` receipt confirms platform delivery.

`needs_source=True` adds a host-owned required `source_message_id`. Select the actual request message from this scene's records; the host populates `Invocation.message` from that message. Do not declare this parameter in the handler or provide an arbitrary owner account. Use `ctx.require_owner()` and `ctx.delegate()` with that actual sender.

Override synchronous `Plugin.unavailable_tools(scene)` to return tool names and configuration reasons. Unavailable tools remain visible in the panel preview but are excluded from discovery and execution. Keep the actual caller checks in the handler; never include credentials in reasons or guides.

Use `await ctx.fetch_image(url, timeout_seconds=15)` for verified public image bytes under host network/image limits. `image_assets` validation, limits and OriginalImage, plus `text_cards` CardSection, CardPage and TextCards are public helpers. Render expensive images with `asyncio.to_thread`.

`PluginTest.preview_tools()` shows the model-facing summaries, schemas, shared guides and availability. Inject a clock with `now=...`, save a source with `add_message(...)`, and explicitly configure `models=...` only for model protocol tests. Sending stays simulated. The template includes native JSON lookup and background generation/delivery examples. See the [Chinese guide](plugins-v1.md) for full examples and compatibility details.

Group related search/detail operations into a single capability using strict discriminated request models; keep data queries separate from sending and account writes. Model schemas omit generated titles while preserving property names, descriptions, examples and constraints. Check context size as well as tool count. Development continues without a version tag or Release.
