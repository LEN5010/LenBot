---
name: public-browser
description: Interact with dynamic public web pages in this task's anonymous browser using the configured Playwright CLI.
---

# Public browser

Use `lenbot-browser` for public pages that need interaction. It uses this task's anonymous browser session and configured public network proxy; having this skill loaded does not mean a particular site is reachable.

- Run `lenbot-browser open https://example.com`, then `lenbot-browser snapshot`. Read the JSON snapshot before choosing an actual element reference such as `e4`; use a fresh snapshot after navigation or a substantial page change. Examples: `lenbot-browser fill e4 "search text"`, `lenbot-browser click e5`.
- For a screenshot that should become a task file, run `lenbot-browser screenshot --filename=out/browser-proof.png` (relative to `/workspace`). The default browser artifacts and downloads are under `/workspace/out/browser`; inspect the actual file and wait for a download to finish before calling `deliver_file`. A registered delivery file is not a QQ upload.
- Read the returned JSON, including `isError`: an exit code of 0 alone does not establish success. If an operation's result is uncertain, observe the page again before deciding what happened; do not silently switch browser backends or retry a mutation.
- Run `lenbot-browser close` when finished. Its cookies and profile belong only to this task and do not provide account access. If the page requires login or a verification step, ask for human help through the task's existing interaction tools rather than claiming an anonymous session is logged in.
