---
name: web-research
description: Research public web sources reachable from a task, distinguishing retrieved evidence from unavailable or dynamic pages.
---

# Web research

Use this for claims that need current public sources. The image has `curl` and `jq`; a browser-control service or search API is not implicitly available. Public HTTP(S) access depends on this task's configured proxy and limits.

- Begin with the user's URLs or identifiable primary sources. Fetch the actual page or document with `curl`, check the final URL, status, and retrieval date, and follow relevant source links deliberately. Do not infer a page's contents from a title or search snippet.
- Compare publication date with event date for time-sensitive claims. Keep a compact source list: URL, publisher, date, and which claim the page supports. Prefer primary records for precise figures, rules, and technical details.
- If the page requires JavaScript or access fails, record the failure and use another explicitly retrieved source only if it answers the same question. Do not claim that Chromium's mere presence supplies an automated browser workflow.
- Write the requested answer or report under `/workspace/out` with links beside supported claims. If delivering a file, use `deliver_file(path, name, note)`; that registers a copy, not a QQ upload.
