# Plugin interface v1

[中文](plugins-v1.md)

A plugin is a Python package containing `plugin.toml`, `__init__.py`, and exactly one `Plugin` subclass. Copy [counter](examples/counter/), or click Use this template on the [plugin template repository](https://github.com/lendevs/lenbot-plugin-template). Plugins run in the host process and share its Python environment.

```toml
name = "counter"
version = "1.0.0"
interface = 1
requires_lenbot = ">=0.1,<1"
requires_python = ">=3.13"
platforms = ["linux", "darwin", "win32"]
reload = "plugin"
authors = ["Plugin maintainer"]
license = "GPL-3.0-only"
description = "A separate counter for each scene"
```

All fields above are required. The installed directory must match `name`. Versions use Python packaging's version rules; compatibility ranges are explicit specifiers checked against the actual host and Python versions. Platform names are Python's `sys.platform` values. `win32` in a manifest does not imply a native Windows host release. `reload` is `plugin` or `host`. Optional fields are `repository`, `homepage`, `dependencies`, and `config`.

Interface 1 is the supported generation. Additions within it preserve existing signatures and behavior; breaking changes require a new generation. Public imports are `len_bot.next.plugin`, the message types it exposes from `len_bot.next.platform.messages`, and `len_bot.next.plugin_testing`. Internal host modules are not library APIs.

## Handlers and lifecycle

```python
from len_bot.next.plugin import Plugin, Invocation, command

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
| `tool(name, description)` | `ctx: Invocation`, typed named arguments | Model-discovered tool; must return a string |
| `background(every="5m")` | `ctx: PluginContext` | Wait after completion; minimum interval 10 seconds |

Methods are async and use one decorator each. Command, exact-text, then regex matching takes precedence; regex priority sorts descending. At most one handler consumes a message, without waking the chat model. Returning text records a result; sending requires `ctx.reply`. A model selecting a plugin tool and the tool itself calling a model are separate actions.

`start()` acquires resources; `stop()` releases them. Register background coroutines through `self.ctx.start_task(name, coroutine)`. The host cancels and waits for owned calls before stop. Keep blocking operations out of the event loop. A handler error ends that call and preserves the original error; it does not trigger automatic retries.

## Configuration and data

The panel builds a form from `[config.field]` declarations, so operators never write JSON. Each field needs `type` and `description`. A field with `default` is optional; otherwise required. Supported types: string (`multiline = true` for a text area), secret, integer, number, boolean, string_list (one item per line), `scene` (one configured group, picked by name), `scene_list` (several), `path` (absolute path), `url` (http or https) and object_list. Optional attributes: `label` (display name, defaults to the key), `group` (section heading, top-level fields only), `placeholder`, `options` for string/integer/number as plain values or `{ value = "auto", label = "Auto" }`, and `minimum`/`maximum` for numbers. Object lists can declare one level of child fields of any type except secret and object_list; each row is shown as a card. Undeclared object lists fall back to JSON editing. `path` and `url` may stay empty only when declared with `default = ""`. Scene values are checked against the host's configured scenes when saving and loading; `scene` cannot have a default and `scene_list` defaults only to `[]`. Unknown or invalid values are rejected.

Validated values are in `ctx.config`. Runtime configuration, enabled state and scenes live only in root `lenbot.config.json`. Secrets are masked in the panel; null preserves an existing secret when saving. Do not use KV or environment variables as alternate runtime configuration.

`ctx.get_kv`, `set_kv`, `delete_kv` operate on JSON business values in the plugin's own `data_dir/kv.sqlite3`. KV is isolated by plugin; include the scene in keys for per-scene data. Authors own file and KV formats. Publish explicit offline conversion instructions when a release changes data. Selecting older source does not undo data changes.

## Context capabilities

Scene IDs are qualified, for example `onebot:group:80001`; accounts use `onebot:70001`. `Invocation.message` contains the actual triggering sender; a tool or scheduled call may have no message. Never invent a requester.

| Capability | Public call |
| --- | --- |
| Send | `await ctx.reply(text)`, `reply_image(bytes, description)`, `reply_parts(parts)` |
| Message parts | `Text(text)`, `Image(bytes, description)`, `Mention("onebot:70001")` |
| History | `ctx.recent_messages(limit=20)`, current scene only, up to 100 |
| Memory | `await ctx.memory(arguments)` through the permitted scene service |
| Wake chat | `await ctx.emit_event(text)` |
| One model request | `await ctx.generate(prompt, role="mind", system=None)` |
| Work | `await ctx.delegate(goal, deliverable, context="", materials=())` |
| Time | `ctx.now()`, `ctx.timezone()` |
| Public HTTP bytes | `await ctx.fetch_public(url, timeout_seconds=10, max_bytes=10000000)` returns raw bytes using the host public-address checks and `network.fake_ip_networks`; each redirect hop is checked, total time and decoded size are bounded |
| Owner permission | `ctx.plugin.require_owner(ctx.scene, ctx.message.sender.uid)` |

`generate` uses an explicitly configured mind or learner binding, without tools, history injection, automatic sending or changing providers on error. `delegate` requires an actual non-self triggering message; the host uses its sender's task permissions. A returned task record confirms submission, not delivery.

`self.ctx` is a `PluginContext`; scene-specific capabilities require a scene argument. Calls are limited to enabled scenes. `self.ctx.scenes` lists enabled scenes whose chat is on: when an operator switches a group's chat off in the panel, that group drops out of the list, commands and notices there are not dispatched, and sending or emitting events to it raises an error. Sends return `Sent` with sent, failed, unconfirmed, simulated or partial status. Only sent means platform confirmation.

Register five-field cron jobs in start with `self.ctx.cron(name, expression, handler, scene=..., timezone=...)`. The handler receives an Invocation without a message. Use an IANA timezone. Restart calculates the next run from the current time and does not replay missed occurrences. Plugin `skills/<name>/SKILL.md` resources are read-only for workers and remain subject to persona skill permissions.

## Installation, updates and restart

Git uses complete HTTP(S) or ssh:// repository URLs and local Git credentials. The repository root is the plugin root. Select a branch, tag or commit; an omitted ref follows the recorded default branch. ZIP accepts files at archive root or inside a single top-level directory with a direct manifest. Paths, duplicate files, symlinks and a 200 MiB extracted limit are checked. ZIP preparation can be offline; dependency installation may need network access.

Both sources prepare a candidate without importing it, stopping the current plugin or changing dependencies. Save candidate configuration, then explicitly apply it. New plugins begin disabled; enable them and select scenes after source application. The panel shows installed, candidate and actual running versions separately.

Everything lives under the instance `plugins/` directory: managed source is `plugins/<name>`, staging is `plugins/.candidates/<name>`, source/application records are `plugins/.installations/<name>.json`, and plugin data defaults to `plugins/.data/<name>`. `plugins.paths` defaults to `["plugins"]`, so a plugin copied into `plugins/` by hand is discovered too. Builtin and manual directories are never taken over. Switching repositories or Git/ZIP source requires explicit source replacement. Git application rejects local changes, including untracked files except host-generated __pycache__.

If dependencies are unchanged and reload=plugin, applying stops only that plugin, switches files and reloads it. Dependency changes or reload=host require an explicit host restart. After clean host shutdown, the launcher runs `maintenance.apply_plugins` once with the instance lock: validate selected candidates, resolve combined requirements, switch source, then start the host. Existing installed packages constrain resolution; conflicts preserve the original error. Failure ends that launcher run, without automatic retry or environment rollback. Ordinary startup never consumes candidates.

For direct host runs, stop the instance, run `python -m len_bot.next.maintenance.apply_plugins` from its root, then explicitly start it. Rebuilt environments can restore installed plugin requirements with `maintenance.plugin_dependencies`. Use the target interpreter and uv.

Canceling a candidate preserves installed source. Canceling an unapplied first install removes its configuration entry. Disabling preserves configuration and data. Uninstall removes source and enabled configuration but retains data and shared packages. Data deletion is a separate action after the plugin stops.

The discovery page uses a [static catalog](plugin-catalog.md). The first release does not include a marketplace backend.

## Local tests and publishing

```python
from pathlib import Path
from len_bot.next.plugin_testing import PluginTest

async def check():
    async with PluginTest(Path("counter"), config={"step": 2}) as bot:
        assert await bot.message("计数加一")
        assert bot.deliveries[-1].text == "本群计数：2"
        result = await bot.tool("counter_read", {})
```

PluginTest runs real lifecycle, matching, tool validation, scene permissions and on-disk KV in a temporary installation. Specify scenes and owners in the constructor; message accepts scene and sender. Deliveries contain captured parts, reply_to and simulated status. events() exposes actual stored plugin events. Handler/start failures fail the call. Context exit stops the plugin and deletes its temporary installation.

The local harness provides no model, memory service or worker and raises an explicit error for those capabilities. A plugin's own external network calls still execute. Use an explicit test instance for service integration.

Publish a standalone repository containing source, manifest, documentation and a license. Release ZIPs contain the same root package. Set your own name and authors, update version and compatibility ranges, document configuration/data changes, and test the published package. The [plugin template](https://github.com/lendevs/lenbot-plugin-template) already includes a test workflow and a tag workflow that attaches an importable ZIP to each GitHub Release.

**License**: the LenBot host is AGPL-3.0-only; the plugin template and the counter example are GPL-3.0-only. Plugins run in the host process, so GPL-3.0 is the recommended plugin license; section 13 of GPLv3 permits combining GPLv3 works with AGPLv3 works. If you choose another license, check its compatibility with GPLv3 and AGPLv3 yourself.
