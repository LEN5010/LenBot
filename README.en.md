<img src="src/len_bot/web/frontend/src/assets/lenbot-mark-tile.svg" width="64" height="64" alt="LenBot">

# LenBot

[中文](README.md) · [Documentation (zh)](https://lendevs.github.io/LenBot/) · [Quick start (zh)](https://lendevs.github.io/LenBot/guide/quick-start)

LenBot is a chat bot that stays in your QQ groups for the long run. It sends and receives QQ messages through OneBot v11 and thinks and talks with a language model of your choice.

You don't talk to it through commands. When people chat, it decides for itself whether to join in, whom to answer and whether a sticker says it better. When someone asks for a report or some research, it hands the job to a background task and posts the result when it is done. Personas, memory, plugins, models and permissions are all managed in a web panel.

<img src="website/public/screenshots/home.png" alt="Panel home" width="860">

## What it does

- **Chats like a group member.** Each group has one continuing conversation; older parts are condensed into a recap as it grows. It quotes, mentions people and sends stickers, and can be set to chime in without being called.
- **Has a persona.** Profile, voice, boundaries, examples, background notes and stickers live in one persona package. The default persona, Xiaoran (小然), works out of the box, or you can write your own. Try changes in a test chat before saving them.
- **Remembers the group.** Memory is plain Markdown on your disk, kept separately per group. You can read and edit it in the panel, or make it forget something completely.
- **Takes on longer jobs.** Reports and research run as background tasks in a Docker container, so the chat doesn't wait. You can ask about progress, add instructions or cancel.
- **Uses tools.** Web search and reading, images, voice transcription, forwarded messages, member info, and MCP servers.
- **Runs plugins.** Plugins are Python and can add commands, scheduled jobs and tools for the model. Official plugins: group summaries, a GSUID Core bridge, A-SOUL and Bilibili.
- **Learns from the group.** It picks up common phrasing, slang and stickers; everything it learns can be reviewed, edited or turned off in the panel.

## What it doesn't do

- QQ is the only platform for now.
- It doesn't log in to QQ itself. Run a OneBot implementation such as [NapCat](https://github.com/NapNeko/NapCatQQ) alongside it.
- It doesn't ship a model. Bring your own provider and API key: OpenAI-compatible, OpenAI Responses, Anthropic and Gemini are supported.
- It understands voice messages but only replies in text.
- Prompts and the panel are Chinese only.

## Quick start

The first public version, 0.2.0, is still being prepared. Until packages and images are published, run from source. You need:

- [uv](https://docs.astral.sh/uv/) and Node.js 22
- a OneBot implementation that is logged in to QQ
- the base URL and API key of a model provider

```sh
git clone https://github.com/lendevs/LenBot.git
cd LenBot
./scripts/install.sh
uv run --no-sync len-bot
```

On first start the terminal prints a link:

```text
尚无根配置。请打开 http://127.0.0.1:52811/#token=...
```

Open it and go through the six steps of the setup wizard: admin account, QQ connection, owner, model, persona and first group, official plugins. After you save, LenBot starts and the page moves on to the panel by itself.

For the first run, set replies to simulated delivery: replies only show up in the panel. Chat with it on the test page, and when you are happy, switch delivery to QQ under Settings → Connection.

The full walkthrough is in the [quick start (zh)](https://lendevs.github.io/LenBot/guide/quick-start).

## Ways to install

All three run the same program:

| Option | Good for |
|---|---|
| [Release package (zh)](https://lendevs.github.io/LenBot/guide/install-package) | Running on your own computer or a Linux server. Needs only uv, comes with service scripts, upgrades from the panel and can roll back a failed upgrade |
| [Docker (zh)](https://lendevs.github.io/LenBot/guide/install-docker) | Servers and NAS boxes; also the way to get background tasks on Windows |
| [Source (zh)](https://lendevs.github.io/LenBot/guide/install-source) | Changing the code or following development |

Once 0.2.0 is out, packages will be on [GitHub Releases](https://github.com/lendevs/LenBot/releases) and images at `ghcr.io/lendevs/lenbot`, mirrored on Docker Hub.

## Documentation

Most documentation is in Chinese. English versions exist for this README, [CONTRIBUTING](CONTRIBUTING.en.md), the [deployment overview](deploy/README.en.md), the [plugin guide](developer/plugins-v1.en.md) and the [plugin template](https://github.com/lendevs/lenbot-plugin-template).

| To | See |
|---|---|
| Install, set up, use day to day | [Documentation site (zh)](https://lendevs.github.io/LenBot/) |
| Pick a deployment, add optional services | [Deployment](deploy/README.en.md) |
| Maintain personas, plugins and tasks | [Operations (zh)](deploy/current/operations.md) |
| Write a plugin | [Plugin guide](developer/plugins-v1.en.md), [template](https://github.com/lendevs/lenbot-plugin-template) |
| Write a persona | [Personas (zh)](developer/personas.md), [Xiaoran's package](examples/personas/companion/) |
| Understand the internals | [Architecture (zh)](developer/architecture.md) |
| Contribute | [CONTRIBUTING](CONTRIBUTING.en.md) |

Draft release notes are in [changelogs](changelogs/).

## License

Original code is licensed under [AGPL-3.0-only](LICENSE); see [NOTICE](NOTICE). Third-party dependencies and separate services keep their own licenses, see [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md).
