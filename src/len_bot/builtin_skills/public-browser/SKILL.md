---
name: public-browser
description: Interact with dynamic public web pages in this task's anonymous browser using the configured Playwright CLI.
---

# Public browser

Use `lenbot-browser` for public pages that need interaction. It uses this task's anonymous browser session and configured public network proxy; having this skill loaded does not mean a particular site is reachable.

- Run `lenbot-browser open https://example.com`, then `lenbot-browser snapshot`. Read the JSON snapshot before choosing an actual element reference such as `e4`; use a fresh snapshot after navigation or a substantial page change. Examples: `lenbot-browser fill e4 "search text"`, `lenbot-browser click e5`.
- Attach task files to an observed file input with `lenbot-browser attach-files e4 /inputs/brief.pdf /workspace/out/table.csv`. If clicking a button opens a file chooser, use `lenbot-browser upload /inputs/brief.pdf` instead. `attached` means file selection completed; inspect the page and submit separately when the task calls for it.
- To download from an observed link or button, use `lenbot-browser download e5` **instead of clicking it first**. This installs the download wait before one click, waits for the bytes, and preserves the browser's filename in a unique `out/browser/download-*` directory. This command captures a download from the current tab; select the relevant tab before using it. For a site-specific multi-step interaction, continue using the native page controls and inspect its actual files rather than repeating the action.
- `lenbot-browser screenshot` and `lenbot-browser pdf` save into their own `out/browser/` directories. Optional explicit names also belong there: `lenbot-browser screenshot --filename=out/browser/proof.png`. An existing explicit filename is not overwritten. Screenshot supports an observed element ref, `--full-page`, `--hires`, and `--type`.
- File commands return `status`, `files`, actual names/sizes/MIME, page source, and a task resource `reference` for saved outputs. They appear in the task resource browser for preview or further work. Call `deliver_file` with the returned `/workspace/...` path to create an independent delivery; that registration is not a QQ upload. Ordinary native clicks may also produce downloads, but their text is not a completed file-command receipt.
- Read the returned JSON, including `isError`: an exit code of 0 alone does not establish success. If an operation's result is uncertain, observe the page again before deciding what happened; do not silently switch browser backends or retry a mutation.
- Run `lenbot-browser close` when finished. Its cookies and profile belong only to this task and do not provide account access. If the page requires login or a verification step, ask for human help through the task's existing interaction tools rather than claiming an anonymous session is logged in.
