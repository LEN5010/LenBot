---
name: data-analysis
description: Analyze supplied datasets and produce reproducible calculations, tables, and concise findings tied to the data.
---

# Data analysis

Use this when the task asks for conclusions from data rather than only file conversion. Save reusable analysis output and the requested report under `/workspace/out`.

- Inspect the actual schema, units, date range, missingness, and duplicate keys. Record which rows and columns enter each calculation; do not silently turn missing observations into zero.
- Prefer Python standard-library `csv`, `json`, `sqlite3`, and `statistics` for modest data. Install `pandas` or plotting packages with `uv run --no-project --with ...` only when their capabilities are needed; they are not image defaults.
- Check calculations with independent totals, counts, or a small hand-worked sample. Separate measured values from assumptions and explain filters, denominators, and uncertainty in the report.
- Include the requested table/chart and enough method detail to reproduce the result. Deliver final artifacts from `/workspace/out` through `deliver_file(path, name, note)`; registration does not imply QQ delivery.
