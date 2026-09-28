---
name: pdf
description: Create, inspect, extract, or revise PDF files for a task, including page-level checks before delivery.
---

# PDF

Use this when the deliverable or source is a PDF. Work on a copy in `/workspace`; place final files in `/workspace/out`.

- First identify whether the job needs editable source, text extraction, page manipulation, or a final print layout. Keep the supplied original unchanged.
- For a new document, HTML/CSS printed with the installed Chromium is a practical path. Check the available `chromium` command and inspect its actual output; the image and font set is not a guarantee of pagination. For existing PDFs, install a parser only if needed, for example `uv run --no-project --with pypdf python3 ...`.
- Check the resulting page count, order, extractable text, and whether the requested figures/tables appear. If visual fidelity matters, render representative PDF pages with an explicitly installed renderer such as `pypdfium2`; inspecting only source HTML or a successful command exit does not check the final pages.
- When a scan has no text layer, report that fact rather than inventing extracted text. OCR requires an actually available dependency and a check against the page image.
- Deliver the verified PDF from `/workspace/out` with `deliver_file(path, name, note)`. Its result means a host copy was registered, not that QQ received it.
