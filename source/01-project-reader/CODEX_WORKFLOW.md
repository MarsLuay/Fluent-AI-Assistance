# Codex Workflow For Project Reader

Use this folder when you want Codex to understand Tecan files before changing
or generating anything.

## Standard Loop

1. Generate a report:

```powershell
python -m tecan_reader.cli inspect "<path>" --format markdown -o ready-to-import/_shared/temp_files/build/report.md
```

2. Ask Codex to answer questions from `ready-to-import/_shared/temp_files/build/report.md`.
3. If comparing exports:

```powershell
python -m tecan_reader.cli compare "<old.zeia>" "<new.zeia>" --format markdown -o ready-to-import/_shared/temp_files/build/compare.md
```

4. If reusing scripts across many exports, build a project index:

```powershell
python -m tecan_reader.cli index build "<folder-or-zeia-files>" --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --format markdown -o ready-to-import/_shared/temp_files/build/project_index.md
python -m tecan_reader.cli index search "<query>" --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --format markdown
python -m tecan_reader.cli index summary --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --format markdown
```

5. Mine reusable script patterns from that index:

```powershell
python -m tecan_reader.cli patterns mine --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --format markdown -o ready-to-import/_shared/temp_files/build/patterns.md
python -m tecan_reader.cli patterns search "<query>" --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --type aspirate --format markdown
python -m tecan_reader.cli patterns search "" --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --source-script "<source script name>" --format markdown
```

6. Keep generated reports and indexes in `ready-to-import/_shared/temp_files/build/` (shared tooling scratch under ready-to-import).

## Rules For Codex

- Treat summaries as clues, not proof that a script is safe or runnable.
- Do not import `.zeia` files or copy `.xscr` files into FluentControl unless
  the user explicitly asks.
- When a report shows unknown or opaque commands, call them out.
- Prefer exact names from the report for labware, liquid classes, worklists,
  scripts, and workspaces.
- For multi-ZEIA questions, prefer the project index so matches include the
  ZEIA file, script name, command number, and internal archive path.
- For reuse requests, prefer mined script patterns. Cite the pattern type,
  source script, command range, numbered steps, and specifications instead of
  inventing a FluentControl command sequence.
- If the report is too large, rerun with `--script-limit` or inspect one script.

## Useful Commands

```powershell
# Installed UserSpecific scripts
python -m tecan_reader.cli inspect "C:\ProgramData\Tecan\VisionX\DataBase\UserSpecific" --format markdown -o ready-to-import/_shared/temp_files/build/userspecific.md

# One archive
python -m tecan_reader.cli inspect "C:\path\to\export.zeia" --format json -o ready-to-import/_shared/temp_files/build/export.json

# One script from the installed database
python -m tecan_reader.cli inspect "C:\ProgramData\Tecan\VisionX\DataBase\UserSpecific\<guid>.xscr" --format markdown

# Search across many exported projects
python -m tecan_reader.cli index build "C:\path\to\zeia_exports" --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --format markdown -o ready-to-import/_shared/temp_files/build/project_index.md
python -m tecan_reader.cli index search "LihaAspirateCommand" --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --kind command

# Mine reusable patterns from the project index
python -m tecan_reader.cli patterns mine --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --format markdown -o ready-to-import/_shared/temp_files/build/patterns.md
python -m tecan_reader.cli patterns search "MCA384TipBox" --db ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite --type pick_up_tips
python -m tecan_reader.cli patterns types --format markdown
```
