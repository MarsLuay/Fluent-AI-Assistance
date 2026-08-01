# Worklist Builder

Local, dependency-free tooling for generating Tecan Gemini/Fluent `.gwl`
worklists from structured transfer CSV files.

This is Layer 2, the Worklist Builder, in the Fluent AI-Assistance path:

1. Codex helps turn an SOP or transfer idea into a transfer CSV.
2. This tool validates the CSV and writes a `.gwl`.
3. A human reviews the worklist and loads it from an existing FluentControl
   script/worklist command.

No API keys are needed. The AI workflow is Codex reading and editing files in
this folder, then running the CLI locally.

## Quick Start

From this folder:

Preferred installed command: `worklist-builder`. The legacy `tecan-worklist`
alias is still kept for compatibility. Module usage stays
`python -m tecan_worklist.cli`.

```powershell
python -m tecan_worklist.cli convert examples/simple_transfer.csv -o ready-to-import/_shared/temp_files/build/simple_transfer.gwl
python -m tecan_worklist.cli summarize ready-to-import/_shared/temp_files/build/simple_transfer.gwl
python -m unittest discover -s tests
```

Generated output for the simple example:

```text
C;Simple 4-well transfer
A;SourcePlate;;96 Well Flat;1;;20;Water Free Single;;;
D;DestPlate;;96 Well Flat;1;;20;Water Free Single;;;
W;
```

## Transfer CSV Schema

Required columns:

```text
source_label,source_type,source_position,dest_label,dest_type,dest_position,volume_ul
```

Optional columns:

```text
liquid_class,source_id,dest_id,source_tube_id,dest_tube_id,tip_mask,
forced_source_type,forced_dest_type,comment,wash_after,break_after
```

Positions may be numeric Tecan positions or well addresses such as `A1`,
`H12`, or `P24`. For 96-well plates, the default mapping is column-major:
`A1=1`, `B1=2`, ..., `H1=8`, `A2=9`.

## Commands

```powershell
# Convert transfer CSV to GWL
python -m tecan_worklist.cli convert examples/simple_transfer.csv -o ready-to-import/_shared/temp_files/build/simple_transfer.gwl

# Validate CSV without writing a GWL
python -m tecan_worklist.cli validate examples/simple_transfer.csv

# Print record counts from a GWL
python -m tecan_worklist.cli summarize ready-to-import/_shared/temp_files/build/simple_transfer.gwl
```

Useful conversion options:

```powershell
--wash-policy each    # default: add W; after each transfer
--wash-policy none    # no automatic wash records
--batch-size 8        # add B; after each batch of 8 transfers
--well-rows 16        # default row count for address conversion
--strict              # treat validation warnings as errors
```

## Safety Boundary

This tool creates worklists, not complete FluentControl methods. It does not
verify deck layout, liquid classes, labware availability, instrument state, tip
strategy, or FluentControl context-check results. Treat every generated `.gwl`
as a draft until reviewed in FluentControl/simulation by a qualified user.
