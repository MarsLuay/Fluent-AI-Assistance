# Protocol Builder

Layer 3 of the Fluent AI-Assistance workspace.

Use this folder when you want to turn inspected ZEIA context and a reviewed
`request.spec.yaml` into `protocol.ir.json`, generated artifacts, validation
diffs, and a validated `.zeia` archive under `ready-to-import/`.

The Python package is still named `fluent_pipeline` for import compatibility,
but the workspace layer is now called `03-protocol-builder`.
Preferred installed command: `protocol-builder`. Module usage stays
`python -m fluent_pipeline.cli`.

## Quick Start

```powershell
cd "source/03-protocol-builder"

python -m fluent_pipeline.cli doctor --install-missing --report ready-to-import/<project>/temp_files/doctor.md
..\..\..\.venv\Scripts\python.exe -m fluent_pipeline.cli import-project "<project.zeia>" --name my-project --activate
..\..\..\.venv\Scripts\python.exe -m fluent_pipeline.cli request-spec `
  "Use these ZEIA files to make a new script that ____" `
  --context my-project `
  --source-script "<source script name>" `
  -o ready-to-import/<project>/temp_files/generated_script/request.spec.yaml
..\..\..\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_script/request.spec.yaml `
  --context my-project `
  --out-dir ready-to-import/<project>/temp_files/generated_script `
  --no-simulate `
  --no-compile
..\..\..\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_script/request.spec.yaml `
  --context my-project `
  --ir ready-to-import/<project>/temp_files/generated_script/protocol.ir.json `
  --out-dir ready-to-import/<project>/temp_files/generated_script_final
```

The first command creates the shared repository-level `.venv` and hands the
install graph to `python -m fluent_pipeline.bootstrap`, which installs the
local packages in one canonical order, pins the Windows desktop automation
stack through `vendor/desktop-automation/constraints.txt`, caches those
automation wheels under `vendor/desktop-automation/wheels` when network is
available, and runs `pip check` before returning. After that, use the same
shared `.venv` Python for generation, simulation, and compile commands. When
working from this subfolder, that interpreter lives at
`..\..\..\.venv\Scripts\python.exe` on Windows.

To include FluentControl Snapshot support data in the imported context, add one
or more Snapshot ZIPs during import:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli import-project "<project.zeia>" `
  --name my-project `
  --snapshot "<support-snapshot.zip>" `
  --activate
```

The project manifest and diagnosis reports will summarize Snapshot-derived
instrument configuration, `system.config` simulation setup evidence, hardware
or firmware details, logs, screenshots, and user troubleshooting context.
For one-off troubleshooting, pass the same file to `diagnose` with
`--snapshot "<support-snapshot.zip>"`.

List reusable template shapes:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli template-list
.\.venv\Scripts\python.exe -m fluent_pipeline.cli template-info plate_transfer
```

Template assets live under `templates/<name>/` as canonical
`template.ir.json`, template-specific `request.schema.json`, and example
request specs.

Inspect ZEIA import aliases:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli alias-list
.\.venv\Scripts\python.exe -m fluent_pipeline.cli alias-resolve labware "MCA384TipBox[001]"
.\.venv\Scripts\python.exe -m fluent_pipeline.cli alias-normalize-ir ready-to-import/<project>/temp_files/generated_script/protocol.ir.json
```

Alias assets live under `config/aliases/` and are used by repair plans,
worktable diffs, and ready validation gates.

Write the FluentControl/manual/connector compatibility matrix:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli compatibility-matrix `
  --report ready-to-import/<project>/temp_files/fluent_compatibility_matrix.md `
  --json-out ready-to-import/<project>/temp_files/fluent_compatibility_matrix.json
```

The JSON report includes parsed manual metadata, parsed connector package
metadata, evidence links, and per-row confidence levels.

To classify a specific lab setup, pass the exact FluentControl version/build
and Windows environment:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli compatibility-matrix `
  --connector unitelabs `
  --fluentcontrol-version "3.4 SP1" `
  --fluentcontrol-build "3.4.10.62215" `
  --manual-version "FluentControl 3.4 SP1" `
  --windows-environment "Windows 10 Enterprise LTSC 2021"
```

For generation from multiple ZEIA files, import each file as its own context,
combine them into a collection, then generate from that merged context:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli import-project "<project-a.zeia>" --name project-a
.\.venv\Scripts\python.exe -m fluent_pipeline.cli import-project "<project-b.zeia>" --name project-b
.\.venv\Scripts\python.exe -m fluent_pipeline.cli create-collection assay-sources `
  --context project-a `
  --context project-b
.\.venv\Scripts\python.exe -m fluent_pipeline.cli request-spec `
  "Use both ZEIA contexts to make a new script that ____" `
  --collection assay-sources `
  --source-script "project-a:setup_script" `
  --source-script "project-b:cleanup_script" `
  -o ready-to-import/<project>/temp_files/generated_from_collection/request.spec.yaml
.\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_from_collection/request.spec.yaml `
  --out-dir ready-to-import/<project>/temp_files/generated_from_collection
```

You can also repeat `--project-archive` or `--context` directly on `generate`;
the command will create a collection for that run.

To pull mined reader patterns directly into the generated IR, add the reader
SQLite index with exact pattern IDs or pattern queries when writing the request
spec. Complete mined command windows are planned into canonical IR steps
automatically:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli request-spec `
  "Reuse existing tip pickup and transfer command windows" `
  --index-db "..\01-project-reader\build\tecan_project_index.sqlite" `
  --pattern-id 42 `
  --pattern-query "aspirate Water Free Single" `
  --source-script-rank 1 `
  -o ready-to-import/<project>/temp_files/generated_script/request.spec.yaml
.\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_script/request.spec.yaml `
  --no-simulate `
  --no-compile
```

This writes `request.spec.yaml`, `03_ir_synthesis.json`, and
`validation_diff.md` beside `<protocol>.protocol-ir.json`. The synthesis report
contains the synthesized step, labware, liquid class, worklist, and warning
counts. The validation diff compares the reviewed request spec against the IR,
generated files, worktable diff, and ready validation result.
The workflow also writes `<protocol>.liquid-state.md` before Python draft
generation so source volumes, destination capacity, dead volume, mix volume,
and tip carryover assumptions are checked early.

Optionally run a real FluentControl import/load diagnostic before packaging or
before claiming Script Editor load-clean:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_script/request.spec.yaml `
  --ir ready-to-import/<project>/temp_files/generated_script/protocol.ir.json `
  --fluent-context-check `
  --fluent-method "Method visible in FluentControl" `
  --out-dir ready-to-import/<project>/temp_files/generated_script_final
```

This starts or attaches to FluentControl through a configured provider and
writes `<protocol>.fluent-context-check.md/json`. If FluentControl reports
import/load/runtime errors, the optional Gate 27 diagnostic fails before
`ready-to-import` packaging.

For local Script Editor load evidence, use the maintained desktop provider:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_script/request.spec.yaml `
  --ir ready-to-import/<project>/temp_files/generated_script/protocol.ir.json `
  --fluent-context-check `
  --fluent-provider local-desktop `
  --fluent-method "Generated method name" `
  --out-dir ready-to-import/<project>/temp_files/generated_script_final
```

`local-desktop` runs `python -m fluent_pipeline.fluent_runtime_provider`, imports
ZEIA artifacts when supplied, opens the compiled XSCR in Script Editor, captures
nearby FluentControl logs/UI dialogs, and reports `runtime_errors` such as
`VX_SCEDT_...` load failures. It does not run methods or accept hardware-motion
prompts. Useful environment variables:

- `TECAN_FLUENT_EXE`: optional FluentControl executable to start if the main
  window is not already open.
- `TECAN_FLUENT_SCRIPT_OPEN_CMD`: optional Script Editor open command template
  with `{method}`, `{xscr}`, `{zeia}`, `{mode}`, and `{timeout}` placeholders.
- `TECAN_FLUENT_LOG_DIR`: additional FluentControl log root to scan.
- `TECAN_FLUENT_ALLOW_REPLACE=1`: allow expected import replacement prompts.

<!-- BEGIN GENERATED: readiness-gate-summary -->
Readiness registry summary (generated from `fluent_pipeline/data/readiness_gate_registry.json`):
- Required offline ready-to-import gates: `26`
- Optional diagnostics: `1` (`Gate 27`)
- Current active entries: `27`
- Stable IDs are the contract; gate numbers are display labels only.
- Authoritative table: [Readiness Gate Registry](docs/READINESS_GATES.md)
<!-- END GENERATED: readiness-gate-summary -->

Use `fluent-prepare-check` directly to test the connector setup; set
`TECAN_FLUENT_CONTEXT_CHECK_CMD` for a custom external provider or
`--fluent-provider legacy-sila` for the legacy method-name-only check.

## Folder Map

- `fluent_pipeline/` - local CLI wrapper, protocol IR, generation workflow,
  request specs, validation gates, export packaging, and worktable diff logic.
- `config/aliases/` - reusable catalog, labware, liquid-class, and device alias maps.
- `source/00-shared/tecan_common/data/command_registry.json` - shared command
  IDs, aliases, operation mappings, required fields, and manual-step
  templates.
- `AGENTS.md` - Codex command contract with required CLI paths and edit
  boundaries.
- `docs/` - detailed protocol-builder guide and Codex workflow.
- `examples/` - small protocol drafts for smoke tests and reference.
- `ready-to-import/<project>/temp_files/` - isolated imported ZEIA project
  contexts, source archives, extracted files, reports, drafts, and collections.
- `templates/` - reusable protocol template IR shapes, request schemas, and examples.
- `tests/` - unit tests and the protocol regression corpus.
- `libs/fluentcoder/` - first-party Fluent authoring/simulation/compiler
  implementation used directly by the pipeline.

## Docs

- `docs/PROTOCOL_BUILDER_GUIDE.md` - full command guide, artifact policy,
  canonical IR flow, validation gates, regression corpus, setup notes, and
  safety boundary.
- `docs/PROTOCOL_IR_SCHEMA.md` - canonical IR version, required fields,
  operation enum, JSON Schema commands, validation behavior, and migration API.
- `docs/CODEX_WORKFLOW.md` - step-by-step workflow Codex should follow when
  creating or revising FluentControl scripts.
- `AGENTS.md` - concise command contract for Codex, including allowed edits,
  forbidden commands, and required verification.
- `docs/NEXT_STEPS.md` - backlog for improving the local AI-assisted authoring
  workflow.

Generated `.xscr` files are internal compilation intermediates only. A
generation task is complete only when a strictly validated `.zeia` archive has
been atomically published under `ready-to-import/`; if publication does not
occur, the task is incomplete or blocked.
