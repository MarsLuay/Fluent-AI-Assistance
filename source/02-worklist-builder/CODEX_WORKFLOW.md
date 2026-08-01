# Codex Workflow For Worklist Builder

Use this folder as a local Codex workbench for `.gwl` worklist generation.

## Standard Loop

1. Read the SOP, notes, or user request.
2. Convert the protocol into `examples/<name>.csv` or `ready-to-import/_shared/temp_files/build/<name>.csv`.
3. Use the transfer CSV schema in `README.md`.
4. Run:

```powershell
python -m tecan_worklist.cli validate ready-to-import/_shared/temp_files/build/<name>.csv
python -m tecan_worklist.cli convert ready-to-import/_shared/temp_files/build/<name>.csv -o ready-to-import/_shared/temp_files/build/<name>.gwl
python -m tecan_worklist.cli summarize ready-to-import/_shared/temp_files/build/<name>.gwl
```

5. Show the user:
   - assumptions made,
   - row count and total volume,
   - worklist path,
   - any validation warnings.

## Rules For Codex

- Do not invent rack labels or rack types when the user gave specific ones.
- If labware names are unknown, keep them explicit in the CSV and flag them for
  human review.
- Prefer well addresses in CSV for readability; the CLI will convert them.
- Use `--wash-policy none` only when the FluentControl method handles tips/wash
  elsewhere.
- Do not claim a `.gwl` is robot-ready. Say it is generated and needs
  FluentControl review.
- Keep generated CSV files beside their output `.gwl` so the worklist remains
  explainable.

## Common Prompt Shape

```text
Create a transfer CSV for this Tecan worklist:
- Source labware/label:
- Destination labware/label:
- Wells:
- Volume:
- Liquid class:
- Tip/wash policy:

Then run validate, convert, and summarize.
```
