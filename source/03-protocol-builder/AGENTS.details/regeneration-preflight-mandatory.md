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

