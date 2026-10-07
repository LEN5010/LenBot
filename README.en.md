<img src="src/len_bot/web/frontend/src/assets/lenbot-mark-tile.svg" width="64" height="64" alt="LenBot">

# LenBot

[中文](README.md) · [Documentation (zh)](https://lendevs.github.io/LenBot/) · [Quick start (zh)](https://lendevs.github.io/LenBot/guide/quick-start)

LenBot is a chat agent that lives in your QQ groups. It connects to QQ through OneBot v11, is written in Python with asyncio and SQLite, and ships with a web control panel.

<img src="website/public/screenshots/home.png" alt="Panel home" width="860">

## How it is designed

LenBot is organized around the chat scene. Every group (or private chat) has one long-lived agent session:

- Messages from members, notifications from plugins and progress from background tasks all arrive in the same runtime and are handled by that session.
- The agent decides when to speak, what to say, whether to send a sticker, and whether to hand a long job to a background task.
- The host keeps track of what actually happened: how far a task has got, whether a reply was really sent, whether a file really arrived.

Exact commands and keyword rules are still handled directly by plugins, without the model. But the agent is what coordinates the conversation, rather than a feature that some command happens to call.

## Features

| Area | What you get |
|---|---|
| Chat | One persistent session per scene; decides whether and to whom to reply; stickers, quotes and mentions; long conversations are compacted into a recap |
| Personas | Persona packages with identity, voice, boundaries, examples, knowledge and stickers; import, export, and trial-chat changes before adopting them |
| Memory | Local Markdown, isolated per scene; full-text and optional vector search; background curation, editing, deletion and full forgetting |
| Background tasks | Long jobs run with Pi in an isolated Docker container; follow-up questions, cancellation and resumption; registered outputs can be sent to the chat |
| Tools | Web search and reading, image viewing, voice transcription, forwarded messages, member info, MCP, browser tasks |
| Plugins | In-process Python plugins with commands, rules, schedules, tools and skills; install from Git or ZIP, version checks, per-plugin reload |
| Learning | Learns phrasing, slang and stickers from the group and watches how people react to replies; everything can be adopted, edited or disabled in the panel |
| Management | The panel manages models, budgets, permissions, reminders and logs; owners can also change settings by asking in chat |

Group summaries, the GSUID Core bridge, A-SOUL and Bilibili are [standalone plugins (zh)](developer/plugin-examples.md), installed and updated separately.

## Quick start

The first public version, 0.2.0, is still being prepared and no packages or images have been published yet, so run from source for now. You need [uv](https://docs.astral.sh/uv/) and Node.js 22:

```sh
git clone https://github.com/lendevs/LenBot.git
cd LenBot
./scripts/install.sh      # install dependencies and build the panel; does not start
uv run --no-sync len-bot  # prints the setup wizard link on first run
```

Open the link and follow the wizard: connect OneBot and read the bot account, then enter the owner, a model, a persona and the first group. After saving you land in the panel. The default persona is Xiaoran (小然), with a ready-made profile, voice, knowledge and stickers; it is copied into your instance, so edit it freely.

Start with **simulated delivery**: the bot receives messages and decides what to say, but nothing is sent to QQ, so you can trial-chat in the panel first. Model calls are billed either way.

The checkout itself is the instance directory: configuration, database, personas and runtime data live in the repository root and are excluded by `.gitignore`. `lenbot.config.json` is the only runtime configuration. It contains secrets, so do not commit it. Change settings in the panel while running, and stop the bot before editing the file by hand.

The full walkthrough is in the [quick start (zh)](https://lendevs.github.io/LenBot/guide/quick-start).

## Ways to run it

All three run the same program:

| Option | Good for | Notes |
|---|---|---|
| Release package | Day-to-day use on Linux, macOS or Windows | Only needs uv; includes service control; upgrade from the panel and restore if it fails. See [package (zh)](https://lendevs.github.io/LenBot/guide/install-package) |
| Docker | Servers, NAS, or Windows when you need background tasks | Instance data lives in named volumes; upgrades also run from the panel. See [Docker (zh)](https://lendevs.github.io/LenBot/guide/install-docker) |
| Source | Development, tracking the main branch | See above and [CONTRIBUTING](CONTRIBUTING.en.md) |

Where downloads will be once 0.2.0 is out:

- Packages: `lenbot-<version>-linux.tar.gz`, `-macos.tar.gz` and `-windows.zip` on [GitHub Releases](https://github.com/lendevs/LenBot/releases).
- Images: `ghcr.io/lendevs/lenbot`, `lenbot-updater` and `lenbot-worker`, mirrored as `docker.io/lendevs/...` on Docker Hub, for amd64 and arm64.

## Documentation

Most documentation is in Chinese. English versions exist for this README, [CONTRIBUTING](CONTRIBUTING.en.md), the [deployment overview](deploy/README.en.md), the [plugin guide](developer/plugins-v1.en.md) and the [plugin template](https://github.com/lendevs/lenbot-plugin-template).

| Task | Where |
|---|---|
| Install, first setup, daily use | [Documentation site (zh)](https://lendevs.github.io/LenBot/) |
| Choose a deployment, optional services | [Deployment](deploy/README.en.md) |
| Maintaining personas, plugins and tasks | [Operations (zh)](deploy/current/operations.md) |
| Internal structure | [Architecture (zh)](developer/architecture.md) |
| Write a plugin | [Plugin guide](developer/plugins-v1.en.md), [template](https://github.com/lendevs/lenbot-plugin-template) |
| Contribute, build releases | [CONTRIBUTING](CONTRIBUTING.en.md) |
| Write a persona | [Personas (zh)](developer/personas.md), [example persona](examples/personas/companion/) |

## Status

0.2.0 is the first public version and is still being prepared; the draft release notes are in [changelogs](changelogs/). OneBot (QQ) is the only platform adapter, prompts and the panel are Chinese only, and there is no text-to-speech.

## License

Original code is licensed under [AGPL-3.0-only](LICENSE); see [NOTICE](NOTICE). Third-party dependencies and separate services keep their own licenses, see [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md).
