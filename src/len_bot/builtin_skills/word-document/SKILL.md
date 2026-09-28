---
name: word-document
description: Create or edit DOCX documents while checking document structure, retained content, and final file integrity.
---

# Word documents

Use this for `.docx` deliverables or edits to an existing Word file. Keep the source intact and write the finished document under `/workspace/out`.

- Inspect headings, tables, images, headers/footers, and any existing comments or revisions before choosing an edit route. Pandoc is installed for straightforward Markdown–DOCX conversion; it is not a fidelity-preserving editor for every existing layout.
- For structured edits to an existing DOCX, install `python-docx` explicitly if needed (`uv run --no-project --with python-docx python3 ...`). Preserve unrelated sections and check how the chosen library handles the document's actual features; do not silently drop unsupported comments or tracked changes.
- After writing, reopen the DOCX and verify the requested paragraphs, table cells, image references, and document order. If exact visual layout matters, use an actually available renderer; a valid DOCX archive alone does not prove page layout.
- Deliver the final `/workspace/out/*.docx` through `deliver_file(path, name, note)`. Registration is separate from any later platform upload.
