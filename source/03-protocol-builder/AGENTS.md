# Agent Command Contract

Project entrypoint (layout + layer order): [`../../AGENTS.md`](../../AGENTS.md).

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

Before importing any external ZEIA or creating a replacement context for an
existing protocol, search `ready-to-import/` for the newest matching delivery
bundle. Use the resolver, not a guessed versioned folder:

```bash
python3 -m fluent_pipeline.cli resolve-spec latest:<protocol-name>
```

The resolver prioritizes `ready-to-import/<protocol>_vN/source/request.spec.yaml`
over temporary generations. Inspect the resolved spec, bundle `source/` tree,
IR, and ready-validation result before deciding whether the original ZEIA must
be imported again. Reuse the bundle's recorded context/collection when it is
available. Import external ZEIA files only when no matching ready bundle exists
or its source evidence is unusable; import and all regenerated artifacts must
stay under `ready-to-import/<project>/temp_files/`.

1. Check the local setup:

   ```bash
   python3 -m fluent_pipeline.cli doctor --install-missing --report ready-to-import/<project>/temp_files/doctor.md
   ```

   If `ready-to-import/<project>/temp_files/doctor.md` already exists from a recent successful run and setup
   has not changed, you may skip re-running doctor to save time unless
   simulation or compile later fails. Otherwise this command must install
   missing local dependencies before generation continues. It creates `.venv`
   when needed, upgrades `pip`, installs `pydantic`, `PyYAML`, and `pytest`,
   then installs this package and `fluentcoder` in editable mode without
   calling author/chat tools.

2. Import ZEIA context:

   ```bash
   python3 -m fluent_pipeline.cli import-project "<project.zeia>" --name "<context-name>" --activate
   ```

   The ZEIA must be a full FluentControl export for new-script generation. It
   should include the source scripts plus referenced worktables, liquid classes,
   labware/system-specific objects, and other dependencies. If the user did not
   clearly provide a full export, or the imported context reports
   `full_zeia_export.status: needs_user`, ask for the full export and wait. Only
   continue with a smaller/non-full/standard ZEIA after explicit user approval,
   recorded with `--approve-partial-zeia` or
   `generation.approve_partial_zeia: true` in `request.spec.yaml`.

3. For multiple contexts, create a collection:

   ```bash
   python3 -m fluent_pipeline.cli create-collection "<collection-name>" --context "<context-a>" --context "<context-b>"
   ```

4. Capture the user request as a reviewable spec:

   ```bash
   python3 -m fluent_pipeline.cli request-spec \
     "Use these ZEIA files to make a new script that ____" \
     --context "<context-or-collection>" \
     --source-script "<source-script>" \
     -o ready-to-import/<project>/temp_files/generated_script/request.spec.yaml
   ```

   Then lint the spec (and any `verification_recipe`) before generating:

   ```bash
   python3 -m fluent_pipeline.cli validate-spec ready-to-import/<project>/temp_files/generated_script/request.spec.yaml
   ```

   `validate-spec` reports actionable errors/warnings with a path-style location
   (for example `verification_recipe.groups[0].steps[2]`) and exits non-zero on
   any error, so a malformed spec/recipe fails fast instead of silently
   producing an empty protocol IR. Always run it before `generate`. (As a safety
   net, `generate --spec` also auto-aborts when a recipe-driven spec would emit
   zero IR body steps.)

   To verify reproducibility, regenerate from the same reviewed spec + IR into
   two output directories and compare them:

   ```bash
   python3 -m fluent_pipeline.cli determinism-check ready-to-import/<project>/temp_files/run_a ready-to-import/<project>/temp_files/run_b --root <shared-root>
   ```

   `determinism-check` compares every artifact in both directories after
   normalizing ISO timestamps (`<TIMESTAMP>`) and absolute output/source roots
   (`<ROOT>`), excluding only per-run `ready-to-import/_shared/temp_files/logs/*.events.jsonl` telemetry. The
   protocol IR, Python draft, GWL, recreate guide, worktable patch, validation
   diff, and generation manifest must match byte-for-byte; it exits non-zero on
   any mismatch so nondeterminism (dict/set ordering, GUID churn) is caught
   before it shows up as a confusing diff.

5. Scaffold generation without runtime-dependent steps:

   ```bash
   python3 -m fluent_pipeline.cli generate \
     --spec ready-to-import/<project>/temp_files/generated_script/request.spec.yaml \
     --out-dir ready-to-import/<project>/temp_files/generated_script \
     --no-simulate \
     --no-compile
   ```

   A scaffold run does NOT run the ready gates. It reports
   `workflow_status: scaffold_not_validated` and `ready_to_import: false` in
   `generation_manifest.json`, writes a `ready_validation.md` that says
   "scaffold only", and the CLI prints `Status: scaffold_not_validated`. Never
   treat a scaffold as validated or copy it into `ready-to-import`; only a final
   pass with compile enabled (step 8) can produce a ready bundle.
   If generation reports `workflow_status: needs_full_zeia_export`, no IR or
   drafts are valid yet; read `full_zeia_export_check.md`, ask the user for a
   full ZEIA export, and wait unless the user explicitly approves
   `--approve-partial-zeia`.

6. When using mined reader patterns, pull exact windows into the spec:

   ```bash
   python3 -m fluent_pipeline.cli request-spec \
     "Use these ZEIA files to make a new script that ____" \
     --context "<context-or-collection>" \
     --index-db "ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite" \
     --pattern-id "<id>" \
     --pattern-query "<query>" \
     --source-script-rank 1 \
     -o ready-to-import/<project>/temp_files/generated_script/request.spec.yaml
   python3 -m fluent_pipeline.cli generate \
     --spec ready-to-import/<project>/temp_files/generated_script/request.spec.yaml \
     --out-dir ready-to-import/<project>/temp_files/generated_script \
     --no-simulate \
     --no-compile
   ```

7. Review `request.spec.yaml` for intent, sources, patterns, generation
   options, review state, and acceptance criteria. Then review and edit only the
   generated IR when protocol behavior changes:

   ```text
   ready-to-import/<project>/temp_files/generated_script/request.spec.yaml
   ready-to-import/<project>/temp_files/generated_script/<protocol>.protocol-ir.json
   ```

8. Run the final generation pass from reviewed spec and IR:

   ```bash
   python3 -m fluent_pipeline.cli generate \
     --spec ready-to-import/<project>/temp_files/generated_script/request.spec.yaml \
     --context "<context-or-collection>" \
     --ir ready-to-import/<project>/temp_files/generated_script/<protocol>.protocol-ir.json \
     --out-dir ready-to-import/<project>/temp_files/generated_script_final
   ```

9. Ready packaging is allowed only when every ready gate passes. The validation
   report must include every required offline gate in the active registry, including post-compile XSCR
   reinspection, XSCR-to-IR roundtrip comparison, volume bounds, well ranges, tip
   capacity, liquid-state validation, liquid-class compatibility, raw XML
   approval, the worktable-resource gates (tip boxes, carriers, device
   aliases, and deck-layout consistency vs. the source worktable), the
   checksum gate (Gate 23) that verifies the generated ZEIA ships valid
   FluentControl `<Checksum>` values, and the packaged-ZEIA gate (Gate 24) that
   opens `generated_project.zeia` itself and checks zip integrity, datastore
   metadata consistency, and reference resolution inside the archive. A
   deck-layout change blocks readiness until it is acknowledged with
   `--approve-deck-layout` and recorded at `review.deck_layout`
   (stable ID `deck_layout_consistent`, approval key `deck_layout_changes`).
   Gate 23 has checksum recompute ON BY DEFAULT: it stamps edited entries with the
   vendored pure-Python FluentControl checksum implementation
   (`fluent_pipeline/checksum.py`, self-verified against embedded known-good
   fixtures, and independently confirmed byte-exact against 41,763 known-good
   datastore entries) when no real `fluentcontrol_core` bridge is importable, so
   offline bundles pass import-clean without a FluentControl machine and without
   `--waive-checksum-recompute`. The older empirical/brute-force checksum backend
   has been retired. Gate 23 only fails when no checksum backend can produce valid
   values (for example if the vendored self-verification fails); then edited
   entries ship blank and it can be waived with `--waive-checksum-recompute`
   (context key `checksums_recompute_waived`) once you accept the in-app
   recalculation prompt or will recompute on a FluentControl machine. That waiver
   should no longer be needed in the normal offline path. Gate 24 runs after packaging writes the archive; a
   corrupt zip or a `meta/content.xml` entry with no matching file blocks
   readiness, while unresolved `<Reference>` GUIDs are needs-review (the model
   must already exist in the target FluentControl system). Gate 25 (stable ID
   `command_inventory_resolves`) diffs compiled
   command XML name strings (`LabwareType`, `LabwareName`, `LiquidClassName`,
   `DeviceAlias`, `AvailableID`) against the source manifest and alias maps; an
   unknown name blocks until approved with `--approve-command-inventory`
   (context key `command_inventory_approved`). Gate 26 audits subroutines ADDED to the base
   ZEIA (those not already present): a metadata defect (missing entry, malformed
   GUID, or no nodedescription node) blocks, and otherwise additions pass as
   needs-review with a "prefer replace over add" recommendation (build the
   subroutine into the base and re-run so it is replaced, reusing its GUID).
   When a new script calls a subroutine, the called subroutine is authoritative
   for any same-name shared variable declaration. If the generated main script
   declares that variable with different declaration fields, make the main
   declaration match the subroutine declaration exactly. This includes scope,
   type, query-on-startup flag, query text, default/startup value, read-only
   flag, allowed values, and bounds when present. Treat `Script` vs `Run` (or any
   other non-equal scope value) as a real conflict, not a harmless spelling
   difference. If the main script also needs the old definition for unrelated
   local logic, create a distinct local variable name such as `<Name>_Main` and
   update only those local references.
   The active numbered readiness registry currently stops at Gate 27. Gate 27 is
   an optional FluentControl import/load diagnostic; it is not part of the
   required offline ready-to-import count and only runs when requested with a
   live provider. Older notes in this file referred to Gate 28-31 concepts
   (automated-motion review, extra subroutine-load review, prompt coverage, and
   prompt text quality). Treat those as manual review or `validation_diff.md`
   follow-up items today rather than active numbered readiness gates.
   <!-- BEGIN GENERATED: readiness-gate-summary -->
   Readiness registry summary (generated from `fluent_pipeline/data/readiness_gate_registry.json`):
   - Required offline ready-to-import gates: `26`
   - Optional diagnostics: `1` (`Gate 27`)
   - Current active entries: `27`
   - Stable IDs are the contract; gate numbers are display labels only.
   - Authoritative table: [Readiness Gate Registry](docs/READINESS_GATES.md)
   <!-- END GENERATED: readiness-gate-summary -->
   Also confirm the
   `Trivial passes` line in the validation report:
   liquid-handling gates that pass with nothing to check are flagged
   `trivial: true`, which is only expected for non-pipetting protocols. The
   "confirm an empty result matches intent" trivial-pass warning is auto-resolved
   for prompt-only protocols: when `generation.prompt_only` is declared in
   `request.spec.yaml` (or the generated IR has no liquid-handling steps, in which
   case the workflow auto-sets it), the validation report and `validation_diff.md`
   state the empty liquid-handling result is expected instead of asking you to
   confirm it. Inspect
   `validation_diff.md` too; it compares `request.spec.yaml` with the generated
   IR, artifacts, worktable diff, and ready validation result.

   Readiness terms are intentionally separate:
   `ready_to_import` / `import-clean` means the offline gates allowed packaging
   and, for generated ZEIA archives, checksum/archive import health is acceptable.
   It does not mean Script Editor load-clean, simulation-clean in FluentControl,
   or hardware-run-ready. Script Editor load-clean requires the optional Gate 27
   FluentControl import/load diagnostic or a manual Script Editor open/load check
   on the target FluentControl machine.
   Hardware-run-ready is never granted by the offline bundle; an operator must
   confirm target dependencies, deck state, labware, liquids, adapters/fingers,
   prompts, and instrument setup before use.

10. The `generated_project.zeia` is **script-scoped** by default:
    - **Windows with FluentControl archive-writer DLLs:** `fluent_archive_writer`
    - **Mac/Linux (or Windows without those DLLs):** `portable_archive_writer`
      (pure Python; same staging + metadata as the Fluent writer)
    - **Emergency full copy only:** set `TECAN_PACKAGE_FULL_ZEIA_COPY=1` to use
      `python_zip_fallback` (preserves every source ZEIA entry and only adds /
      replaces scripts). Do not use that as the Mac default.

    Script-scoped packaging copies the generated script plus packable referenced
    datastore objects from the source ZEIA; no models, components, liquid
    classes, worktables, carriers, or devices are ever fabricated. Worktables /
    liquid classes / components remain target-system prerequisites. Read
    `source/reports/project_import_report.md`: `Base reuse` must show `0` models
    created; `Missing model dependencies` / `dependencies_not_packaged` list
    refs that must already exist in the target FluentControl system before
    import.

11. Checksums determine clean import. FluentControl validates `<Checksum>` on
    every datastore object at load. The pipeline preserves valid checksums on all
    unchanged base entries and recomputes checksums on edited entries via the
    active checksum backend. Checksum recompute is ON BY DEFAULT: the default
    offline backend is the vendored pure-Python implementation in
    `fluent_pipeline/checksum.py` (uppercase MD5 over the inner `<Payload>` for
    `VxData` objects, uppercase SHA-256 over the whole `<Payload>` for
    archive-metadata roots), confirmed byte-for-byte against 3047 known-good
    entries (and re-verified byte-exact against 41,763 known-good entries across
    both branches) and self-verified against embedded fixtures. The older
    empirical/brute-force backend has been retired. A real `fluentcontrol_core`
    bridge is used when importable. Edited entries therefore ship with valid
    checksums and the bundle is import-clean offline without
    `--waive-checksum-recompute`; they only ship blank if no backend can produce
    valid values. Hand-built harnesses that write `.xscr` directly must call the
    same checksum backend before generating IR or packaging; placeholder,
    non-hex, or stale values such as `<Checksum>PLACEHOLDER</Checksum>`,
    `<Checksum>valid</Checksum>`, or `<Checksum>stale</Checksum>` are invalid and
    produce `VX_APPFR_016_005` / `XML checksum error indicates unauthorized
    modification` on import/load. Manual XSCR builders must XML-escape every
    generated text value before checksum stamping, including `>` as `&gt;`; raw
    text such as `Move A -> B` can pass offline XML/checksum checks but fail
    FluentControl Script Editor checksum validation after reserialization. Read the
    `Checksum status` line in `project_import_report.md` and the `checksum_audit`
    in `project_import_report.json` (including `backend_is_vendored` /
    `backend_name`): `import-clean` means FluentControl loads silently;
    `NOT import-clean` means it will reject or prompt to recalculate the listed
    entries. In that case recompute on a FluentControl machine before import, or
    accept the in-app recalculation prompt.
    Also check for target-database script reference rewrites before stamping
    checksums. FluentControl may resolve a same-name subroutine to the local
    datastore GUID during import even when the source ZEIA contains a different
    GUID. The packager must apply that rewrite consistently to the main script
    and every packaged `.xscr` dependency before checksum recompute; otherwise
    the archive can pass offline checksum audit and still fail import with
    `VX_APPFR_016_005` / `InvalidChecksumException` after FluentControl changes
    the referenced GUID.

12. Gate 24 validates the packaged `generated_project.zeia` itself. Read the
    `Import artifact check` line in `project_import_report.md` and the
    `archive_audit` in `project_import_report.json`. `import-ready` means the
    archive opens cleanly; `broken` means a corrupt zip or datastore-metadata
    mismatch blocks import; `needs-review` means unresolved references that must
    already exist in the target FluentControl system.

13. Read `generation_manifest.json` and `GENERATION_WORKFLOW.md` readiness fields
    before handoff. `workflow_status` is the legacy packaging status;
    `readiness_status` and `readiness` distinguish direct XSCR availability,
    generated ZEIA import health, Script Editor load/open status, simulation
    status, and hardware review state. If `readiness_status` is
    `ready_to_import` and
    `readiness.fluentcontrol_load_diagnostic.status` is `not_run`, open the
    generated script in FluentControl Script Editor or run the optional Gate 27
    diagnostic before calling the workflow load-clean.

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

When the user gives an existing exported script/ZEIA plus new photos or videos,
update prompt media as an existing-script media refresh, not as fresh script
generation.

- Use the newest relevant ZEIA export as `generated_project.zeia` unless the
  script's media path basenames must change. If script XML must change, patch
  only the target `.xscr`, restamp checksums, and run `compare_xscr_minimal_edit`.
- Treat the root ZEIA being handed off as the source of truth. Re-extract it
  into `source/export-extracted/` before editing; do not rebuild from an older
  extracted folder if its entry names or media refs differ from the root ZEIA.
- Identify the target script `.xscr` by `ObjectName` and prompt text. Parse
  existing TouchTools media references from
  `SelectedImagePath`, `CustomDetailImageFilePath`, and `File`.
- Scan the extracted target `.xscr` for hardware-sensitive commands and device
  bindings before packaging: `ApplicationDriverMacro`, RGA/CGA moves,
  barcode/scanner commands, `AvailableID`, and `DeviceAlias`. If Script Editor
  reports `Command "...TransferLabware" is unknown` or
  `USB:.../CGA:1 is not associated with a scanner instance`, classify it as a
  driver/device readiness problem first. Confirm the relevant hardware is
  powered on, connected, and initialized on the instrument PC before changing
  script logic. Only if the same error persists with hardware ready should the
  workflow consider a source-mined native command replacement or an explicit
  operator/manual verification prompt.
- Preserve the referenced basenames. If the script points at
  `step_021_video.gif`, produce `media/step_021_video.gif`; do not invent a new
  filename or regenerate prompt steps. Other existing basenames from the
  handed-off script are equally valid and must be preserved.
- When supplied raw files are named `stepN.ext`, interpret `N` as the **current
  final visible prompt number** in the script being handed off. If an older
  pre-renumbering prompt number must be used, require an explicit old-number
  filename/policy (for example `oldstep23.ext`) rather than silently treating
  `step23.ext` as an old prompt. Resolve these labels with
  `assign_step_label_media_to_final_prompts` so removed prompts cannot shift
  captures onto the wrong slots.
- Inventory supplied media by file timestamps and embedded metadata. Use contact
  sheets or thumbnails to match by visible content first and timestamp order
  second.
- Convert videos to GIFs with `ffmpeg`. Convert still captures to static GIFs
  when the existing prompt already references a GIF slot.
- If the user says a step was unfinished or no capture was taken, do not reuse
  a nearby capture. Generate an explicit missing-capture placeholder for that
  slot and record it in `source/MEDIA_ASSIGNMENT.md`.
- Handoff bundles should include `RECREATE_SCRIPT.md`, `<name>_vN.zeia`,
  one root `run_tecan_bundle_setup.bat`, root `media/`, and `source/` with raw
  media, extracted export, assigned originals, `MEDIA_ASSIGNMENT.md`, and
  `media_assignment.json`. (Internal staging may still write `generated_project.zeia`
  before rename/publish.)
- Validate that the ZEIA opens as a ZIP archive and every unique TouchTools
  basename referenced by the target `.xscr` exists in root `media/`. If the ZEIA
  was rebuilt, compare the rebuilt entry set against the source ZEIA entry set
  and document any added or removed entries.

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
  ..\..\scripts\test-fast.ps1
  ```

  Use `..\..\scripts\test-mcp.ps1` for MCP changes, `..\..\scripts\test-simulator.ps1`
  for simulator-gated changes, and `..\..\scripts\test-all.ps1` when you want
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

- If validation fails, leave artifacts in the build folder and report the
  blocking gates. Do not copy or hand-create a ready-to-import bundle.

## Declarative Verification Recipe (skip hand-built IR)

- For operator-verification / teaching scripts, put a `verification_recipe`
  block in `request.spec.yaml` instead of hand-editing the protocol IR. When the
  recipe declares groups, `build_ir_from_recipe` synthesizes the full IR
  (labware + ordered steps) automatically; no `--ir` file is needed.
- Populate every name from the imported full ZEIA / context (see **Lab / ZEIA
  name provenance**). The YAML below is a shape only.
- Shape:

  ```yaml
  verification_recipe:
    # Replace every <...> token with exact strings from the imported ZEIA.
    worktable: "<WorktableNameFromZeia>"       # optional override
    worktable_guid: "<WorktableGuidFromZeia>"  # optional
    labware:
      - {label: "<LabwareLabel>", catalog: "<CatalogExactFromZeia>", location: "<LocationFromZeia>", site: 1}
      - {label: "<PlateLabel>[platecount]", catalog: "<PlateCatalogFromZeia>", location: "<LocationFromZeia>", site: 3}
    simulation_values:
      # Only when the ZEIA/source script needs simulator-only expressions:
      - {name: '<ExpressionFromZeia>', value: 0}
    groups:
      - name: "Operator setup"
        description: "Confirms operator setup and deck load before verification moves."
        steps:
          - prompt: "Confirm external instruments are connected and initialized ..."
            instrument_init_check: true
          - prompt: "Confirm <LabwareLabel> is on the deck in the correct nest."
            deck_presence_check: true
            worktable_binding: <binding_key_for_labware>
          - prompt: "Confirm <PlateLabel> is on the deck in the correct position."
            deck_presence_check: true
            worktable_binding: <binding_key_for_plate>
      - name: "Arm verification"
        description: "Tests and confirms arm positioning."
        steps:
          - subroutine: "<ScriptsFolder>\\<SubroutineName>"
          - prompt: "Confirm gripper fingers are oriented correctly ..."
      - name: "Device / transfer verification"
        description: "Verifies a mined transfer from the source script."
        steps:
          - verified_move: {labware: "<LabwareLabel>", to_location: "<DestLocationFromZeia>", to_site: 1}
          - prompt: "Confirm the labware seated correctly after the move."
    worktable_patterns:            # optional; copy fields from mined source prompts
      example_load_pattern:
        labware: "<CarrierOrHolderLabel>"
        labware_type: "<LabwareTypeFromZeia>"
        grid: 1
        site: 3
  ```

- **RUP Worktable (deck only):** set `deck_presence_check: true` with
  `worktable_binding` on prompts that confirm an item is loaded on the deck
  *before* automated moves (initial placement). These compile to
  `RUPWorktableStatement` with deck labware highlight.
- **RUP Standard (external init + teaching):** set `instrument_init_check: true`
  on prompts that confirm external instruments mined from the ZEIA are connected
  and initialized — include reference media for power-button / init-screen
  walkthroughs. Post-move confirmations, arm checks, barcode/capping teaching
  prompts, and summary prompts also compile to `RUPStandardStatement` with
  `SelectedImagePath`. Do **not** set `deck_presence_check` or `worktable_binding`
  on non-deck prompts.
- Use `plain_prompt: true` only for brief text-only confirmations with no media
  (for example the run identity check at the start of Operator setup).
- **Deck load / presence checks:** use **one prompt per labware item**, each with
  `deck_presence_check: true` and its own `worktable_binding`.
  Do not combine multiple parts into a single deck-load prompt.
- Step types: `comment`, `prompt`, `subroutine` (string or
  `{name, execution_mode, variable_mappings_start, variable_mappings_end}`),
  `move`/`manual_move`
  (`{labware, to_location, to_site, onto}`), and `verified_move`. Each group may
  also declare `description:` — a short purpose comment emitted at the top of that
  group (for example `Tests and confirms arm positioning.`). Do not use `{comment: ...}`
  steps for script/worktable metadata; `validate-spec` warns and the builder drops
  those meta comments automatically. Each labware entry also emits an `add_labware`
  step in the single `Operator setup` group; if a recipe declares multiple
  setup-ish groups such as `Setup` and `Operator setup`, the workflow merges
  them into that one setup group. `move` and
  `manual_move` steps are flagged `force_manual_verification` and become
  manual-verification prompts via `convert_unsafe_rga_adapter_moves_to_prompts`.
  Use `verified_move` only when the user explicitly wants the physical movement
  to run first and a prompt to visually confirm it afterward; it emits an actual
  `move_plate` step tagged `allow_automated_verification_motion` and still
  requires final generation with `--approve-automated-motion` so the approval is
  recorded in the validation context.
  Use `simulation_values` for simulator-only Fluent runtime expressions mined
  from the ZEIA (expressions that cannot be declared as normal variables but
  must be seeded before subroutine simulation).
  Media placeholders and unsafe-move-to-prompt conversion still run automatically
  after recipe synthesis.

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

Author verification/teaching scripts with `verification_recipe` in
`request.spec.yaml` (see **Declarative Verification Recipe** above). Every
`prompt` step becomes a `prompt_user` IR step with text plus image/video media
slots unless interactive mode converts question prompts to `query_variable`.
Inspect `media_placeholders.md`, `media/`, and `RECREATE_SCRIPT.md`
after generation.

### Text rules

- Each `verification_recipe` `prompt` (or hand-authored `prompt_user` step) is
  operator-facing teaching text. Keep checks specific and visual (what to look
  for, pass vs. fail cues).
- Pre-movement prompts must not say "keep your hands clear of the deck" or
  "press OK". Normalize them to end with `Press Continue to proceed.`
- Group `description` and variable-bookkeeping comments are written to
  `RECIPE_GROUP_NOTES.md` in the build/ready folder — never as FluentControl
  Comment statements in the XSCR.
- Verification recipe scripts keep `protocol.comment` empty. Keep the original
  user request only under `source.verbatim_prompt` / `source.intent`.
- `annotate_verification_prompts_with_media` appends a bounded
  `[Reference media: ...]` marker to the prompt text so the runtime prompt flags
  pending media. The marker is stripped in `RECREATE_SCRIPT.md` and prompt-review
  follow-up.
- Seed `acceptance.required_checks` in `request.spec.yaml` from the verbatim
  request when empty; prompt-coverage review matches those lines to emitted
  prompts.
- `verified_move` emits real `move_plate` / TransferLabware commands. The RGA
  move policy keeps those steps when `allow_automated_verification_motion` is
  set or a mined source window is attached; it must not rewrite them into
  "Manual verification only" prompts. Prefer attaching mined source move
  windows from the full ZEIA export via `_attach_source_move_patterns`.
- Unsafe non-approved `move` / `manual_move` steps without a source pattern still
  convert to operator prompts via `convert_unsafe_rga_adapter_moves_to_prompts`.
- When a recipe/IR uses a labware label that the imported full ZEIA already
  places with a specific catalog/type, copy that exact catalog string from the
  ZEIA preferred label→catalog map (worktable geometry / `labware_catalog.json`).
  Shared lint / IR / post-compile gates (`label_catalog_mismatch`) enforce those
  pairs. Without an imported ZEIA preferred map, the pipeline does not invent
  vendor-variant label→catalog pairings. Do not waive or post-patch around these
  checks.
- Recipe `liha_dispense` requires explicit `liquid_class` from ZEIA/recipe
  (never invent AcidExtract). Recipe `a200_dispense` requires `macro_name`,
  `wait_macro`, `module_name`, `execution_settings`, `wait_timeout` (or
  `wait_seconds`), `start_well`, and `end_well` from ZEIA/driver/recipe
  (never invent ResolvexA200_*, SPE 4,~a200startwell~,~a200endwell~,0, or
  wait `300`). `VolTransferMax` is emitted only when that full macro path is
  supplied (structural Fluent var for mined macros — not an SPE invent).
- fluentcoder `generation.yaml` ships empty `worktable` guid/name,
  `device` alias/AvailableID, and `liquid_class` name/guid. Compile requires
  Protocol/ZEIA worktable binding (or fails closed); LC resolves from protocol /
  steps / `liquid_classes.json` / catalog index. Device-bearing commands fail
  closed when `DeviceAlias`/`AvailableID` are unset (no USB invent, no
  alias→AvailableID cross-fill). Renderer must not invent
  `"Empty Tip"` LiquidClassName or rewrite unknown MCA96/FCA tip-box names to
  stock strings. Labware type canonicalize is **exact ZEIA
  `labware_catalog.json` only** (`FLUENTCODER_LABWARE_CATALOG` or
  `labware_catalog.path` in config) — no fuzzy install-DB `_try_correct`.
  Unknown names stay as supplied. RGA `ModuleName` comes from ZEIA/recipe only
  (never invent `"RGA 1"`).
- Plain text-only prompts compile to `UserPromptStatement` (Control Bar ->
  Flow Control -> User Prompt). Use recipe `plain_prompt: true` on setup or
  confirmation-only steps. Other media-bearing prompts use **mixed RUP routing**
  by default: `deck_presence_check` + `worktable_binding` → **RUP Worktable**
  (initial on-deck placement only); `instrument_init_check` and other media
  prompts → **RUP Standard** with `SelectedImagePath`.

### Expression-field registry

- `fluentcoder.expressions.EXPRESSION_FIELDS` is the single authority for XSCR
  command fields that contain FluentControl expressions.
- Decompilation, semantic inventory, XSCR validation, canonical round-trip
  comparison, and final packaged-ZEIA readiness must consume that registry.
  Do not add command/field-specific expression lists in those consumers.
- Add a malformed-field regression whenever a registry entry is introduced;
  the final ZEIA gate must reject malformed expressions in both the main script
  and packaged subroutines.

### Image rules (bundle + compile + runtime)

**Bundle / IR (always):**

- Ready bundles use schema **`tecan.ready_to_import.bundle.v2`**. Bundle root
  carries `<protocol>.zeia`, `delivery_manifest.json`,
  `RECREATE_SCRIPT.md`, one `run_tecan_bundle_setup.bat`, root `media/`
  (`processed/` deploy copies, `unprocessed/` raw backups), root `reports/`,
  and a `source/` tree for IR, reports, and slot files.
- `delivery_manifest.json` is the only external-file deployment authority.
  Each external payload record must contain its exact `bundle_path`, absolute
  `target_path`, and lowercase SHA-256. Never resolve deployment sources by
  recursive basename search. The setup BAT verifies source and installed hashes.
- Ready bundle publication validates V2 layout and delivery_manifest hashes.
  Use `run_tecan_bundle_setup.bat` on the instrument PC for log collection,
  driver/config snapshot install, and TouchTools media deploy (menu or
  `--deploy-touchtools`). There is no `--all` flag; pick the task you need.
- Ready bundle publication is atomic replacement of
  `ready-to-import/<protocol>/`. Stage and validate the replacement folder
  first; failed or `validated_not_ready` runs must not create or replace the
  final protocol folder.
- Every verification `prompt_user` step gets an image slot:
  `media/<step_id>_image.png` recorded in `parameters.media_placeholders`.
- `_create_media_slot_files` pre-creates each missing image slot as a **valid
  dummy PNG** labeled **Replace with image** (template:
  `templates/media/placeholder_image.png`). Slot basename, bundle path, IR, and
  XSCR `CustomDetailImageFilePath` all match a real capture; only the pixels
  are a placeholder until a human replaces the file in place. Re-running never
  clobbers files a human already dropped in. Video slots ship as animated GIF
  placeholders (**Replace with GIF**) at `media/<step_id>_video.gif`; replace in
  place or call `convert_video_to_gif` when a video capture is supplied. During generate,
  raw captures land in `source/media-originals/`; packaging splits bundle media
  into `processed/` and `unprocessed/`. `unprocessed/` is an exact raw operator
  capture copy, not a stale-file quarantine; generated or unreferenced media must
  not be moved there.
- `map-media` maps bundle-relative slots to absolute TouchTools deploy paths
  under `C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\<ScriptName>_media\`
  (see `PROTOCOL_BUILDER_GUIDE.md`). Compile rewrites XSCR paths to the same
  per-script subfolder automatically.

**Compile path (native — mixed RUP after init):**

- Initial deck presence checks (`deck_presence_check: true` + `worktable_binding`)
  emit `wt.user_prompt_worktable(...)` / `RUPWorktableStatement` with deck labware
  highlight. These prompts require the generated XSCR to carry the selected
  source script's native `VxWorkspaceData` / `WorkspaceDeltas` block; otherwise
  FluentControl can fail opening the script with
  `WorktableVXDataStoreManager.LoadWorkspaceDelta` / `deltaId` null around the
  first Worktable prompt. Resolve selected sources through the project manifest
  and keep the post-compile Worktable metadata gate blocking.
- Post-move, teaching, and summary media prompts emit
  `wt.user_prompt(..., image_path=..., rup_kind='standard')` /
  `RUPStandardStatement` with `SelectedImagePath`.

**Runtime on FluentControl:**

- **RUP Standard displays images/GIFs** via `SelectedImagePath` once the
  instrument is initialized (operator-validated after init worktable load).
- Legacy **RUP Worktable** still works with `CustomDetailImageFilePath` when
  `generation.verification_prompt_rup: worktable` is set, but use still images
  by default there. Animated portrait GIFs can flicker or render glitchily in
  Worktable detail images; normalize any required Worktable GIFs first.

**Deploy pattern (validate on the target FluentControl version):**

1. Import script/ZEIA — RUP Standard steps carry `SelectedImagePath` (or
   Worktable steps carry `CustomDetailImageFilePath` when using legacy mode).
2. Copy deploy-ready files from `media/processed/` to
   `TouchToolsData\Images\<ScriptName>_media\` (run
   `run_tecan_bundle_setup.bat` and choose **Deploy TouchTools media**, or
   `--deploy-touchtools`; basenames must match the script paths).
   Standard prompts are GIF-first; Worktable deck-presence prompts use the GIF
   slot for `CustomDetailImageFilePath` (normalized during generation when needed).
3. Run initialization worktable, then preview RUP Standard steps in Script Editor.

**Driver/setup bundle path map:** when adding or repairing
`run_tecan_bundle_setup.bat` driver/config collection, verify against a real
FluentControl install before shipping. Current VisionX folders are
`%ProgramData%\Tecan\VisionX\Config`,
`%ProgramData%\Tecan\VisionX\InstrumentConfigurations`,
`%ProgramData%\Tecan\VisionX\InstrumentInformation`, and
`%ProgramData%\Tecan\VisionX\MapDataBase`. Do not use stale singular paths like
`VisionX\Configuration` or `VisionX\InstrumentConfiguration`. Keep driver
install behind an Administrator preflight so the BAT cannot partially copy
Program Files or ProgramData. Leave `VisionX\DataBase\SystemSpecific` out of the
default driver snapshot unless the user explicitly asks for a full machine
clone; it is much larger and more invasive to install.
When the BAT lists `%ProgramData%\Tecan\VisionX\DumpFiles`, PowerShell pipeline
operators inside quoted `-Command` chunks must be plain `|`, not CMD-escaped
`^|`; the escaped form is passed literally and makes
`visionx_dumpfiles_listing.txt` contain a parser error instead of dump evidence.
Run `fluent_pipeline.bundle_setup.repair_powershell_pipelines` on copied legacy
setup BATs and require `setup_bat_findings(...)` to return no findings before
publishing the bundle.

During `generate`, raw captures are staged under `source/media-originals/` with
`source/reports/media_provenance.json`. Packaging splits bundle `media/` into
`processed/` (deploy) and `unprocessed/` (exact raw operator captures). Any
pre-normalization backups stay in `source/media-originals/`; do not copy stale
generated media into `media/unprocessed/`.

**Auto-process raw captures:** drop files into `media/unprocessed/` (or pass
extra folders) and run::

    python3 -m fluent_pipeline.cli process-media <bundle-or-out-dir>

During `generate`, progress streams to **stderr** by default (use `--quiet` to
suppress). Tail structured timing in ``ready-to-import/_shared/temp_files/logs/<out-dir>.events.jsonl`` or read
``generation_timing.json`` in the build out-dir after a run. Each validation
gate and media phase emits ``duration_ms`` and ``since_previous_ms`` events.

**Generation note:** Set `generation.target_fluentcontrol_version` when the
target FluentControl version is known. Versions below 3.6 force
`generation.verification_prompt_rup: worktable`, so all prompt images use
`RUPWorktableStatement` / `CustomDetailImageFilePath`. Otherwise default
verification bundles use **mixed RUP routing**
(`generation.verification_prompt_rup: mixed` or omit): `deck_presence_check` +
`worktable_binding` → `RUPWorktableStatement` (initial deck placement only);
other media prompts → `RUPStandardStatement`.
Plain text-only prompts (`plain_prompt: true`) compile to `UserPromptStatement`.
Force all media to one path with `generation.verification_prompt_rup: worktable`
or `standard`.

### Video / motion clip rules

- Every verification prompt also gets `media/<step_id>_video.gif` in
  `media_placeholders` (IR kind stays `video`). Generation writes an animated
  placeholder GIF labeled **Replace with GIF**.
- When a human or agent has a video capture, convert it into the named slot with
  the library helpers (there is no `video-to-gif` CLI verb). Prefer
  `process-media` during generate, or call:

    python3 - <<'PY'
    from pathlib import Path
    from fluent_pipeline.media_convert import convert_video_to_gif
    convert_video_to_gif(Path("<video>"), Path("media/<step_id>_video.gif"), overwrite=True)
    PY

  Requires **ffmpeg** on PATH.
- On instrument FluentControl, GIF works on **RUP Standard** (`SelectedImagePath`) after instrument
  init. RUP Worktable (`CustomDetailImageFilePath`) uses a different renderer:
  prefer still PNG/image media for Worktable deck-presence checks. If a
  Worktable prompt must use motion, normalize the GIF first and mark that video
  slot `worktable_display: true` / `worktable_safe: true` in the IR:

    python3 - <<'PY'
    from pathlib import Path
    from fluent_pipeline.media_convert import normalize_worktable_gif, convert_video_to_worktable_gif
    normalize_worktable_gif(Path("media/<step_id>_video.gif"), Path("media/<step_id>_video.gif"), overwrite=True)
    # or from raw video:
    # convert_video_to_worktable_gif(Path("<video>"), Path("media/<step_id>_video.gif"), overwrite=True)
    PY

  Do not point Worktable prompts at raw ffmpeg / `convert_video_to_gif` output
  directly. Prefer `normalize_worktable_gif` / `convert_video_to_worktable_gif`.
  Worktable GIFs must use: full clip, 640x480 canvas, 6 fps, uniform 160 ms
  frames, `disposal=2`, no transparency, full-frame tiles, and `optimize=False`.
  Do not shorten Worktable GIFs by default; use `--max-seconds` only for a
  separate instrument-tested experiment.

### Prompt-image wiring (compile-time mixed RUP — native path)

- Default **mixed** routing (`resolve_verification_prompt_rup_kind` in
  `protocol_ir.py`): `deck_presence_check` + `worktable_binding` →
  `wt.user_prompt_worktable(...)` / `RUPWorktableStatement`; all other annotated
  media prompts → `wt.user_prompt(..., image_path=..., rup_kind='standard')` /
  `RUPStandardStatement`. GIF/video slots are preferred over still-image slots
  for Standard prompts; Worktable prompts should use still images unless the GIF
  has been normalized with `normalize_worktable_gif` and explicitly marked as
  the Worktable display slot.
- **Plain prompts** (`plain_prompt: true`) emit `UserPromptStatement`.
- **Force one path:** `generation.verification_prompt_rup: worktable` or
  `standard`.
- Fallback: `_inject_prompt_media_image_paths` (`generation_workflow.py`)
  upgrades any media prompt still emitted as a plain `UserPromptStatement` by
  replacing it with generated `RUPWorktableStatement` XML wired to the bundle
  media slot (GIF for Worktable deck-presence prompts via `worktable_display`;
  still-image PNG remains an optional reference slot). Prompts already emitted as `RUPStandardStatement` or
  `RUPWorktableStatement` are skipped so slots are never double-assigned.

## Manual Step Locations (RECREATE_SCRIPT.md)

- Every manual step in `RECREATE_SCRIPT.md` includes a `Location:` breadcrumb that
  tells the operator exactly where to grab the command from in FluentControl.
  `_manual_step_location` (in `protocol_ir.py`) fills it automatically; never
  leave it blank (`Location: ___ -> ___ -> ___`).
- Commands map to their Control Bar palette section by registry `family`
  (`_FAMILY_CONTROL_BAR_PATH`) plus the registry `fluentcontrol_name`, for example
  `Control Bar -> Flow Control -> User Prompt` or
  `Control Bar -> RGA / CGA (Robotic Gripper Arm) -> Move Labware (Transfer Labware)`.
- The numbered manual-step title is the FluentControl UI command name followed
  by the generated operator description, for example `Get Tips: Pick up tips...`
  or `Move Labware (Transfer Labware): Move ...`. Keep known command titles in
  `command_registry.json` via `fluentcontrol_name`; `_OPERATION_COMMAND_LABEL`
  is only a compatibility fallback.
- `call_subroutine` steps point into the project Scripts tree from the subroutine
  path mined from the ZEIA, for example
  `Scripts -> <ScriptsFolder> -> <SubroutineName>`. When adding a new
  command family or operation, extend the two maps so its breadcrumb is accurate.
- Each step also lists a `Fields:` block of the command's options. EVERY
  re-creatable command (all operations carrying a Control Bar label) declares a
  full option set in `_MANUAL_OPERATION_OPTIONS` (in `protocol_ir.py`), so the
  block enumerates every configurable option a command exposes — not just the
  IR-populated ones — with a `(default: ...)` / `(not set)` hint for anything
  left at its default. This covers User Prompt, Comment, Query/Run-Time Variable
  prompts, Add Labware, Get/Set-back/Drop Tips, Aspirate/Dispense/Mix,
  Wash, Get/Drop Head Adapter, Move Labware, Initialize, Read Worklist,
  Set/Set-Remaining Variable, Subroutine, If/Else, and Loop. Examples: a User
  Prompt always shows User prompt text, On-screen image (only when the step
  carries an image media slot), Close prompt after (seconds), Change status
  light color, Play sound file, and Repeat sound; Move Labware always shows
  labware, source/destination location+site, onto-labware, move-to-base,
  fixed-site, gripper fingers, and back position. The appended
  `[Reference media: ...]` marker is stripped from the displayed prompt text, and
  a `0`/empty auto-close timeout renders as its default hint. The
  `test_every_modeled_operation_has_a_full_option_set` guard fails if a new
  re-creatable operation is added without a full option set. To add/adjust a
  command's options, edit `_MANUAL_OPERATION_OPTIONS` (label falls back to
  `_MANUAL_FIELD_LABELS`) and, so any set value renders, the matching
  `field_aliases` in `command_registry.json`. If the command appears in
  FluentControl, also set `fluentcontrol_name` to the exact UI command title;
  only raw passthrough statements (no modeled operation) keep the legacy
  "IR-populated fields only" behavior.

## Catalog Index Cache

- Imported full ZEIA worktable geometry also writes
  `ready-to-import/<ctx>/temp_files/labware_catalog.json` (and
  `source/labware_catalog.json` in packaged bundles). That file is the
  lab-specific labware name/component-GUID/`mesh_guid(s)`/dimension catalog,
  plus mined `pipettable` (wells/cavity), `grip` (`AllowedGripModes` + Force),
  `site_templates` (arrangement site GUIDs + xsit metadata when present), and
  `compatible_components` (xcmp refs + workspace occupancy).
  Entries keep `guid` (component) separate from `mesh_guid` / `mesh_guids`
  (WorktableMesh refs used for GLBs). Do not hardcode site labware names or
  host mesh GUIDs into `config/aliases/*.yaml` or the simulator static catalog;
  let import/package populate them. `ready-to-import/` stays gitignored.
- The same import writes `liquid_classes.json` (packaged as
  `source/liquid_classes.json`) from ZEIA `SystemSpecific/LiquidClasses/*.xlqc`
  (and manifest `.xlqc` objects): schema `tecan.liquid_classes.v2` with name,
  filename GUID, supported heads, head×tip `profiles[]` (EquationSet /
  DetectionAndPositioningSet scalars + Microscript section names and ordered
  Object ``Type`` command sequences — not full micro-command payloads), plus
  flattened aspirate/dispense/mix summaries for older consumers. Never invent
  liquid-class name/GUID in shipped `generation.yaml`.
- The same import writes `driver_macros.json` (macro_name/module_name mined from
  script `LegacyDriverMacro` / `ApplicationDriverMacro` usages) and
  `script_folder_bindings.json` (Scripts-folder tree + script↔worktable
  bindings). Init-worktable selection prefers those ZEIA bindings over soft
  filename scoring when present.
- Large ZEIA imports that skip detailed `worktable_geometry` (object-entry
  limit) still build `labware_catalog.json` from a Components `.xcmp` walk when
  possible.
- CapBC / tube-scan prep schema is mined at generate time
  (`subroutine_deck_locations`): CapBC subroutine VariableDefinitions + call
  VariableMappings as schema; GripperClose/Open from those decl defaults or
  source SetVariable (never invented widths); TubeRunnerName from worktable
  placements whose catalog contains the exact phrases `tube runner` /
  `tube holder`. CapBC in the subroutine name is only a soft secondary enable.
- fluentcoder `_assets/reference/labware.yaml` is an optional reference dump
  (Falcon/Resolvex rows). Renderer loads it only when
  `FLUENTCODER_USE_LABWARE_YAML=1`; prefer ZEIA `labware_catalog.json`.
- The same import writes sibling `connector_coverage.json` (packaged as
  `source/connector_coverage.json`): one coverage row per component that has
  connectors in **this** ZEIA/install geometry. `connector_graph.json` prefers a
  full `Connectors/*.xcon` Snap walk under the extracted DataStore (same scope as
  host `build_connector_graph.py --install`), not only connectors already mined
  into detailed `worktable_geometry` (large ZEIA imports skip that parse). Never
  assume soft site-labware family profiles in product source, and never bake host
  connector GUID totals.

- The fluentcoder catalog index (`install_index.db`) is expensive to build from a
  full ZEIA (~9 min cold) and is the dominant cost of a first `generate`.
  `ensure_project_catalog` (in `project_catalog.py`) caches it two ways: a
  per-context DB under
  `ready-to-import/<ctx>/temp_files/build/.fluentcoder_catalog/`,
  and a shared content-addressed cache under
  `ready-to-import/_shared/temp_files/cache/catalog/<hash>/`.
- The cache key is a SHA-256 over the catalog source files (Components, Workspaces,
  Sites, LiquidClasses) by relative path + size + content, so it is independent of
  mtimes. This matters because import re-extracts files with fresh mtimes, which
  invalidates the mtime-based per-context freshness check; the content hash lets a
  re-import or a differently named context reuse a prior build (a fast DB copy,
  ~seconds) instead of rebuilding.
- Hashing ~1.3k files / ~280 MB takes ~6 s and is only paid on a cold context
  (the in-context freshness fast path skips it). A cache write failure never
  blocks generation. To pre-seed the cache from an already-built context, copy its
  DB via `_store_in_shared_cache(project_catalog_db_path(ctx), _shared_cache_db_path(_catalog_content_hash(project_datastore_dir(ctx))))`.

## Long Runs vs. Hangs

- Importing a large full ZEIA export and packaging `generated_project.zeia` are
  genuinely slow (often several minutes each). Both now emit progress
  heartbeats: import prints `  ...` lines and packaging prints `  [package] ...`
  lines (reading the base ZEIA, resolving references across all base entries,
  writing via the FluentControl archive writer, verifying, bundle complete). A
  ~90 MB / ~15k-entry base can spend minutes in the reference-resolution step
  alone. The generation artifacts (IR, `.py`, `.xscr`, `ready_validation.md`,
  `validation_diff.md`) are written well before `generation_manifest.json`,
  which is written last after packaging. Check the out-dir file timestamps or the
  `[package]` heartbeats to confirm progress instead of assuming a hang.
- All external subprocesses are time-bounded (fluentcoder simulate/compile,
  the PowerShell ZEIA archive writer, and the checksum shim), and the CLI forces
  a clean process exit after a command returns. If a `generate` run still
  appears stuck after `generation_manifest.json` exists, the bundle is already
  complete; verify on disk rather than waiting.
- Use `generate -v` / `--progress` to stream every stage transition
  (`[stage NN] <id>: <status> - <summary>`) plus the `[catalog]`/`[package]`
  heartbeats to stderr, unbuffered. Stage lines go to stderr (not stdout), so
  they stay live even when stdout is captured through a PowerShell
  `Tee-Object` pipe. Without the flag, stage transitions are silent and the
  sub-step heartbeats keep printing to stdout as before.
- Use `generate --event-log <PATH>` to override the default workspace
  `ready-to-import/_shared/temp_files/logs/<out-dir>.events.jsonl` event stream. Pass `--no-event-log` to disable
  file-based event logging. JSON Lines fields: `ts`, `elapsed_ms`, `stage`,
  `status` (`start`/`heartbeat`/`info`/`done`/`error`), `message`. It is
  independent of `-v/--progress` (either, both, or neither): the human-readable
  text behavior above is unchanged. Parent dirs are created and the file is
  line-buffered so CI can tail it; `--event-log-stderr` also emits the JSON to
  stderr. The stream opens with a `workflow`/`start`, emits a per-stage
  `done`/`info`/`error`, reuses heartbeat call sites, and closes with a
  `workflow`/`done` (or `error`). Emission is best-effort and never crashes the
  workflow. See `docs/PROTOCOL_BUILDER_GUIDE.md` for the schema and example.
