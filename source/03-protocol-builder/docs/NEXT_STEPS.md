# Next Steps Toward AI-Assisted Fluent Scripting

## 1. Build A Local Script Corpus

Collect safe copies of existing `.zeia`, `.xscr`, `.gwl`, and related Fluent
files by importing each `.zeia` into its own project context:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli import-project "<project.zeia>" --name project-name
.\.venv\Scripts\python.exe -m fluent_pipeline.cli project-info project-name
.\.venv\Scripts\python.exe -m fluent_pipeline.cli project-find "96 Well" --context project-name
```

The goal is to give Codex a growing local knowledge base of real script
patterns, deck layouts, liquid classes, labware names, and failure modes.

Status on 2026-07-13: source-controlled safe fixtures live in
`corpus/local_corpus_manifest.json`. Generated project artifacts belong under
`ready-to-import/<project>/temp_files/` and are not corpus fixtures.

Status on 2026-06-07: fixture-based diagnostic QA started with
`tests/test_diagnostics.py`. The `diagnose` command now imports real ZEIA files,
selects a failing script, exports protocol IR, checks worktable/context gaps,
flags missing GWL references, spots unsupported commands, and correlates
optional FluentControl error text.

## 2. Fix Catalog And Worktable Gaps

Initial alias maps now live in `config/aliases/` for catalog, labware,
liquid-class, and device-name mismatches found during roundtrips. Example from
the current test:

```text
Plexiglas Pane[002] -> local catalog only has Plexiglas Pane
```

Use `alias-list`, `alias-resolve`, and `alias-normalize-ir` to inspect and apply
those decisions. Next, expand these maps from real ZEIA imports and add a
protocol-builder command that suggests likely new replacements.

Status on 2026-06-07: alias maps include reviewed import evidence for
`EVA[001] -> EVA` and
`Instrument=1/Device=MCA384:1 -> MCA384`; corpus tests assert the default maps
resolve those values.

## 3. Harden Repeated Workflow Templates

Initial reusable IR shapes now live in `templates/` for:

- plate-to-plate transfer
- normalization to target concentration
- serial dilution
- reagent addition
- magnetic bead cleanup
- worklist execution
- tip handling and wash patterns

Next, connect each template to project-specific source patterns, add known-good
compiled `.xscr` drafts where available, and record simulation reports for the
example request specs.

## 4. Add Validation Gates

Make every script pass these gates before it is treated as usable:

- decompile or generate Python draft
- simulate with full or expected coverage
- run `repair-plan` and review catalog/modeling suggestions
- compile to `.xscr`
- inspect the compiled `.xscr` with the project-reader layer
- copy final user-facing artifacts into `ready-to-import/`
- manually load and validate in FluentControl

Keep deployment manual until the validation story is much stronger.

## 5. Make Codex The Authoring Loop

Use Codex as the local extension-style AI:

```text
Read this request spec, the relevant template, and the latest simulation report.
Update protocol.ir.json first, regenerate artifacts, read validation_diff.md,
and run simulation. If it fails, explain the failure and make one repair
attempt. Do not deploy.
```

This keeps the AI useful without giving it direct control over the instrument or
requiring API keys.
