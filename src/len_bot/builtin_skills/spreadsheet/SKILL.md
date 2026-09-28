---
name: spreadsheet
description: Build, edit, or analyze CSV and XLSX files with checks for data types, formulas, ranges, and requested outputs.
---

# Spreadsheets

Use Python's `csv` module for plain CSV. For XLSX work, install `openpyxl` explicitly when needed, for example `uv run --no-project --with openpyxl python3 ...`; it is not guaranteed to be in the image.

- Inspect sheet names, dimensions, header rows, number/date formats, formulas, merged ranges, and missing cells before changing a supplied workbook. Keep the original file unchanged.
- Make transformations against explicit column names and units. Check row counts, key uniqueness where relevant, totals, and representative rows after saving and reopening the result.
- `openpyxl` writes formulas but does not calculate them. Do not present missing cached formula values as zero or as freshly verified results; calculate independently when the task needs numeric answers.
- Put the finished CSV/XLSX in `/workspace/out`, with a short note explaining any unverified recalculation or visual formatting. Use `deliver_file(path, name, note)` to register the copy; this is not a QQ receipt.
