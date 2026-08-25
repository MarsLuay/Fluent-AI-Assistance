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
  carries `<protocol>.zeia`, `RECREATE_SCRIPT.md`, one
  `run_tecan_bundle_setup.bat`, root `media/` (`processed/` deploy copies,
  `unprocessed/` raw backups), and `source/`. Helpers, manifests, the reviewed
  spec, IR, metadata, generated Python, and reports all live under `source/`.
- `source/delivery_manifest.json` is the only external-file deployment authority.
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

