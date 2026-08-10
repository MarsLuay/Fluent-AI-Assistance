# Codex Workflow For Protocol Builder

Use this workflow when you want Codex to help create or revise FluentControl
scripts without using API keys.

## First Check

```powershell
cd "source/03-protocol-builder"
python -m fluent_pipeline.cli doctor --install-missing --report ready-to-import/<project>/temp_files/doctor.md
```

This creates the shared repository-level `.venv` and installs the local
protocol-builder/fluentcoder dependencies if they are missing. When later
examples use an explicit Python path from inside this folder, point them at
`..\..\..\.venv\Scripts\python.exe` on Windows. Ask Codex to read
`ready-to-import/<project>/temp_files/doctor.md` if anything still fails. If `ready-to-import/<project>/temp_files/doctor.md` already exists
from a recent successful run, you may skip this check unless simulation or
compile later fails.

## Analyze Existing Scripts

When the user asks to review, critique, debug, or suggest improvements for an
existing FluentControl script, start with the read-only `analyze` command instead
of generating or repairing artifacts:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli analyze "<project.zeia>" `
  --script "<script name>" `
  --error-file ready-to-import/<project>/temp_files/latest_error.txt `
  --latest-log `
  --out-dir ready-to-import/<project>/temp_files/script_analysis
```

`analyze` wraps static diagnosis, script explanation, dependency/subroutine
review, optional log parsing, and improvement suggestions into
`analysis.md` / `analysis.json`. Use the lower-level `script-report`,
`diagnose`, and `parse-fluent-log` commands only when a focused artifact is
needed. Move to `request-spec` + `generate` or `repair-plan` / `repair-draft`
only after the user asks for a changed script.

If the script is saved only inside the local FluentControl program/database and
the task is running on a FluentControl or instrument PC, use the local datastore
source:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli analyze `
  --fluent-script "<script name>" `
  --fluent-folder "<optional folder>" `
  --out-dir ready-to-import/<project>/temp_files/script_analysis
```

This stages a read-only copy of the matched `.xscr` before analysis. If the
script cannot be found locally, ask for a ZEIA/XSCR export or a copied datastore
root via `--fluent-database`.

## Existing Script Minimal Edits

When the user gives a ZEIA with an existing script and asks to edit/fix that
script, do **not** use the fresh-script `request-spec` + `generate` workflow
unless they explicitly ask for a newly generated script. Treat the imported
script XSCR as the source of truth and make the smallest possible command-level
change.

Minimal edit workflow:

1. Import or select the ZEIA context, then resolve the target script from that
   context.
2. Copy the resolved original `.xscr` to a ready-to-import/<project>/temp_files/edit folder and edit only the
   requested command(s). Prefer a direct XML/command patch over decompile and
   regenerate when the fix is localized.
3. Run the minimal-edit guard before packaging or handoff:

   ```powershell
   .\.venv\Scripts\python.exe -m fluent_pipeline.cli minimal-edit-diff `
     ready-to-import/<project>/temp_files/minimal_edit/edited.xscr `
     --context "<context-name>" `
     --script "<script name>" `
     --allow-command-index <original-command-index> `
     --report ready-to-import/<project>/temp_files/minimal_edit/minimal_edit_diff.md `
     --json-out ready-to-import/<project>/temp_files/minimal_edit/minimal_edit_diff.json
   ```

4. The report must show `Status: passed`. If it reports unapproved changes, undo
   the unrelated edits or ask the user to approve the additional command indexes
   before continuing.

For an unknown command index, run `minimal-edit-diff` once without an allowlist
to inspect the changed command index, then re-run with the narrow allowlist. Do
not approve broad command IDs such as all `UserPromptStatement` or all
`AddLabwareDataV1` unless the user explicitly asked for a bulk edit.

## Verification Script Grouping

For newly generated verification scripts, keep setup as one top-level
`Operator setup` group. Put detailed verification sections under concise parent
groups when it improves operator navigation: `Arm verification`, `RGA movement
verification`, and `Tube scanning and capping`. Use an explicit `parent_group`
in the IR for exceptions; otherwise the generator applies the default mapping
for the known verification groups. Do not split setup into
multiple top-level setup groups.

## Existing Script Media Refresh

Use this workflow when the user gives an existing exported script/ZEIA plus new
photos or videos and asks to update the operator prompt media. This is an
existing-script task, not a new-script generation task. Preserve the script logic
and replace media files under the filenames the script already references.

Media refresh workflow:

1. Inventory the user-supplied folder, usually Downloads. Sort recent `.zeia`,
   `.zip`, image, and video files by `LastWriteTime`, and use embedded EXIF or
   `ffprobe` creation timestamps when available. ZIP download times can be less
   useful than image/video capture metadata.
2. Stage the newest script export and raw media in a new handoff folder under
   `ready-to-import/<script-media-refresh-name>/source/download-media-raw/`.
   Extract any image ZIPs into a sibling folder, preserving the raw archive.
3. Extract the root ZEIA being handed off into `source/export-extracted/` and
   identify the target script `.xscr` by `ObjectName`, prompt text, or known
   script name. The root ZEIA is the source of truth; if an older extracted
   folder disagrees with it, delete/re-extract that folder before editing. Do
   not rewrite unrelated scripts in the archive.
4. Parse the target `.xscr` for TouchTools media references:
   `SelectedImagePath`, `CustomDetailImageFilePath`, and `File` values under
   `C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\...`. Extract prompt
   labels from `Prompt`, `LabwareDescriptionGui`, `MessageText`, and
   `CheckMessagePrompt`.
5. Before assigning media or rebuilding the archive, scan the extracted target
   `.xscr` for hardware-sensitive command objects (`ApplicationDriverMacro`,
   RGA/CGA moves, barcode/scanner commands, `AvailableID`/`DeviceAlias` values).
   If FluentControl reports `Command "...TransferLabware" is unknown` or
   `USB:.../CGA:1 is not associated with a scanner instance`, classify it as a
   driver/device readiness problem first. Confirm the relevant hardware is
   powered on, connected, and initialized on the instrument PC before changing
   script logic. Only if the same error persists with hardware ready should the
   workflow consider a source-mined native command XML replacement or an
   explicit operator/manual verification prompt.
6. Generate contact sheets or thumbnails for the supplied images and videos, then
   map captures by visible content first and timestamp order second. The output
   filename must be the exact basename already referenced by the script, for
   example `step_021_video.gif` or `script1_verification_step10.gif`.
7. Convert videos to GIFs with `ffmpeg`, and convert stills to static GIFs when
   the existing script references `*_video.gif`. Keep the slot filename stable.
8. If the user says a prompt was unfinished or no photo/video was taken, do not
   reuse a nearby capture. Generate an explicit missing-capture placeholder GIF
   for that exact slot and list it in `source/MEDIA_ASSIGNMENT.md`.
9. Build the handoff bundle:

   ```text
   ready-to-import/<script-media-refresh-name>/
     RECREATE_SCRIPT.md
     generated_project.zeia
     run_tecan_bundle_setup.bat
     media/
       processed/
         <exact TouchTools basenames referenced by the script>.gif
       unprocessed/
         <exact raw operator captures>
     source/
       MEDIA_ASSIGNMENT.md
       media_assignment.json
       media-originals/
       download-media-raw/
       export-extracted/
       media-assigned-originals/
   ```

   `generated_project.zeia` can be the user's newest export unchanged when the
   script already points at the correct TouchTools basenames. If media paths must
   change, patch only the target `.xscr`, restamp checksums, and run
   `minimal-edit-diff`.
10. Validate before handoff:

   - The ZEIA opens as a ZIP archive (`testzip` returns no bad entry).
   - Rebuilt ZEIA entries match the source ZEIA entry set unless the report
     explicitly explains each added/removed entry.
   - Every unique TouchTools basename referenced by the target `.xscr` exists in
     bundle-root `media/processed/`.
   - Hardware-sensitive command findings are either fixed, intentionally
     preserved for the instrument PC, or listed in the handoff report.
   - `source/MEDIA_ASSIGNMENT.md` lists every slot, source capture, and every
     missing-capture placeholder.
   - The bundle root includes `run_tecan_bundle_setup.bat`, which is limited to
     diagnostics and guarded instrument driver/config snapshots. Its top-level
     menu is `Collect Logs`, `Collect/Install Drivers and Configs`, `Settings`,
     and `Exit`; the detailed choices live in submenus. It collects
     FluentControl/VisionX logs into an `error_logs_MM-DD-YYYY_vN` folder.
   - When you run `run_tecan_bundle_setup.bat` during validation, verify the
     timestamped `error_logs_MM-DD-YYYY_vN` folder exists and contains root
     `diagnosis.md` / `diagnosis.json`. The log collection phase should copy
     matching log files only, not broad empty directory trees, and should not
     produce a zip.

The important distinction is that this workflow updates what existing prompts
display by replacing deployed media files. It should not regenerate the script,
renumber prompt steps, or infer captures for unfinished steps.

## FluentControl Error Log Triage

When FluentControl reports an import, Script Editor, simulation, or runtime
dialog error, check the machine logs before guessing from the dialog text. The
most useful locations are:

- `C:\ProgramData\Tecan\LoggingServer\LogFiles\LogFile *.ulf` - primary
  FluentControl/VisionX application log. Use this for `VX_APPFR_016_005`,
  `InvalidChecksumException`, Script Editor load errors, missing subroutines,
  missing files, and runtime dialogs.
- `C:\ProgramData\Tecan\VisionX\AuditTrail\AuditTrail_*.csv` - import/delete
  audit trail. Use this to confirm which scripts were actually imported or
  deleted, including accidental deletion of source scripts.
- `C:\ProgramData\Tecan\VisionX\AuditArch\Log\auditarch*.log` - audit archive
  status.
- `C:\ProgramData\Tecan\VisionX\TaskHandling\*.log` - task/restart handling.
- `C:\ProgramData\Tecan\VisionX\DumpFiles\*.dmp` - crash dumps only; do not
  open these as text because they can be very large.

Use the maintained BAT first so the same evidence package, settings, import scan,
and likely-cause analyzer are captured before any source-only guessing:

```powershell
.\tools\run_tecan_bundle_setup.bat --logs-only `
  --log-profile script-errors `
  --no-pause
```

Use `--log-profile everything`, `program-crash`, or `import-errors` for those
cases. Debug from the newest `error_logs_MM-DD-YYYY_vN\diagnosis.md` and
`diagnosis.json`. Use the lower-level parser only for focused follow-up against a
specific copied ULF:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli parse-fluent-log `
  "ready-to-import\<project>\temp_files\error_logs_MM-DD-YYYY_vN\loggingserver_logfiles\LogFile 2026-06-15 11.41.49.000.ulf" `
  --report ready-to-import/<project>/temp_files/fluent_log_diagnostics.md
```

Common readings:

- `VX_APPFR_016_005` plus `InvalidChecksumException` / `XML checksum error`
  means FluentControl rejected a datastore object checksum. Rebuild the ZEIA
  through the exporter/checksum path; do not hand-edit the final XSCR/ZEIA
  without restamping checksums.
- `Unable to load selected subroutine` means the generated script points at a
  missing, ambiguous, or incorrectly GUID-mapped subroutine. Resolve against the
  source ZEIA manifest and datastore records. On regeneration, package the direct
  and transitive `TypeId=Script` subdependencies into `generated_project.zeia`
  when their `.xscr` files are present in the source ZEIA or local FluentControl
  datastore; do not leave them as workstation-local assumptions. If the routine
  is missing, ambiguous, or unsafe to reuse, prefer
  `generation.subroutine_error_policy: inline_local_on_error` or mark the recipe
  step for local inlining rather than repeatedly emitting the same fragile
  external `call_subroutine`.
- `Command BCRMicro_Read is unknown` means a called subroutine tree contains a
  legacy BCRMicro driver macro. Check direct and transitive subroutine
  dependencies: a clean-looking scan routine can still load a cap-handler child
  routine that requires the BCRMicro driver. For non-instrument or no-BCR
  targets, add a note that the local PC may be missing the driver and the same
  routine may work on the host instrument PC if BCRMicro is installed there.
  For generated verification scripts, do not keep retrying a subroutine that has
  already produced this error. Inline parseable local commands from the source
  routine, or replace the subroutine with an explicit operator verification
  prompt if the legacy-driver body cannot be represented safely.
- `Variable ... defined with different scopes` means the main script and called
  subroutine both declare the same variable name incompatibly. For new-script
  generation, treat the called subroutine as authoritative for that shared
  variable name: make the main declaration match the subroutine declaration's
  fields exactly. This includes scope, type, query-on-startup flag, query text,
  default/startup value, read-only flag, allowed values, and bounds when present.
  A `Script` scope in the main script and a `Run` scope in the subroutine is a
  real conflict. If the generated main script also needs its old definition for
  unrelated local logic, create a distinct main-script variable such as
  `<Name>_Main` and update only those local references.
- `WorktableVXDataStoreManager.LoadWorkspaceDelta`, `Value cannot be null`, or
  `Parameter name: deltaId` while opening an early RUP Worktable prompt means
  the generated main XSCR likely contains `RUPWorktableStatement` without the
  source script's native `VxWorkspaceData` / `WorkspaceDeltas` block. Regenerate
  from a project context where the selected source script resolves to the
  extracted `.xscr`; the generator should transplant that raw workspace metadata
  before packaging. If the prompt is not an initial deck-position check, convert
  it to `RUPStandardStatement` instead of using Worktable.
- Missing `C:\TubeEye\bin\TEyeClient.exe` on a non-instrument computer is an
  environment warning, not necessarily a script defect; the instrument PC should
  carry that dependency if the verification script must exercise TubeEye.

## Import A Fluent Project

## Regenerate An Existing Protocol

Before reimporting external ZEIA files, resolve the latest matching request
spec from `ready-to-import/` with
`resolve-spec latest:<protocol-name>`. The resolver
prefers an existing delivery bundle over temporary outputs. Inspect that
bundle's `source/` artifacts and reuse its recorded context or collection when
available. Only recreate a context from an external archive if no usable ready
bundle exists.

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli import-project "<project.zeia>" `
  --name my-project `
  --activate
.\.venv\Scripts\python.exe -m fluent_pipeline.cli project-info
.\.venv\Scripts\python.exe -m fluent_pipeline.cli project-find "Plexiglas"
```

Imported projects stay isolated under
`ready-to-import/<project-name>/temp_files/`. When a project is active,
relative draft, build, report, and roundtrip paths resolve inside that
directory.

## Default Path For New Scripts From ZEIA Files

When the user asks:

```text
Use these ZEIA files to make a new script that ____.
```

Resolve the protocol step source in this strict order:

1. Explicit `--ir`.
2. Explicit `verification_recipe`.
3. Same-name baseline only with
   `generation.preserve_regeneration_baseline: true` or
   `--preserve-regeneration-baseline`.
4. Automatic synthesis.

An automatically discovered same-name baseline is contextual during IR- and
recipe-driven runs. It can contribute workspace identity, dependencies,
subroutines, and referenced files, but cannot replace the explicit steps. The
spec linter rejects non-boolean preserve values, and an explicitly requested
preserve run fails if no matching baseline exists.

Use the official generation workflow:

1. Capture the user request in `request.spec.yaml`.
2. Import one or more ZEIA project contexts.
3. If more than one ZEIA is relevant, combine them into a project collection.
4. Inspect scripts and worktable.
5. Select source scripts/patterns.
6. Build `protocol.ir.json`. For interactive operator scripts, set
   `generation.interactive: true` or use `query_variable` recipe steps (see
   `AGENTS.md`).
7. Validate liquid logic with the robotools-style liquid-state model.
8. Generate Python draft.
9. Simulate.
10. Generate repair plan.
11. Apply safe repairs.
12. Compile to `.xscr`. Verification prompts use **mixed RUP routing** by
    default: `deck_presence_check` + `worktable_binding` → `RUPWorktableStatement`
    (initial deck placement only); other media prompts → `RUPStandardStatement`
    with `SelectedImagePath`. Recipe `plain_prompt: true` steps compile to plain
    `UserPromptStatement`. Force one path with
    `generation.verification_prompt_rup: worktable` or `standard`. When
    `generation.target_fluentcontrol_version` is below `3.6`, the workflow
    forces Worktable prompt images regardless of the requested media mode.
    `_inject_prompt_media_image_paths` remains only as a fallback for a plain
    prompt that was not emitted natively; it skips native RUP prompts and logs as
    "Prompt Media Image Fixup (fallback)" in `<protocol>.compile.md`. Deploy
    files from `media/processed/` to
    `TouchToolsData\Images\<ScriptName>_media\` via `run_tecan_bundle_setup.bat`.
    See `docs/PROTOCOL_BUILDER_GUIDE.md` "Operator prompt media".
13. Generate `RECREATE_SCRIPT.md`.
14. Generate `worktable_changes.md` and `worktable.patch.json`.
15. Validate the active readiness registry. Gates 19-22 enforce the
    worktable diff (tip boxes, carriers, device aliases, and deck-layout
    consistency vs. the source worktable); a deck move blocks until approved
    with `--approve-deck-layout` and recorded at `review.deck_layout` (stable ID
    `deck_layout_consistent`, approval key `deck_layout_changes`). Gate 23
    enforces that the generated ZEIA
    ships valid FluentControl checksums; checksum recompute is ON BY DEFAULT and
    offline it uses the vendored pure-Python checksum implementation
    (`fluent_pipeline/checksum.py`, self-verified against embedded known-good
    fixtures) to stamp edited entries, so it passes import-clean without a
    FluentControl machine and without `--waive-checksum-recompute`. The older
    empirical/brute-force backend has been retired. Gate 23 only blocks (needing
    `--waive-checksum-recompute`) in the genuine no-backend case where the
    vendored backend's self-verification fails and no backend can produce valid
    values. If a one-off harness writes `.xscr` directly, restamp the file with
    `fluent_pipeline.checksums.recompute_checksum_bytes(...)` before deriving IR
    or packaging; placeholder/non-hex/stale checksum strings are invalid and
    FluentControl reports them as `VX_APPFR_016_005` with `XML checksum error
    indicates unauthorized modification`. Manual XSCR builders must XML-escape
    every generated text value before checksum stamping, including `>` as
    `&gt;`; raw text such as `Move A -> B` can pass offline XML/checksum checks
    but fail FluentControl Script Editor checksum validation after
    reserialization.
    Gate 24 opens the packaged
    `generated_project.zeia` and blocks on corrupt zip, datastore-metadata
    mismatch, or unpackaged `TypeId=Script` subroutine references; unresolved
    non-script model references are needs-review. Gate 25 (stable ID
    `command_inventory_resolves`) extracts the literal
    `LabwareType`/`LabwareName`/`DeviceAlias`/`AvailableID`/`LiquidClassName`
    strings from the compiled command XML and diffs them against the source
    manifest and alias maps; a name that resolves nowhere blocks (approve with
    `--approve-command-inventory`), and a name whose category has no source
    inventory to check against passes as needs-review. Gate 26 audits subroutines
    ADDED to the base ZEIA (not already present): a metadata defect blocks, while
    clean additions pass as needs-review with a "prefer replace over add" note
    (build the subroutine into the base and re-run so it is replaced). Gate 27
    is an optional FluentControl import/load diagnostic rather than a required
    offline readiness gate; it only runs when requested with a live provider.
    Older notes referred to Gate 28-31 concepts (automated-motion review, extra
    subroutine-load review, prompt coverage, and prompt text quality). Treat
    those as manual review or `validation_diff.md` follow-up items today rather
    than active numbered readiness gates.
    <!-- BEGIN GENERATED: readiness-gate-summary -->
    Readiness registry summary (generated from `../fluent_pipeline/data/readiness_gate_registry.json`):
    - Required offline ready-to-import gates: `26`
    - Optional diagnostics: `1` (`Gate 27`)
    - Current active entries: `27`
    - Stable IDs are the contract; gate numbers are display labels only.
    - Authoritative table: [Readiness Gate Registry](READINESS_GATES.md)
    <!-- END GENERATED: readiness-gate-summary -->
    Check
    the `Trivial passes` line in
    `validation_report.md`: liquid-handling and worktable-resource gates that
    pass with nothing to check are flagged `trivial: true` and must match the
    intended protocol. For prompt-only protocols the "confirm an empty result
    matches intent" warning is auto-resolved: declare `generation.prompt_only: true`
    in `request.spec.yaml` (or leave it `null` and the workflow auto-detects it from
    an IR with no liquid-handling steps), and the report/`validation_diff.md` state
    the empty result is expected.
16. Generate `validation_diff.md` and `validation_diff.json`.
17. Package into `ready-to-import/<protocol>/`. Publication is an atomic
    replacement of the complete V2 delivery folder; failed or
    `validated_not_ready` runs must not create or replace that final folder.

Keep four readiness levels separate when reporting results:

- Import-clean: generated ZEIA checksum/archive health passed or was explicitly
  reviewed. This is not proof that Script Editor can open the method.
- Script Editor load-clean: manual Script Editor open/load check on the target
  FluentControl machine.
- Simulation-clean: offline simulation passed (Gate 7).
- Hardware-run-ready: never certified by this offline workflow; it requires
  target-system dependency, deck, labware, liquid, adapter/finger, prompt, and
  operator review.

Scaffold the workflow first:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli request-spec `
  "Use these ZEIA files to make a new script that ____" `
  --project-archive "<project.zeia>" `
  --source-script "<source script name>" `
  --pattern "<pattern reference>" `
  -o ready-to-import/<project>/temp_files/generated_script/request.spec.yaml
.\.venv\Scripts\python.exe -m fluent_pipeline.cli validate-spec `
  ready-to-import/<project>/temp_files/generated_script/request.spec.yaml
.\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_script/request.spec.yaml `
  --name my-project `
  --out-dir ready-to-import/<project>/temp_files/generated_script `
  --no-simulate `
  --no-compile
```

Run `validate-spec` before `generate` to lint the spec and its
`verification_recipe`. It reports actionable errors/warnings with a path-style
location (for example `verification_recipe.groups[0].steps[2]`) and exits
non-zero on any error, so a malformed recipe fails fast instead of silently
producing an empty protocol IR. `generate` also runs the empty-IR error check
automatically: if a recipe-driven spec would emit zero IR body steps, the run
aborts early with the same message. Errors include a missing intent, an empty
or malformed `verification_recipe` (missing/empty `groups`, all-empty `steps`,
unrecognized step shape, empty prompt/comment text, a nameless subroutine, or a
labware entry with no `label`). Warnings cover defaults the run will fall back
on (labware missing `catalog`/`location`, no resolvable base-ZEIA source, or a
wrong-typed `generation.prompt_only`/`acceptance` field).

To verify reproducibility, regenerate from the same reviewed spec + IR into two
output directories and compare them with `determinism-check`:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli determinism-check `
  ready-to-import/<project>/temp_files/run_a ready-to-import/<project>/temp_files/run_b --root <shared-temp-or-projects-root>
```

It compares every artifact in both directories after normalizing ISO timestamps
(`<TIMESTAMP>`) and the directory/source roots (`<ROOT>`), excluding only the
per-run `logs/*.events.jsonl` telemetry. The protocol IR, Python draft, GWL, recreate
guide, worktable patch, validation diff, and generation manifest must match
byte-for-byte; the command exits non-zero on any mismatch so CI catches
nondeterminism (dict/set ordering, GUID churn) before it shows up as a confusing
diff.

For operator-verification recipes, use ordinary `move`/`manual_move` when the
script should prompt the operator instead of moving hardware. Use
`verified_move` only when the requested check requires the instrument to perform
the move first and then show an operator prompt to confirm behavior. A
`verified_move` remains automated, is tagged as intentional verification motion,
and final generation must use `--approve-automated-motion` so the approval is
recorded in the validation context.
Recipe `subroutine` steps may also include `variable_mappings_start` and
`variable_mappings_end`; use those when reusing source subroutines whose behavior
depends on mapped runtime variables.
Recipes must have one setup group in the generated script. The workflow merges
setup-ish recipe groups (`Setup`, `Operator setup`, deck/labware/instrument
setup) into `Operator setup`; add-labware, startup selectors, initialization
comments, and deck-load prompts should stay in that one group.
Keep `runtime_variable_prompt` instructions short; FluentControl cuts off the
remaining characters. Verification toggle selectors should use:
`For each test, leave it on "yes" to run it or set it to "no" to skip it.`
The default regeneration policy is `generation.subroutine_error_policy:
inline_local_on_error`: healthy subroutines may stay as calls, but missing,
ambiguous, legacy-driver, or otherwise error-producing subroutines should be
inlined into the new script when parseable. If the body cannot be parsed into
local commands, emit a local operator prompt/comment instead of an external
`call_subroutine`. Use `preserve` only when the target instrument has been
explicitly confirmed to carry and load the subroutine.
Recipe `simulation_values` entries seed simulator-only Fluent runtime
expressions before setup. Use them for expressions such as
`MountedFESfinger()<>"Eccentric[001]"` that cannot be declared as normal
variables but must have a sim-time value when a reused subroutine is simulated.

For multiple ZEIA files, create or use a collection and qualify source scripts
with their project context name:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli create-collection my-sources `
  --context source-project-1 `
  --context source-project-2
.\.venv\Scripts\python.exe -m fluent_pipeline.cli request-spec `
  "Use these ZEIA files to make a new script that ____" `
  --collection my-sources `
  --source-script "source-project-1:<source script name>" `
  --source-script "source-project-2:<source script name>" `
  -o ready-to-import/<project>/temp_files/generated_script/request.spec.yaml
.\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_script/request.spec.yaml `
  --out-dir ready-to-import/<project>/temp_files/generated_script `
  --no-simulate `
  --no-compile
```

Then review `ready-to-import/<project>/temp_files/generated_script/request.spec.yaml` as the user contract and
edit `ready-to-import/<project>/temp_files/generated_script/<protocol>.protocol-ir.json` as the generation
source of truth. Use selected scripts and mined pattern reports for exact
command structure. After the spec and IR are reviewed, run the final generation
pass:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_script/request.spec.yaml `
  --context my-project `
  --ir ready-to-import/<project>/temp_files/generated_script/<protocol>.protocol-ir.json `
  --out-dir ready-to-import/<project>/temp_files/generated_script_final
```

Do not compile directly from invented Python when ZEIA context is available.
Plan in IR, generate from IR, simulate, repair, then compile.

## Progress And Event Streaming

`generate` has two independent progress channels (use either, both, or neither):

- `-v` / `--progress`: human-readable stage transitions and `[catalog]` /
  `[package]` / `[media]` heartbeats streamed to stderr, unbuffered. Unchanged.
- `--event-log <PATH>`: override the default workspace `logs/<out-dir>.events.jsonl`
  JSON Lines event stream. `--no-event-log` disables file-based event logging.
  `--event-log-stderr` mirrors the JSON to stderr.

Each event has `ts` (ISO-8601 UTC), `elapsed_ms` (monotonic, non-decreasing),
`stage`, `status` (`start` | `heartbeat` | `info` | `done` | `error`), and
`message`. The stream always opens with a `workflow`/`start` event, emits a
per-stage event (`done`/`info`/`error`), reuses heartbeat call sites for
long stages, and closes with a `workflow`/`done` (or `error`) carrying the
overall `workflow_status`. See `docs/PROTOCOL_BUILDER_GUIDE.md` for the schema
table and an example. Emission is best-effort and never crashes the workflow.

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli generate `
  --spec ready-to-import/<project>/temp_files/generated_script/request.spec.yaml `
  --out-dir ready-to-import/<project>/temp_files/generated_script `
  --no-simulate --no-compile `
  -v --progress
```

## Ready-To-Import Artifact

Every successful generated script must publish a complete V2 protocol delivery
folder:

```text
ready-to-import/
  my_new_protocol/
    my_new_protocol.zeia
    run_tecan_bundle_setup.bat
    RECREATE_SCRIPT.md
    media/
    source/
      collect_tecan_diagnostic_bundle.ps1
      copy_tree_with_progress.ps1
      deploy_touchtools_media.ps1
      install_external_files.ps1
      stall_watchdog.ps1
      delivery_manifest.json
      generation_manifest.json
      GENERATION_WORKFLOW.md
      request.spec.yaml
      protocol.ir.json
      metadata.json
      generated/
        protocol.py
      reports/
```

The `.zeia` is the only FluentControl import deliverable. The surrounding folder
is the human and future-agent delivery bundle. Use
`source/delivery_manifest.json` and `source/generation_manifest.json` for the
artifact inventory. `source/request.spec.yaml` is the user-review contract. If a
worklist or report was not produced, record the absence in the manifest or the
relevant run report. If
`ready-to-import/my_new_protocol/` already exists, stage and validate the new
folder first and atomically replace the old folder only after the V2 validator
passes.
`RECREATE_SCRIPT.md` must be generated from `source/protocol.ir.json`, including the
worktable, chosen labware/liquid classes, and ordered command steps.
If a readable source `.zeia` is available, build the published archive from that
source base and document replacement/addition details in the run reports.
`worktable_changes.md` must compare source ZEIA
worktable/context metadata against `protocol.ir.json` requirements and call out
missing labware, changed deck positions, liquid classes, tip boxes, carriers,
device aliases, worklist paths, and manual FluentControl setup steps. Do not
create extra top-level aliases for generated files in `ready-to-import/`.
`worktable.patch.json` must be generated from the same diff and include
machine-readable operations with `safe`, `needs_review`, or `blocking`
severities.
`source/validation_diff.md` / `.json` must compare `source/request.spec.yaml` against the
generated IR, artifact inventory, worktable diff, and ready validation result.
`source/generation_manifest.json` also includes `readiness_status` and a `readiness`
object. Treat `readiness_status: ready_to_import` with
`readiness.fluentcontrol_load_diagnostic.status: not_run` as a clear next
action: run the optional FluentControl import/load diagnostic or manually open
the generated script in Script Editor before claiming load-clean.

## Ready Validation Gates

Treat generated `.xscr` files as internal compilation intermediates only.
Standalone `.xscr` files are never deliverables, must not persist in normal
generation output directories, and must not be copied into `ready-to-import`.
A generation task is complete only when a strictly validated V2 protocol folder
has been atomically published under `ready-to-import/<protocol>/` and contains
the validated `<protocol>.zeia`. If publication does not occur, the task is
incomplete or blocked.

The full-export command-corpus workflow follows the same handoff boundary.
Run `python -m tools.full_export_command_corpus` with no `--out-dir` for the
normal path; it publishes
`ready-to-import/full_export_command_corpus/full_export_command_corpus.zeia`.
That root ZEIA is the only source of truth and the only import deliverable; do
not publish `direct-imports/` or any standalone `.xscr` for this workflow.
`--out-dir` is only for temporary debug artifacts and must not be treated as the
workflow deliverable.

Ready validation must pass before the workflow may publish the `.zeia`:

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

Reports and manifests stay in the run output folder. If any gate fails, publish
nothing to `ready-to-import`; failed internal artifacts are deleted unless the
run explicitly used `--preserve-failed-artifacts`.

## Regression Corpus

Use `tests/protocols/` as the regression corpus for workflow changes. It covers
`simple_transfer`, `serial_dilution`, `plate_copy`, `worklist_import`,
`mca384_transfer`, and `tip_pickup_cleanup`. Keep these fixtures current when
adding new command families or generation rules. Each folder should include
`input_zeia.json`, `expected_extracted_manifest.json`,
`expected_protocol.ir.json`, `expected_guide_steps.json`,
`expected_worklist_records.json`, `expected_simulation_result.json`, and
`allowed_source_operations.json`. The test materializes temporary `.zeia`
archives from these text fixtures so the corpus stays readable in git.

## Command Registry

Use `source_command_registry_path()` before adding new command-name heuristics.
The registry maps FluentControl command IDs and aliases to canonical IR
operations, UI command titles (`fluentcontrol_name`), command families, pattern
types, required fields, field aliases, and manual-step templates. Reader pattern
mining, protocol IR export, generation planning, ready validation, and
`RECREATE_SCRIPT.md` command-title rendering all consult it first.

## Start From An Existing Fluent Script

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli decompile "<script.xscr>" `
  --context my-project `
  -o drafts/draft.py
.\.venv\Scripts\python.exe -m fluent_pipeline.cli simulate drafts/draft.py `
  --context my-project `
  --report reports/draft_simulation.md `
  --json-out reports/draft_simulation.json
```

Then ask Codex:

```text
Read ready-to-import/my-project/temp_files/drafts/draft.py and
ready-to-import/my-project/temp_files/reports/draft_simulation.md. Explain what this Fluent
script does, identify any catalog or simulation failures, and make the smallest
safe edits needed to get the draft simulating cleanly.
```

## Repair A Decompiled Draft

After simulation, create a project-aware repair plan:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli repair-plan drafts/draft.py `
  --context my-project `
  --simulation-json reports/draft_simulation.json `
  --report reports/draft_repair_plan.md
```

To write a repaired draft:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli repair-draft drafts/draft.py `
  --context my-project `
  --simulation-json reports/draft_simulation.json `
  -o drafts/draft_repaired.py
```

If the plan contains raw XML modeling suggestions and you accept the tradeoff,
opt in explicitly:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli repair-draft drafts/draft.py `
  --context my-project `
  --simulation-json reports/draft_simulation.json `
  -o drafts/draft_repaired.py `
  --apply-modeling
```

Common local failure: a decompiled script may reference catalog occupants that
are missing from the local fluentcoder catalog index. Use:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli alias-list
.\.venv\Scripts\python.exe -m fluent_pipeline.cli alias-resolve catalog "Plexiglas Pane[002]"
.\.venv\Scripts\python.exe -m fluent_pipeline.cli catalog-find "Plexiglas"
.\.venv\Scripts\python.exe -m fluent_pipeline.cli catalog-find "96 Well"
.\.venv\Scripts\python.exe -m fluent_pipeline.cli project-find "Plexiglas" --context my-project
```

Known aliases live in `config/aliases/` across `catalog_aliases.yaml`,
`labware_aliases.yaml`, `liquid_class_aliases.yaml`, and
`device_aliases.yaml`. The repair plan, worktable diff, and ready validation
gates use these maps automatically. If the protocol IR itself should be
canonicalized, write a reviewed copy with:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli alias-normalize-ir ready-to-import\<project>\temp_files\protocol.ir.json `
  -o ready-to-import\<project>\temp_files\protocol.alias-normalized.ir.json
```

Then ask Codex to adjust the Python draft or IR to match installed catalog
names only when no configured alias resolves the mismatch.

## Start From A New Protocol Idea

When ZEIA context is not available, create a Python draft in `ready-to-import/<project>/temp_files/` or
`examples/` using the fluentcoder DSL. Codex can use `examples/simple_transfer.py`
as the first pattern.

Useful prompt:

```text
Create a fluentcoder Python protocol draft for this Fluent workflow. Do not use
API authoring tools. Edit a local Python file, then run the protocol builder
simulation command with a report. Stop before compiling if simulation has
warnings or failures.
```

## Canonical IR First

For durable edits, convert scripts to canonical protocol IR before changing the
workflow logic:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli ir-export drafts/draft.py `
  --context my-project `
  -o ready-to-import/<project>/temp_files/draft.protocol-ir.json
```

Review/edit the IR as the source of truth for steps, labware, liquid classes,
deck positions, variables, worklists, dependencies, and safety assumptions.
Then regenerate downstream artifacts from that IR:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli ir-build ready-to-import/<project>/temp_files/draft.protocol-ir.json `
  --context my-project `
  --out-dir ready-to-import/<project>/temp_files/draft_from_ir
```

If the local fluentcoder venv is unavailable, use `--no-compile` to still produce
the IR copy, Python draft, GWL draft, `RECREATE_SCRIPT.md`, and
`worktable_changes.md` / `worktable.patch.json`.

## Compile

Only compile after simulation looks clean:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli compile drafts/draft.py `
  --context my-project `
  -o ready-to-import/<project>/temp_files/draft.xscr
```

The standalone `.xscr` from this command is a developer/debug artifact, not a
generation deliverable. The shared `generate` workflow is the path that can
publish a ready-to-import `.zeia`.

For a full pass:

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli roundtrip "<script.xscr>" `
  --context my-project
```

Successful roundtrips are developer/debug evidence. They must not publish a
standalone `.xscr` into `ready-to-import/`.

## What Codex Should Avoid

For this no-key local workflow, Codex should avoid these fluentcoder commands:

- `author`
- `chat`
- `deploy`

`author` and `chat` are model-authoring surfaces. `deploy` writes into the
FluentControl datastore. Keep generated files in project folders or
`ready-to-import/` and validate them manually in FluentControl.
