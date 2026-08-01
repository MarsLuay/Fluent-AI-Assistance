# Project Reader

Local, dependency-free tooling for reading Tecan FluentControl export archives
and script XML.

This is Layer 1, the Project Reader, in the Fluent AI-Assistance path:

1. Generate structured summaries from `.zeia`, `.xscr`, `.xcmp`, `.xwsp`,
   `.xlqc`, and `.gwl` files.
2. Let Codex use those summaries to explain scripts, compare exports, identify
   dependencies, and point out likely questions for human review.
3. Keep all work local. No API keys are needed.

## Quick Start

Preferred installed command: `project-reader`. The legacy `tecan-reader` alias
is still kept for compatibility. Module usage stays `python -m tecan_reader.cli`.

```powershell
python -m tecan_reader.cli inspect "C:\ProgramData\Tecan\VisionX\DataBase\UserSpecific\0035411b-0f50-47d8-90e4-69a2e577ce36.xscr" --format markdown
python -m tecan_reader.cli inspect "path\to\export.zeia" --format markdown -o ready-to-import/_shared/temp_files/build/export_report.md
python -m tecan_reader.cli compare "old.zeia" "new.zeia" --format markdown -o ready-to-import/_shared/temp_files/build/export_compare.md
python -m tecan_reader.cli index build "path\to\exports" --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --format markdown
python -m tecan_reader.cli index search "Water Free Single" --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --kind liquid_class
python -m tecan_reader.cli patterns mine --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --format markdown -o ready-to-import/_shared/temp_files/build/patterns.md
python -m tecan_reader.cli patterns search "MCA384TipBox" --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --type pick_up_tips
python -m unittest discover -s tests
```

## What It Extracts

For `.xscr` scripts:

- object name, checksum, workspace references, and script metadata
- command sequence and command family counts
- command IDs resolved through the shared command registry when known
- variables, query prompts, loop and conditional groups
- comments, liquid classes, labware mentions, device aliases, worklist mentions

For `.zeia` archives:

- internal file counts by extension
- script summaries for contained `.xscr` files
- object-name index for catalog-like XML files

For `.gwl` worklists:

- record counts and estimated transfer pairs

For multi-ZEIA project indexes:

- ZEIA files, scripts, worktables, labware, liquid classes, carrier candidates,
  device aliases, variables, worklists, command sequences, and dependencies
- searchable SQLite rows that preserve both the source ZEIA path and the
  internal archive path for each match

For reusable script-pattern libraries:

- mined patterns for `pick_up_tips`, `drop_tips`, `aspirate`, `dispense`,
  `mix`, `wash`, `prompt_user`, `loop_over_wells`, `read_worklist`,
  `move_plate`, `load_labware`, and `initialize_device`
- command-registry operation mappings, aliases, required fields, and manual
  step summaries for known FluentControl commands
- source script, ZEIA file, internal archive path, command range, numbered
  steps, command names, labware, liquid classes, volumes, worklists, device
  aliases, and safety notes for reuse

## Codex Workflow

Ask Codex to run a report first:

```powershell
python -m tecan_reader.cli inspect "<file-or-folder>" --format markdown -o ready-to-import/_shared/temp_files/build/report.md
```

For reuse across many exports, build a project index:

```powershell
python -m tecan_reader.cli index build "<folder-or-zeia-files>" --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --format markdown -o ready-to-import/_shared/temp_files/build/project_index.md
python -m tecan_reader.cli index search "<script-labware-liquid-class-or-command>" --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --format markdown
python -m tecan_reader.cli index summary --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --format markdown
```

Then mine reusable command structures:

```powershell
python -m tecan_reader.cli patterns mine --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --format markdown -o ready-to-import/_shared/temp_files/build/patterns.md
python -m tecan_reader.cli patterns search "<pattern-query>" --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --type aspirate --format markdown
python -m tecan_reader.cli patterns search "" --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --source-script "Old Assay Setup" --format markdown
```

Then ask questions against the report:

- What does this script appear to do?
- Which commands or modules does it rely on?
- Which labware/liquid classes should I verify in FluentControl?
- What changed between these two exports?
- Where has this command sequence or worklist been used before?
- Which script has the safest existing tip pickup, aspirate/dispense, or
  cleanup pattern to reuse?

The reader does not execute or import anything into FluentControl. It only reads
files and writes reports.
