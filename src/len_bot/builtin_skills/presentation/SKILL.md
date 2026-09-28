---
name: presentation
description: Produce or revise PPTX slide decks with source-backed content and checks of slide structure and assets.
---

# Presentations

Use this for a slide deck deliverable, not for a prose document with slide-like headings. Write the final `.pptx` to `/workspace/out`.

- Start from the requested audience, purpose, source material, and visual constraints. Keep factual claims traceable to the supplied material or an actually read source; use concise slide text and preserve important charts or quotations.
- Pandoc is installed and can create a simple PPTX from Markdown. For controlled layouts, install a presentation library such as `pptxgenjs` in the task workspace only when needed; do not assume Node packages are preinstalled.
- Open or inspect the generated deck with an available parser to check slide count, titles, media, and speaker notes. Actual appearance needs a renderer; if none is available, state that visual layout was not rendered rather than calling the deck visually verified.
- Deliver the final file using `deliver_file(path, name, note)`. A registered task file is not a platform upload.
