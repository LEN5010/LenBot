# Counter plugin template

[中文](README.md)

These files are prepared for a standalone GitHub template repository; that public repository has not been created. Copy this directory's contents to your repository root. Set your own manifest name, author, version and license, and rename commands/tools to avoid collisions. The current example uses AGPL-3.0-only.

No model or external service is required. `/计数` reads this scene's count, `计数加一` adds the configured step, and `/计数清零` clears it. The counter_read tool returns JSON without sending a message. Source updates preserve per-scene KV data.

Install the host and pytest/pytest-asyncio, then run `uv run --no-sync pytest -q tests`. CI installs a specified host source revision and uses the public PluginTest harness. Sends are captured as simulated; QQ and models are not contacted. Plugins using external services need explicit service integration tests.

The host panel installs this standalone Git repository directly. Prepare the candidate, save configuration, apply it, then enable it and select scenes. The v* tag workflow creates an importable ZIP and attaches it to a GitHub Release. It packages plugin source and documentation, without instance data.

Before publishing, select a fixed host revision you tested in CI, update the manifest version and tag that commit. Document configuration and data migrations. Downgrading source does not undo data changes. The host repository's developer/plugins-v1.en.md documents the public interface.
