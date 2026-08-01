# Protocol Builder

Layer 3, the Protocol Builder, for the Fluent AI-Assistance project.

This is a local Codex-facing wrapper around the first-party `fluentcoder`
package in `libs/fluentcoder`. It does not use API keys and it does not call
optional authoring/chat/deploy surfaces.
The official generation loop is:

1. Capture `request.spec.yaml`.
2. Import ZEIA project context.
3. Inspect scripts and worktable.
4. Select source scripts/patterns.
5. Build `protocol.ir.json`.
6. Validate liquid logic with the robotools-style liquid-state model.
7. Generate Python draft.
8. Simulate.
9. Generate repair plan.
10. Apply safe repairs.
11. Compile to `.xscr`.
12. Generate `RECREATE_SCRIPT.md`.
13. Generate `worktable_changes.md` and `worktable.patch.json`.
14. Validate all 26 required ready gates.
15. Generate `validation_diff.md` and `validation_diff.json`.
16. Package into `..\ready-to-import\`.

## Commands

```powershell
cd "source/03-protocol-builder"

# Import a .zeia as an isolated project context
.\.venv\Scripts\python.exe -m fluent_pipeline.cli import-project "C:\path\to\project.zeia" `
  --name my-project `
  --activate

# Switch between imported contexts
.\.venv\Scripts\python.exe -m fluent_pipeline.cli list-projects
.\.venv\Scripts\python.exe -m fluent_pipeline.cli use-project my-project
.\.venv\Scripts\python.exe -m fluent_pipeline.cli clear-project
.\.venv\Scripts\python.exe -m fluent_pipeline.cli project-find "Plexiglas"

# Combine multiple imported ZEIA contexts for generation
.\.venv\Scripts\python.exe -m fluent_pipeline.cli create-collection assay-sources `
  --context source-project-1 `
  --context source-project-2
.\.venv\Scripts\python.exe -m fluent_pipeline.cli list-collections
.\.venv\Scripts\python.exe -m fluent_pipeline.cli collection-info assay-sources

# Check the local fluentcoder setup
python -m fluent_pipeline.cli doctor --install-missing --report ready-to-import/<project>/temp_files/doctor.md

# List reusable template protocol shapes
.\.venv\Scripts\python.exe -m fluent_pipeline.cli template-list
.\.venv\Scripts\python.exe -m fluent_pipeline.cli template-info plate_transfer

# Inspect and apply ZEIA import aliases
.\.venv\Scripts\python.exe -m fluent_pipeline.cli alias-list
.\.venv\Scripts\python.exe -m fluent_pipeline.cli alias-resolve liquid_class "Water Free Single[001]"
.\.venv\Scripts\python.exe -m fluent_pipeline.cli alias-normalize-ir ready-to-import\<project>\temp_files\protocol.ir.json `
  -o ready-to-import\<project>\temp_files\protocol.alias-normalized.ir.json

# Simulate a Python protocol draft
.\.venv\Scripts\python.exe -m fluent_pipeline.cli simulate examples/simple_transfer.py `
  --report ready-to-import/<project>/temp_files/simple_transfer_simulation.md `
  --json-out ready-to-import/<project>/temp_files/simple_transfer_simulation.json

# Build a repair plan from a draft and simulation JSON
.\.venv\Scripts\python.exe -m fluent_pipeline.cli repair-plan drafts/script_decompiled.py `
  --context my-project `
  --simulation-json reports/script_simulation.json `
  --report reports/script_repair_plan.md

# Write a repaired draft. Modeling suggestions require explicit opt-in.
.\.venv\Scripts\python.exe -m fluent_pipeline.cli repair-draft drafts/script_decompiled.py `
  --context my-project `
  --simulation-json reports/script_simulation.json `
  -o drafts/script_repaired.py `
  --apply-modeling

# Compile a Python protocol draft to .xscr
.\.venv\Scripts\python.exe -m fluent_pipeline.cli compile examples/simple_transfer.py `
  -o ready-to-import/<project>/temp_files/simple_transfer.xscr

# Decompile an installed FluentControl script
.\.venv\Scripts\python.exe -m fluent_pipeline.cli decompile "<path-to-script.xscr>" `
  --context my-project `
  -o drafts/script_decompiled.py

# Try a full decompile -> simulate -> compile pass
.\.venv\Scripts\python.exe -m fluent_pipeline.cli roundtrip "<path-to-script.xscr>" `
  --context my-project

# Export a Python draft, .xscr, .gwl, or .zeia archive to canonical protocol IR
.\.venv\Scripts\python.exe -m fluent_pipeline.cli ir-export examples/simple_transfer.py `
  -o ready-to-import/<project>/temp_files/simple_transfer.protocol-ir.json

# Build artifacts from the canonical IR
.\.venv\Scripts\python.exe -m fluent_pipeline.cli ir-build ready-to-import/<project>/temp_files/simple_transfer.protocol-ir.json `
  --out-dir ready-to-import/<project>/temp_files/simple_transfer_from_ir

# Compare imported ZEIA worktable/context against protocol IR requirements
.\.venv\Scripts\python.exe -m fluent_pipeline.cli worktable-diff ready-to-import/<project>/temp_files/simple_transfer.protocol-ir.json `
  --context assay-context `
  --source-script old_assay_setup `
  -o ready-to-import/<project>/temp_files/simple_transfer_from_ir/worktable_changes.md
# Also writes ready-to-import/<project>/temp_files/simple_transfer_from_ir/worktable.patch.json

# Write the user request contract first
.\.venv\Scripts\python.exe -m fluent_pipeline.cli request-spec `
  "Use these ZEIA files to make a new script that transfers 20 uL from source to destination" `
  --project-archive "C:\path\to\project.zeia" `
  --source-script "old_assay_setup" `
  --pattern "pick_up_tips from old_assay_setup" `
  -o ready-to-import/<project>/temp_files/generated_transfer/request.spec.yaml

# Official ZEIA-to-new-script generation workflow
.\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_transfer/request.spec.yaml `
  --name assay-context `
  --out-dir ready-to-import/<project>/temp_files/generated_transfer

# Multi-ZEIA generation from a saved collection
.\.venv\Scripts\python.exe -m fluent_pipeline.cli request-spec `
  "Use these ZEIA files to make a new script that combines setup and cleanup" `
  --collection assay-sources `
  --source-script "source-project-1:setup_script" `
  --source-script "source-project-2:cleanup_script" `
  -o ready-to-import/<project>/temp_files/generated_collection_transfer/request.spec.yaml
.\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_collection_transfer/request.spec.yaml `
  --out-dir ready-to-import/<project>/temp_files/generated_collection_transfer

# Multi-ZEIA generation without a separate create-collection step
.\.venv\Scripts\python.exe -m fluent_pipeline.cli request-spec `
  "Use these ZEIA files to make a new script that combines setup and cleanup" `
  --project-archive "C:\path\to\source_project_1.zeia" `
  --project-archive "C:\path\to\source_project_2.zeia" `
  --source-script "source_project_1:setup_script" `
  --source-script "source_project_2:cleanup_script" `
  -o ready-to-import/<project>/temp_files/generated_collection_transfer/request.spec.yaml
.\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_collection_transfer/request.spec.yaml `
  --name assay-sources `
  --out-dir ready-to-import/<project>/temp_files/generated_collection_transfer
```

`create-collection` reports source loading, counted script/object merges,
validation, and manifest publication by default. Use `--progress json` for
JSON Lines events, `--progress plain` for terminal lines, or `--progress none`
to suppress progress. Progress is written to stderr; the final collection
summary remains on stdout.

The shared `generate` workflow publishes only a validated FluentControl project
archive under:

```text
..\ready-to-import
```

Standalone `.xscr` files remain internal intermediates. Reports, manifests, and
drafts stay in the run output folder.

## Ready-To-Import Artifact

Every successful generation publishes this predictable layout:

```text
ready-to-import/
  my_new_protocol/
    my_new_protocol.zeia
    run_tecan_bundle_setup.bat
    delivery_manifest.json
    source/
    media/
    reports/
```

`request.spec.yaml` records the reviewed user intent, source scripts/patterns,
generation options, review state, and acceptance criteria.
`validation_diff.md` / `.json` compares that contract against the protocol IR,
generated artifact inventory, worktable diff, and ready validation result.
Run reports and manifests are copied into the protocol folder after generation
and also remain in the generation output folder.
`RECREATE_SCRIPT.md` is generated from `protocol.ir.json` and must not be
handwritten as a separate after-the-fact explanation.
`worktable_changes.md` is generated by comparing source ZEIA worktable/context
metadata with `protocol.ir.json` requirements. It calls out missing labware,
changed deck positions, liquid classes, tip boxes, carriers, device aliases,
worklist paths, and manual FluentControl setup steps. `worktable.patch.json` is
generated from the same diff and records machine-readable operations with
`safe`, `needs_review`, or `blocking` severities.

If failed internal artifacts are needed for debugging, rerun with
`--preserve-failed-artifacts`; preserved files are placed under the run output's
`failed_artifacts/` directory.

## Ready Validation Gates

Generated `.xscr` files are internal compilation intermediates only. A
standalone `.xscr` is never a deliverable and must not be left in normal
generation output directories. The protocol builder completes generation only
when a strictly validated V2 delivery folder is atomically published under
`ready-to-import/<protocol>/` and contains the validated `<protocol>.zeia`; if
publication does not occur, the run is incomplete or blocked.

The protocol builder will not publish the `.zeia` unless these gates pass:

1. ZEIA parsed successfully.
2. `protocol.ir.json` schema is valid.
3. All labware names resolve.
4. All liquid classes resolve.
5. All worklist paths are valid.
6. Python draft was generated.
7. Simulation passes.
8. Repair plan has no unresolved critical errors.
9. `.xscr` compiles.
10. `RECREATE_SCRIPT.md` matches `protocol.ir.json`.
11. Post-compile `.xscr` re-inspects successfully.
12. Compiled `.xscr` roundtrip IR matches `protocol.ir.json`.
13. Liquid handling volumes are within configured bounds.
14. Explicit well references are in range.
15. Tip capacity can support liquid handling volumes.
16. Liquid classes are compatible with selected operations.
17. No unsupported raw XML is present unless explicitly approved.
18. Robotools-style liquid state model is valid.
19. Required tip boxes resolve in the source context.
20. Required carriers resolve in the source context.
21. Required device aliases resolve in the source context.
22. Deck positions match the source worktable unless explicitly approved.
23. Generated ZEIA entries carry valid FluentControl checksums.
24. Packaged generated ZEIA opens, resolves references, and matches its datastore metadata.
25. Compiled command name strings resolve in the source context.
26. Added subroutines carry sound datastore metadata (replace preferred over add).

Gates 19-22 enforce the worktable diff that was previously computed but never
gated: a tip box, carrier, or device alias the IR depends on must be present (or
verifiable) in the source context, and any labware the IR moves to a different
deck position than the source worktable blocks readiness. A deck-layout change
can be acknowledged by passing `--approve-deck-layout` on `generate` and
recording the approval at `review.deck_layout` (stable gate ID
`deck_layout_consistent`, approval key `deck_layout_changes`) once an operator
confirms the manual relocation. When the IR requires none of a given resource
the gate passes as a `trivial: true` vacuous pass.

Gate 23 enforces import-cleanliness. FluentControl validates `<Checksum>` on
every datastore object at load, so an edited entry shipped with a blank checksum
makes the bundle reject or prompt to recalculate on import. Unchanged base
entries keep their valid source checksums; edited entries are recomputed when the
`fluentcontrol_core` bridge is available. When it is not (the default offline
environment), the gate fails because the generated ZEIA would ship blank
checksums. Pass `--waive-checksum-recompute` (context key
`checksums_recompute_waived`) to package anyway and accept the in-app
recalculation prompt, or recompute on a FluentControl machine. The
`project_import_report.md` `Checksum status` line and the `checksum_audit` in
`project_import_report.json` list exactly which entries are blank.

Gate 24 validates the packaged `generated_project.zeia` itself — not just the
standalone `.xscr`. After packaging writes the archive, the pipeline re-opens it
and checks zip integrity (including CRC), that every `meta/content.xml` datastore
entry points at a real file, and that every `<Reference>` GUID in shipped scripts
either resolves inside the archive or is flagged as needs-review (the dependency
must already exist in the target FluentControl system). A corrupt zip or a
metadata entry with no matching file blocks readiness. The `Import artifact
check` line in `project_import_report.md` and the `archive_audit` in
`project_import_report.json` list blocking vs needs-review findings.

Gate 25 (stable ID `command_inventory_resolves`) closes the command-level name gap. The earlier gates resolve labware,
liquid-class, and device names from the IR, but a compiled `.xscr` can still embed
a literal `LabwareType`, `LabwareName`/`LabwareLable`, `DeviceAlias`, `AvailableID`,
or `LiquidClassName` string that FluentControl does not actually have (so Gate 3
can pass while the compiled XSCR ships a catalog string the target system lacks).
This post-compile inventory gate extracts those literal strings from the compiled
command XML and diffs them against the `source_manifest` inventory and the alias
maps (`config/aliases/`). A used name that resolves nowhere — the source context
exposed an inventory for that category and the name (after alias resolution) is
not in it — is blocking. A name whose category has no source inventory to compare
against cannot be confidently checked offline, so it passes as needs-review
(`needs_review: true`) rather than being hidden, mirroring the worktable-resource
gates' `missing` vs `unverified` model. Blocking findings can be acknowledged
with `--approve-command-inventory` on `generate` (context key
`command_inventory_approved`) once an operator confirms the target FluentControl
system carries the names; the gate then passes as needs-review.

Gate 26 audits the datastore metadata of subroutines ADDED to the generated ZEIA.
Replacing an existing subroutine reuses its GUID/entry and is the safe path;
adding a brand-new subroutine synthesizes metadata (a fresh GUID, an incremented
`<V>`, `<FileRef>` lines) and is inherently riskier. After packaging writes the
archive, this gate re-opens it and, per added subroutine, confirms the `.xscr`
entry is present, the GUID is a well-formed UUID, and (for a datastore archive)
the GUID has a matching nodedescription node with a positive `<V>`. A defect that
breaks datastore identity (missing entry, malformed GUID, or no node) is blocking;
otherwise additions pass as needs-review with a "prefer replace over add"
recommendation. Best practice: build the subroutine into the base ZEIA in
FluentControl first, then re-run so the pipeline replaces the existing entry
(reusing its GUID/metadata) instead of synthesizing new metadata. The
`Subroutine additions` line in `project_import_report.md` and the
`subroutine_audit` in `project_import_report.json` record the additions and any
defects.

Gate 27 (optional) runs only when a FluentControl import/load diagnostic is
requested (`fluent_context_check_required`). It is not part of the 26 required
offline gates and is skipped in the default no-runtime workflow.

<!-- BEGIN GENERATED: readiness-gate-summary -->
Readiness registry summary (generated from `../fluent_pipeline/data/readiness_gate_registry.json`):
- Required offline ready-to-import gates: `26`
- Optional diagnostics: `1` (`Gate 27`)
- Current active entries: `27`
- Stable IDs are the contract; gate numbers are display labels only.
- Authoritative table: [Readiness Gate Registry](READINESS_GATES.md)
<!-- END GENERATED: readiness-gate-summary -->

When this `fluent_context_check` runs against a produced artifact, the provider
must actually load/import the compiled XSCR (or generated ZEIA) in simulation
mode, not merely prepare a method by name; any load/import error is surfaced as a
gate failure. A provider that cannot import the artifact (the legacy SiLA
wrapper, or an offline environment with no external importer configured) reports
`status="unavailable"`/`ok=False`, which also fails the gate rather than passing
vacuously.

Use `--fluent-provider local-desktop` on a Windows FluentControl host for the
maintained local runtime provider. It invokes
`python -m fluent_pipeline.fluent_runtime_provider`, waits for the FluentControl
main window (or starts `TECAN_FLUENT_EXE` when configured), imports a supplied
ZEIA through the Database Import UI, opens the compiled XSCR or configured Script
Editor open command, captures relevant UI dialogs and recent FluentControl logs,
and maps load/import failures into `runtime_errors`. It only performs
import/open checks; it does not run methods or accept prompts that look like
hardware motion. Configure `TECAN_FLUENT_LOG_DIR` for nonstandard log locations,
`TECAN_FLUENT_SCRIPT_OPEN_CMD` when the host needs a custom Script Editor launch
command, and `TECAN_FLUENT_ALLOW_REPLACE=1` only when replacement prompts are
expected. For a custom importer, use `--fluent-command` /
`TECAN_FLUENT_CONTEXT_CHECK_CMD` with `{xscr}` and `{zeia}` placeholders.

### Readiness vocabulary

The workflow reports several readiness levels because a clean import archive can
still fail later in Script Editor:

- `ready_to_import` / import-clean: the offline gates allowed packaging and the
  generated ZEIA checksum/archive checks are acceptable.
- `import_ready_needs_review`: the required offline gates passed, but at least
  one required gate still raised a non-blocking review item.
- Script Editor load-clean: Gate 27 passed with a real FluentControl artifact
  import/open check, or a human manually opened the generated artifact in Script
  Editor and resolved load errors.
- `load_failed`: the optional Gate 27 diagnostic reported a Script Editor load
  failure. The offline artifact remains structurally valid, but load-clean is
  false until the load issue is resolved.
- Simulation-clean: Gate 7 passed, and any requested FluentControl runtime check
  passed.
- `hardware_review_required`: not granted by the offline workflow. A target-system
  operator must verify dependencies, deck layout, labware, liquids, adapters or
  fingers, prompts/ranges, and instrument state.

Read `generation_manifest.json` fields `readiness_status` and `readiness` before
handoff. `readiness_status: ready_to_import` is a normal offline outcome when
the optional Gate 27 diagnostic did not run. The next action is to run that
diagnostic or manually open the generated script in FluentControl Script Editor
before calling the method load-clean.

Passing runs publish a `.zeia` under `ready-to-import/` and keep validation
reports in the run output folder. Failed gates publish nothing and delete the
internal compiled XSCR unless `--preserve-failed-artifacts` was used.

A scaffold pass (`--no-compile`) runs none of these gates. It is reported as
`workflow_status: scaffold_not_validated` / `ready_to_import: false` in
`generation_manifest.json`, and its `ready_validation.md` is a loud
"scaffold only / Not Ready To Import" stub. A passing report with all 26
required offline gates only exists after a compile-enabled run; optional Gate 27
appears only when the diagnostic is requested.

### Trivial (vacuous) passes

Several liquid-handling gates (13 volume bounds, 14 well ranges, 15 tip
capacity, 16 liquid-class compatibility, 18 liquid state) pass automatically
when the IR contains no liquid-handling steps to check. The worktable-resource
gates (19 tip boxes, 20 carriers, 21 device aliases, 22 deck layout) likewise
pass trivially when the IR requires none of that resource. When this happens the
gate is marked `trivial: true` in `validation_report.md` details, and the report
header lists a `Trivial passes` count. A passing report with trivial gates is
expected for non-pipetting protocols (for example tube scanning or transport
only), but for any protocol that should move liquid, treat trivial passes on
these gates as a signal that the IR did not capture the intended liquid handling
and re-check the source extraction before packaging.

## Canonical IR Flow

The canonical protocol IR is the stable handoff layer between AI edits and
Tecan-specific file formats. It records protocol metadata, worktable identity,
labware, reagents, liquid classes, variables, worklists, dependencies, safety
assumptions, and ordered steps in one JSON or YAML document. JSON works without
extra dependencies; YAML requires PyYAML.

The current schema version is `tecan.protocol_ir.v2`. The schema is exported by:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli ir-schema --format json -o ready-to-import/<project>/temp_files/protocol_ir.schema.json
.\.venv\Scripts\python.exe -m fluent_pipeline.cli ir-schema --format markdown -o ready-to-import/<project>/temp_files/protocol_ir_schema.md
```

Validate the document exactly as written with:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli ir-validate ready-to-import/<project>/temp_files/generated_script/protocol.ir.json
```

Use `--normalize` only when checking the result after migrations and default
filling. The migration registry upgrades legacy v1 documents through
`tecan.protocol_ir.v1 -> tecan.protocol_ir.v2`; see
`docs/PROTOCOL_IR_SCHEMA.md` for the operation enum and migration API.

Use `ir-export` to convert a Python draft, `.xscr`, `.gwl`, or `.zeia` archive
into IR. ZEIA input produces a bundle with one protocol IR per `.xscr` script
inside the archive. Use `ir-build` to generate a Python draft, optional GWL,
compiled XSCR when the fluentcoder environment is configured, `RECREATE_SCRIPT.md`, and
`worktable_changes.md` and `worktable.patch.json` from the same IR.

Treat the IR as the review/edit target. Generated `.xscr`, `.gwl`, Python, and
recreate files should be regenerated from IR after meaningful protocol changes.
The recreate guide uses the IR's worktable, chosen labware/liquid classes, and
ordered command steps for its manual FluentControl instructions. It also opens
with an `## Original Request` section that quotes the original user prompt
(`request.intent`, carried into `protocol.ir.json` under `source.intent`) so the
bundle records why it was built. The same prompt is also present in
`source/request.spec.yaml` and `source/validation_diff.md`.
The worktable diff uses the same IR to make explicit what the generated script
does not prove about deck layout, labware availability, liquid classes,
instrument state, and tip strategy.

## Command Registry

`source/00-shared/tecan_common/data/command_registry.json` is the shared
command-reference layer used by the reader and protocol builder. Use
`command_registry_resource()` at runtime and `source_command_registry_path()`
when editing the source file. The registry maps FluentControl command IDs and
aliases to canonical IR operations, command families, pattern types, required
fields, field aliases, and manual recreation text.

Add or correct registry entries before adding new command-name string matching.
The old text heuristics remain as fallbacks for unknown commands, but known
commands should resolve through the registry first.

## Regression Corpus

Protocol fixtures live under `tests/protocols/`:

- `simple_transfer`
- `serial_dilution`
- `plate_copy`
- `worklist_import`
- `mca384_transfer`
- `tip_pickup_cleanup`

Each case describes a source ZEIA fixture, expected extracted manifest subset,
expected protocol IR operations, expected guide step count, expected worklist
records, expected simulation status, and allowed source operations. The corpus
test materializes a temporary `.zeia` from `input_zeia.json` for each case and
checks that guides, IR, worklists, labware/liquid class resolution, XSCR-derived
roundtrip behavior, and source-command constraints stay stable.

Each protocol folder contains:

- `input_zeia.json`
- `expected_extracted_manifest.json`
- `expected_protocol.ir.json`
- `expected_guide_steps.json`
- `expected_worklist_records.json`
- `expected_simulation_result.json`
- `allowed_source_operations.json`

## Official Generation Workflow

Use `request-spec` followed by `generate --spec` as the default path for
requests like:

```text
Use these ZEIA files to make a new script that ____.
```

The command creates a generation folder containing:

- `request.spec.yaml`
- `GENERATION_PLAN.md`
- `01_context_inspection.md` / `.json`
- `02_selected_sources.json`
- `03_ir_synthesis.json`
- `<protocol>.protocol-ir.json`
- `<protocol>.py`
- optional `<protocol>.gwl`
- `<protocol>.simulation.md` / `.json`
- `<protocol>.repair-plan.md` / `.json`
- `<protocol>.repaired.py`
- `RECREATE_SCRIPT.md`
- `worktable_changes.md`
- `worktable.patch.json`
- `ready_validation.md`
- `validation_diff.md` / `.json`
- `generation_manifest.json`
- `GENERATION_WORKFLOW.md`

When final compile and strict readiness succeed, the workflow publishes the
validated V2 delivery folder under `ready-to-import/<protocol>/`. The run
manifest records `published_artifacts`, `internal_artifacts`, and `deliverable`;
a standalone XSCR must only appear as `compiled_xscr_intermediate` with
`deliverable: false`.

### Project import ZEIA reuses the exact source base

The `generated_project.zeia` is built from the **exact** source ZEIA the user
supplied. Every original entry is preserved byte-for-byte; the pipeline only
adds or replaces script entries (the generated main script and any packaged
subroutines) and the datastore metadata for those scripts. It never creates new
models, components, liquid classes, worktables, carriers, or devices — this
minimizes import conflicts because the method binds to objects that already
exist in the target system.

To enforce "reuse only, never create," every `<Reference>` block in each
packaged script is checked against the GUIDs present in the source base:

- A dead, unused liquid-class reference (absent from the base, not used in the
  script body) is dropped so the archive still imports cleanly.
- Any other unresolved reference — a used liquid class or any non-liquid model
  such as a `WorktableWorkspace`, carrier, or device — is left untouched (the
  model is never fabricated) and recorded as a **missing model dependency**.

`project_import_report.md` records a per-archive `Base reuse` line (entries
preserved, scripts replaced/added, models created — always `0`) and a
`Missing model dependencies` section listing any reference that must already
exist in the target FluentControl system. The same findings are mirrored in
`generation_manifest.json` under each project record's `unresolved_references`
and `base_reuse`. Treat any entry in the missing-dependency section as a setup
prerequisite before import.

The report also records a `Checksum status` line and a `checksum_audit` object
(in `project_import_report.json`). FluentControl validates `<Checksum>` on every
datastore object at load. Unchanged base entries keep their original valid
checksums; edited entries (the replaced main script, packaged subroutines, and
their datastore metadata) are recomputed when the `fluentcontrol_core` bridge is
available. Offline, where the bridge is absent, edited entries ship with a blank
checksum, the bundle is reported `NOT import-clean`, and the blank entries are
listed. Recompute on a FluentControl machine before import, or accept the
in-app recalculation prompt. Gate 23 enforces this and blocks packaging unless
`--waive-checksum-recompute` is set.

The report also records an `Import artifact check` line and an `archive_audit`
object. Gate 24 re-opens the written `generated_project.zeia` and blocks on a
corrupt zip or a `meta/content.xml` entry with no matching file. Unresolved
`<Reference>` GUIDs are needs-review (the file still imports; the dependency
must exist in the target system).

If no completed IR is provided, `generate --spec` writes a seed
`protocol.ir.json` and runs the IR planner. The planner converts selected full
scripts and mined pattern windows into ordered `steps[]`, labware, liquid
classes, worklists, and dependencies when the source data contains required
fields such as labware targets, `volume_ul`, and liquid classes. The
`build_protocol_ir` stage is marked `passed` when synthesis creates steps; it
remains `needs_user` when the selected sources only provide incomplete notes.
`03_ir_synthesis.json` records the planned counts and warnings for any skipped
commands. If `--ir` is provided, the command runs that reviewed IR through
draft generation, simulation, repair, compile, recreate, worktable diff,
validation diff, and packaging.

For planning/scaffold work without the local Fluent runtime, use:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli request-spec `
  "Use these ZEIA files to make a new script that ____" `
  --project-archive "C:\path\to\project.zeia" `
  --source-script "SourceScript" `
  --pattern "aspirate from Script B" `
  --index-db "..\01-project-reader\build\tecan_project_index.sqlite" `
  --pattern-id 42 `
  --pattern-query "pick_up_tips MCA384TipBox" `
  --source-script-rank 1 `
  -o ready-to-import/<project>/temp_files/generated_transfer/request.spec.yaml
.\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_transfer/request.spec.yaml `
  --no-simulate `
  --no-compile
```

`--pattern` still records plain-text reuse notes. Use `--index-db` with
`--pattern-id` or `--pattern-query` to pull exact mined command windows from
the reader's SQLite pattern library into `source.selected_pattern_windows` in
the generated IR. Query selection is deterministic: `--source-script-rank`
chooses the 1-based matching source script, then the best matching pattern
window from that script is embedded with command indexes, fields, summaries,
source script, source ZEIA, and safety notes. Indexed windows with complete
aspirate/dispense/mix/tip/worklist fields are also synthesized into canonical
IR operations automatically.

For final generation from a reviewed IR:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_transfer/request.spec.yaml `
  --context assay-context `
  --ir ready-to-import/<project>/temp_files/generated_transfer/protocol.ir.json `
  --out-dir ready-to-import/<project>/temp_files/generated_transfer_final
```

## Template Library

Reusable protocol shapes live under `templates/<template-name>/`:

```text
templates/
  plate_transfer/
    template.ir.json
    request.schema.json
    examples/
```

The initial template set is `plate_transfer`, `serial_dilution`,
`normalization`, `reagent_addition`, `bead_cleanup`, `worklist_execution`, and
`tip_strategy_test`. Each `template.ir.json` is a valid canonical
`tecan.protocol_ir.v2` document with safe example defaults. Each
`request.schema.json` describes the template-specific fields expected in a
`request.spec.yaml` under `template.parameters`.

Use templates as starting shapes, not as validated instrument methods. Copy an
example request spec, fill in project-specific labware, liquid classes, source
scripts, pattern references, volumes, wells, and review decisions, then run the
normal `request.spec.yaml -> protocol.ir.json -> validation_diff.md` workflow.

## Repair Layer

`repair-plan` reads a Python draft, optional project manifest, and optional
simulation JSON. It can currently detect:

- configured catalog, labware, liquid-class, and device aliases such as
  `Plexiglas Pane[002] -> Plexiglas Pane`
- modelable raw XML commands such as common `Mca384Aspirate`/`Dispense`/tip
  commands
- generic `TipBox` drafts that cause zero-capacity simulator failures when a
  specific `MCA*Box` class is implied by the catalog name

Alias maps live in `config/aliases/` as `catalog_aliases.yaml`,
`labware_aliases.yaml`, `liquid_class_aliases.yaml`, and
`device_aliases.yaml`. The same maps are used by `worktable-diff` and ready
validation gates so bracketed FluentControl project names can resolve to local
canonical names during ZEIA import review.

`repair-draft` is non-destructive. It writes a new Python file. Raw XML modeling
suggestions are only applied when `--apply-modeling` is passed.

## Setup Notes

The wrapper expects the local fluentcoder copy here:

```text
libs\fluentcoder
```

It also expects the shared repository virtual environment here:

```text
..\..\..\.venv
```

Imported project contexts live in:

```text
ready-to-import\<project-name>\temp_files
```

Each context contains the copied `.zeia`, extracted archive files, a
`manifest.json`, `project_report.md`, and scoped `drafts/`, `build/`,
`reports/`, and `roundtrips/` folders.

An older Inspiration checkout is retired and is not used. If you intentionally
want to point at a different fluentcoder checkout, set either of these
environment variables:

```powershell
$env:FLUENTCODER_ROOT = "C:\path\to\local-fluentcoder"
$env:FLUENTCODER_PYTHON = "C:\path\to\python.exe"
```

On this machine, `lxml` did not install cleanly under Python 3.14 without Visual
C++ build tools. The tested local wrapper path uses `fluentcoder` commands that
do not import `lxml`, so the venv was installed with the minimal dependencies
needed for catalog, decompile, simulate, and compile.

To rebuild or repair the local environment, let the workflow install missing
dependencies automatically:

```powershell
python -m fluent_pipeline.cli doctor --install-missing --report ready-to-import/<project>/temp_files/doctor.md
```

The expanded manual equivalent from the repository root is:

```powershell
python -m venv .venv
Set-Location source\03-protocol-builder
..\..\..\.venv\Scripts\python.exe -m fluent_pipeline.bootstrap
```

## Safety

Generated `.xscr` files are internal intermediates, not run artifacts. Do not
run anything on a real instrument until the published `.zeia` has been reviewed,
simulated, and validated in FluentControl with the proper worktable, labware,
liquid classes, and instrument state.

The `ready-to-import` folder is a manual import handoff, not an approval signal.

See `NEXT_STEPS.md` for the next practical build steps.
