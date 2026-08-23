# Agent Command Contract

Project entrypoint (layout + layer order): [`../../AGENTS.md`](../../AGENTS.md).

   <!-- BEGIN GENERATED: readiness-gate-summary -->
   Readiness registry summary (generated from `fluent_pipeline/data/readiness_gate_registry.json`):
   - Required offline ready-to-import gates: `26`
   - Optional diagnostics: `1` (`Gate 27`)
   - Current active entries: `27`
   - Stable IDs are the contract; gate numbers are display labels only.
   - Authoritative table: [Readiness Gate Registry](docs/READINESS_GATES.md)
   <!-- END GENERATED: readiness-gate-summary -->

## Hard start gate (required)

This file is large. Do **not** read it top-to-bottom.

1. Start with bootstrap, then brief:
   - MCP: `fluent_bootstrap_status` → follow `next_step`
   - or CLI: `python -m fluent_pipeline.cli bootstrap-status`
   - then `fluent_agent_brief(intent=...)` / `fluent_resolve_brief_mode(intent=...)`
     (or `--mode` / `scripts/agent/agent-brief.py --intent ...`)
2. Treat the brief as the checklist. Follow its STEPS.
3. Open **only** the `##` sections in this file that the brief names under
   `NEED MORE` / `CONTRACT` (exact heading match). Do not skim unrelated
   sections mid-workflow.
4. Exceptions: user asked a question that names a section; fixing a failure
   that cites a heading; install/simulator-only jobs (brief modes
   `install` / `simulator`).

Do not call generate / deep-inspect until bootstrap allows it:
`fluent_generate_protocol` must stay unused while it appears in
`next_step.blocked_tools`. Typical sequence: `fix_doctor` →
`import_project` → `inspect_project` → re-call bootstrap with
`inspected=true` → `choose_workflow`. Skip only if the user explicitly
skips bootstrap or already ran it this session.

This contract applies to the **protocol-builder** layer only. Work from this
folder: `source/03-protocol-builder`. Use it when the user asks for work like:

```text
Use these ZEIA files to make a new script that ____.
```

## Lab / ZEIA name provenance (required)

This repo must stay **lab-agnostic**. Do not hardcode or invent site, lab,
project, or operator names in agent docs, specs, examples, or committed
artifacts.

- Take script names, Scripts-folder prefixes, worktable names/GUIDs, labware
  labels, catalog/type strings, locations/sites, liquid classes, device aliases,
  and subroutine paths from the **user-provided full ZEIA** (or the activated
  import context mined from it).
- After `import-project`, prefer `inspect-project` / `inspect-script` /
  `list-projects` (or the context manifest) to copy exact strings. Never paste
  confidential lab protocol names into `AGENTS.md`, README examples, or public
  commits.
- Angle-bracket tokens below (`<SourceScriptName>`, `<WorktableNameFromZeia>`,
  `<ScriptsFolder>\<SubroutineName>`, …) are placeholders. Replace them from
  the ZEIA before generate.
- Do **not** teach lab script/folder/worktable/deck location paths or site
  labware labels in this file. Vendor catalog/type strings and external-device
  command names also come from the imported ZEIA (or FluentControl inventory),
  not from hardcoded examples here.

## Test fixtures vs ZEIA (required)

- `tests/fixtures/**` holds **synthetic stubs only** (see
  `tests/fixtures/README.md`). Never treat those specs as a lab template,
  default verification recipe, or source of Scripts-folder / worktable names.
- Site truth is the **user-provided full ZEIA** (and derived context artifacts
  such as `labware_catalog.json`). Mine names from the import; do not copy
  fixture protocol names into new work.
- Do not commit real operator-verification recipes, confidential prompts, or
  lab goldens into git. Optional private golden regeneration is opt-in via
  `TECAN_ENABLE_PRIVATE_GOLDENS` + `TECAN_GOLDEN_SPEC` + `TECAN_GOLDEN_CONTEXT`.

## Command Rules

- Work from this folder: `source/03-protocol-builder`.
- Prefer `python -m fluent_pipeline.cli` or the local venv executable. On this
  machine, tests use `python3 -m ...`.
- Do not run fluentcoder `author`, `chat`, or `deploy`.
- Do not apply raw XML modeling repairs unless the user explicitly approves the
  tradeoff. That means no `--apply-modeling` by default.
- Treat `request.spec.yaml` as the user-request contract and
  `protocol.ir.json` as the generation source of truth. Regenerate Python, GWL,
  XSCR, guides, worktable diffs, validation reports, validation diffs, and
  bundles from these two reviewed artifacts.
- Keep generated verification scripts easy to navigate in FluentControl: keep
  `Operator setup` as the single top-level setup group, then nest detailed
  verification groups under parent groups with `parent_group` when helpful.
  Group names should match the verification topics in the user request (arm,
  device transfers, barcode/capping, etc.); do not invent lab-specific group
  names that are not in the request or ZEIA.
- Treat `command_registry_resource()` as the runtime command reference and
  `source_command_registry_path()` as the editable command reference for
  FluentControl command IDs, aliases, UI command names (`fluentcontrol_name`),
  command families, required fields, pattern types, and manual-step templates.
  Add registry mappings before adding new string matching.
- Before authoring or regenerating any external tool command
  (`LegacyDriverMacro`, `ApplicationDriverMacro`, unknown/passthrough command,
  or vendor device macro), inspect existing ungenerated XSCR usages first:

  ```bash
  python3 -m fluent_pipeline.cli inspect-external-command <VendorCommandName> \
    --module <VendorModule> \
    --source-script "<SourceScriptName>" \
    --context "<context-name>" \
    --json-out ready-to-import/<project>/temp_files/<script>/external_command_contract.json \
    --markdown-out ready-to-import/<project>/temp_files/<script>/external_command_contract.md
  ```

  Copy `<VendorCommandName>`, `<VendorModule>`, and `<SourceScriptName>` from a
  real usage in the imported ZEIA. Use one matching source usage as the contract.
  Preserve its macro/module
  identity, complete execution-settings layout, referenced variable names and
  types, assignment/condition chain, companion run/wait ordering, timeout,
  disabled state, and required labware/device context. Never splice a method
  from one source usage together with variables or timing from another. Keep the
  contract report in the build and ready-bundle reports. If the command has no
  existing source usage or documented vendor contract, do not generate it.
- When the user reports a FluentControl import/open/runtime dialog, collect the
  current machine evidence first with the maintained BAT, then debug from the
  generated `temp_files/error_logs_MM-DD-YYYY_vN/diagnosis.md` /
  `diagnosis.json`. The in-script profile reads the live FluentControl embedded
  Infopad as well as copied logs, so it captures Script Editor rows such as
  `005: Name must not be empty.` that are not modal dialogs. From the repo root
  run the published bundle, never the source-template BAT, so all runtime
  artifacts stay under `ready-to-import/<project>/temp_files/`:
  `ready-to-import/<project>/run_tecan_bundle_setup.bat --logs-only --log-profile script-errors --no-pause`
  for in-script/runtime errors, or swap `script-errors` for `everything`,
  `program-crash`, or `import-errors` as needed. The primary copied log path is
  `error_logs_MM-DD-YYYY_vN\loggingserver_logfiles\LogFile *.ulf`; the audit
  trail for import/delete history is copied under
  `error_logs_MM-DD-YYYY_vN\visionx_audit_trail\AuditTrail_*.csv`. Use
  `python -m fluent_pipeline.cli parse-fluent-log` only for focused follow-up
  after the BAT diagnosis is captured.
  For hardware-run stops, reconcile the AuditTrail operator-facing message with
  the ULF execution lines before calling it a crash or collision. An RGA
  "labware lost during transport" pause is a movement/labware/grip problem, not
  a software crash unless a matching dump/WER/Event Log fault exists. Keep
  handled Pegasus/TouchTools warnings separate from the stop condition unless
  they occur at the same command and halt execution.

## Default New-Script Workflow

## Regeneration Preflight (mandatory)

> Detailed rules: [AGENTS.details/regeneration-preflight-mandatory.md](AGENTS.details/regeneration-preflight-mandatory.md). Read them before working in this area.

## Generated Script Naming (version bump)

Every `generate` pass **always** version-suffixes the FluentControl script name so
the packaged `generated_project.zeia` adds a **new** script entry instead of
replacing a prior import:

- Unversioned requested name `MyScript` → `MyScript_v2` (minimum suffix is `_v2`).
- Already versioned `MyScript_v5` → `MyScript_v6` (or higher when `_v6+` already
  exists in the base ZEIA / local FluentControl inventory).
- Keep the human-facing name in `request.protocol_name` or the recipe; the workflow
  writes the bumped name to `protocol.name`, the compiled XSCR, and
  `generation.script_naming` in `request.spec.yaml`.

Inspect `generation_manifest.json` → `script_naming` and `RECREATE_SCRIPT.md` for
the final import name. Do not set `allow_script_replacement: true` in the spec to
reuse an old name; each regeneration should import cleanly as a new script.

## Request Spec Resolution (regeneration)

Regeneration must not reuse stale workspace `.../ready-to-import/<project>/temp_files/generations/...-vN/request.spec.yaml`
paths when a newer reviewed spec exists. Prefer:

```bash
python3 -m fluent_pipeline.cli generate --spec latest:<ProtocolName> ...
# or
python3 -m fluent_pipeline.cli generate --spec latest --protocol-name <ProtocolName> ...
```

Resolution scans `ready-to-import/*/source/request.spec.yaml` (highest priority),
then `ready-to-import/<project>/temp_files/build/generations/*/request.spec.yaml`.
Passing a versioned
generation or bundle path auto-upgrades to the newest matching spec unless
`--pin-spec` is set. Use `--pin-spec` only when you intentionally want an older
pinned spec.

During `generate`, when spec resolution yields a `source_bundle_dir` (a prior
ready-to-import bundle) and the spec is not pinned, the workflow seeds the new
build's `media/` from that bundle's `media/processed/` (and optionally
`source/media-originals/` from `media/unprocessed/`) before placeholder slots are
created. Real media is matched by slot basename; placeholders are skipped and
existing human drops are not overwritten.

Generation resolves the source of protocol steps with one strict precedence:

1. An explicit protocol IR supplied with `--ir`.
2. An explicit `verification_recipe` in the request spec.
3. A same-name regeneration baseline only when
   `generation.preserve_regeneration_baseline: true` (or
   `--preserve-regeneration-baseline`) is explicitly set.
4. Automatic synthesis from selected source scripts and patterns.

A same-name baseline discovered for an IR- or recipe-driven run is context only.
It may supply workspace identity, dependencies, subroutines, and filesystem
references, but it must not replace the requested steps. Treat a non-boolean
`generation.preserve_regeneration_baseline` as a spec error, and fail when the
option is true but no same-name baseline exists.

## Existing Script Minimal Edits

When the user asks to edit or fix an existing script from a ZEIA/imported
context, default to a surgical edit workflow, not fresh generation. Resolve and
stage the original `.xscr`, edit only the requested command(s), then compare with
the library helper (there is no `minimal-edit-diff` CLI verb):

```bash
python3 - <<'PY'
from pathlib import Path
from fluent_pipeline.minimal_edit import compare_xscr_minimal_edit, write_minimal_edit_reports

report = compare_xscr_minimal_edit(
    original=Path("ready-to-import/<project>/temp_files/minimal_edit/original.xscr"),
    edited=Path("ready-to-import/<project>/temp_files/minimal_edit/edited.xscr"),
    allowed_command_indexes={0},  # replace with approved indexes
)
write_minimal_edit_reports(
    report,
    json_path=Path("ready-to-import/<project>/temp_files/minimal_edit/minimal_edit_diff.json"),
    markdown_path=Path("ready-to-import/<project>/temp_files/minimal_edit/minimal_edit_diff.md"),
)
print(report.get("status") or report)
PY
```

The minimal-edit report must pass before packaging or handoff. If it reports
unapproved changed/added/removed commands, undo the unrelated drift or get
explicit user approval for the extra command indexes. Use `generate` only when
the user asks for a new script or for broad regeneration.

Broad `generate` regen from `latest:` or an auto-upgraded ready-to-import
`request.spec.yaml` seeds the new build `media/` from prior bundle
`media/processed/` (and `source/media/`) before placeholder slots are created.
Matching basenames inherit the newest non-placeholder capture across all
ready-to-import bundles for the protocol stem. Use `--pin-spec` to disable both
spec auto-upgrade and media seeding.

## Existing Script Media Refresh

> Detailed rules: [AGENTS.details/existing-script-media-refresh.md](AGENTS.details/existing-script-media-refresh.md). Read them before working in this area.

## Files Agent May Edit

- `fluent_pipeline/**/*.py`
- `fluentcoder/**` (first-party authoring/simulation/compiler package)
- `config/aliases/**/*.yaml` (generic vendor instance→type maps only; no site
  labware / tube-runner / lab protocol names — those come from ZEIA
  `labware_catalog.json`)
- `tests/**/*.py`
- `tests/fixtures/**` — **synthetic stubs only** (see `tests/fixtures/README.md`)
- `tests/protocols/**`
- `templates/**`
- `docs/**/*.md`
- `README.md`
- `AGENTS.md`
- Generated workflow artifacts only under `ready-to-import/<project>/temp_files/**`.
  Each context uses `ready-to-import/<context>/temp_files/`.
- Shared tooling + setuptools scratch under
  `ready-to-import/_shared/temp_files/build/` (indexes, reports, api_v2 outputs,
  and each package `setup.cfg` `build-base` → `setuptools/<package>/`). Do not
  recreate `source/*/build/`.
- Workflow event logs under `ready-to-import/_shared/temp_files/logs/`.
- Validated import bundles use direct `ready-to-import/<protocol>_vN/` folders,
  written by packaging.

## Files Agent Must Not Edit Without Explicit Approval

- `ready-to-import/**` by hand. Let protocol-builder packaging write this tree.
  Packaging must never replace an existing ready bundle. Regenerating or making
  another script with the same bundle name writes the next free versioned sibling
  (`<name>_v2`, `<name>_v3`, ...), preserving the older folders as backups.
- Imported source archives and extracted source files under
  `ready-to-import/<project>/temp_files/`, except when the user explicitly
  asks to inspect or copy them.
- Any FluentControl installation or datastore location.
- Do not expand `tests/fixtures/**` into real lab verification recipes or
  confidential goldens. Keep private goldens outside the public tree.

## Required Verification

- For code changes, run:

  ```powershell
  ..\..\scripts\test\test-fast.ps1
  ```

  Use `..\..\scripts\test\test-mcp.ps1` for MCP changes, `..\..\scripts\test\test-simulator.ps1`
  for simulator-gated changes, and `..\..\scripts\test\test-all.ps1` when you want
  the full sweep.

- For generation work, inspect these artifacts before claiming readiness:

  ```text
  GENERATION_WORKFLOW.md
  generation_manifest.json
  request.spec.yaml
  validation_diff.md
  ready_validation.md
  worktable_changes.md
  worktable.patch.json
  RECREATE_SCRIPT.md
  media_placeholders.md
  ```
   <!-- BEGIN GENERATED: readiness-gate-summary -->
   Readiness registry summary (generated from `fluent_pipeline/data/readiness_gate_registry.json`):
   - Required offline ready-to-import gates: `26`
   - Optional diagnostics: `1` (`Gate 27`)
   - Current active entries: `27`
   - Stable IDs are the contract; gate numbers are display labels only.
   - Authoritative table: [Readiness Gate Registry](docs/READINESS_GATES.md)
   <!-- END GENERATED: readiness-gate-summary -->

   <!-- BEGIN GENERATED: readiness-gate-summary -->
   Readiness registry summary (generated from `fluent_pipeline/data/readiness_gate_registry.json`):
   - Required offline ready-to-import gates: `26`
   - Optional diagnostics: `1` (`Gate 27`)
   - Current active entries: `27`
   - Stable IDs are the contract; gate numbers are display labels only.
   - Authoritative table: [Readiness Gate Registry](docs/READINESS_GATES.md)
   <!-- END GENERATED: readiness-gate-summary -->

   <!-- BEGIN GENERATED: readiness-gate-summary -->
   Readiness registry summary (generated from `fluent_pipeline/data/readiness_gate_registry.json`):
   - Required offline ready-to-import gates: `26`
   - Optional diagnostics: `1` (`Gate 27`)
   - Current active entries: `27`
   - Stable IDs are the contract; gate numbers are display labels only.
   - Authoritative table: [Readiness Gate Registry](docs/READINESS_GATES.md)
   <!-- END GENERATED: readiness-gate-summary -->

- If validation fails, leave artifacts in the build folder and report the
  blocking gates. Do not copy or hand-create a ready-to-import bundle.

   <!-- BEGIN GENERATED: readiness-gate-summary -->
   Readiness registry summary (generated from `fluent_pipeline/data/readiness_gate_registry.json`):
   - Required offline ready-to-import gates: `26`
   - Optional diagnostics: `1` (`Gate 27`)
   - Current active entries: `27`
   - Stable IDs are the contract; gate numbers are display labels only.
   - Authoritative table: [Readiness Gate Registry](docs/READINESS_GATES.md)
   <!-- END GENERATED: readiness-gate-summary -->

## Declarative Verification Recipe (skip hand-built IR)

> Detailed rules: [AGENTS.details/declarative-verification-recipe-skip-hand-built-ir.md](AGENTS.details/declarative-verification-recipe-skip-hand-built-ir.md). Read them before working in this area.

## Interactive Operator Scripts (Query Variable)

When the user asks for a script that **interacts with the operator**, collects
**answers**, or asks **questions** (not photo/video verification), use Fluent
Control **Query Variable** steps — not `User Prompt`.

Set `generation.interactive: true` in `request.spec.yaml`, or write explicit
`query_variable` recipe steps. The workflow promotes non-media question prompts
to `query_variable` automatically when interactive mode is detected.

### Required setup

1. Declare each answer variable under `verification_recipe.variables` (or let
   promotion create it):
   - `name`, `type` (`double`, `int`, `string`, …), optional `default_value`,
     optional `minimum` / `maximum`, optional `query_prompt`.
2. Add one `query_variable` step per question:
   ```yaml
   - query_variable:
       variable: <VariableNameFromZeiaOrSpec>
       prompt: "<Operator question text>"
       minimum: 1
       maximum: 96
   ```
   Shorthand: `query: {variable: <VariableNameFromZeiaOrSpec>, prompt: "..."}`.
3. Seed simulation values when needed:
   `verification_recipe.simulation_values: [{name: <VariableNameFromZeiaOrSpec>, value: 2}]`.

### Command choice

| Goal | Use |
|------|-----|
| Collect a typed answer stored in a variable | `query_variable` / Query Variable |
| Yes/no or photo-based verification | `prompt_user` (+ media when teaching) |
| Multi-field TouchTools form | `runtime_variable_prompt` / Run Time Variable Prompt |

Do **not** use `prompt_user` for numeric/text questions that downstream script
logic must read from a variable.
Keep `runtime_variable_prompt` instructions short because FluentControl cuts off
long text. For verification toggles, use exactly:
`For each test, leave it on "yes" to run it or set it to "no" to skip it.`

## Verification / Teaching Prompts and Prompt Media

> Detailed rules: [AGENTS.details/verification-teaching-prompts-and-prompt-media.md](AGENTS.details/verification-teaching-prompts-and-prompt-media.md). Read them before working in this area.

## Manual Step Locations (RECREATE_SCRIPT.md)

> Detailed rules: [AGENTS.details/manual-step-locations-recreate-script-md.md](AGENTS.details/manual-step-locations-recreate-script-md.md). Read them before working in this area.

## Catalog Index Cache

> Detailed rules: [AGENTS.details/catalog-index-cache.md](AGENTS.details/catalog-index-cache.md). Read them before working in this area.

## Long Runs vs. Hangs

> Detailed rules: [AGENTS.details/long-runs-vs-hangs.md](AGENTS.details/long-runs-vs-hangs.md). Read them before working in this area.

   <!-- BEGIN GENERATED: readiness-gate-summary -->
   Readiness registry summary (generated from `fluent_pipeline/data/readiness_gate_registry.json`):
   - Required offline ready-to-import gates: `26`
   - Optional diagnostics: `1` (`Gate 27`)
   - Current active entries: `27`
   - Stable IDs are the contract; gate numbers are display labels only.
   - Authoritative table: [Readiness Gate Registry](docs/READINESS_GATES.md)
   <!-- END GENERATED: readiness-gate-summary -->
