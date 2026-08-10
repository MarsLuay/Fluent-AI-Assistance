# Operator Handoff

Use this page when a protocol-builder run finishes and publishes a protocol
folder under `ready-to-import/`. It ties together FluentControl import and what
"ready" actually means. Per-run details live in the protocol folder's
`source/GENERATION_WORKFLOW.md` and `source/generation_manifest.json`.

For Codex-assisted generation steps, see [CODEX_WORKFLOW.md](CODEX_WORKFLOW.md).
For gates, IR flow, and `map-media` details, see
[PROTOCOL_BUILDER_GUIDE.md](PROTOCOL_BUILDER_GUIDE.md).

## Ready-To-Import Layout

```text
ready-to-import/
  <protocol>/
    <protocol>.zeia
    run_tecan_bundle_setup.bat
    RECREATE_SCRIPT.md
    media/
    source/
      delivery_manifest.json
      generation_manifest.json
      GENERATION_WORKFLOW.md
      request.spec.yaml
      protocol.ir.json
      metadata.json
      generated/protocol.py
      reports/
```

A successful generation atomically replaces the whole
`ready-to-import/<protocol>/` folder with a validated V2 delivery bundle. Failed
or `validated_not_ready` runs must not create or replace that final protocol
folder.

Standalone `.xscr` files are internal compilation intermediates only. They are
never ready-to-import deliverables.

The `.zeia` is the only FluentControl import artifact. The surrounding folder is
the complete human and future-agent delivery bundle.

## FluentControl import order

Work on the **target instrument PC** (or a machine with the same FluentControl
dependencies).

1. Copy the entire `ready-to-import/<protocol>/` folder to that PC.
2. Open FluentControl.
3. Import `<protocol>/<protocol>.zeia`.
4. Open the generated script in Script Editor.
5. Read `source/reports/` for checksum status, missing model
   dependencies, media payloads, and archive health before calling the import
   clean.
6. Apply manual deck/labware/liquid setup from `source/worktable_changes.md`
   and `RECREATE_SCRIPT.md` as needed.
7. If the protocol-builder CLI is available, optionally re-check the V2
   delivery structure from the builder tree with
   `python -m fluent_pipeline.cli validate-delivery-bundle <protocol-folder>`.

## Error Log Collection

If FluentControl, Script Editor, simulation, or a runtime script step shows an
error, collect the relevant FluentControl/VisionX logs before changing the
script again. The standard log collection output is:

```text
tecan_error_logs_<timestamp>/
tecan_error_logs_<timestamp>.zip
```

The collector should copy the common Tecan log locations used for
import/load/runtime debugging, including LoggingServer `.ulf` files, VisionX
audit trail CSVs, audit archive logs, task-handling logs, DataStore IoT client
logs, and a listing of large dump files. Send the resulting folder or zip back
with the exact error text.

The bundled setup utility groups log collection into diagnostic packages:

1. Everything
2. In-Script errors
3. Tecan Program Crash
4. Import errors
5. Likely Causes Script

Use `run_tecan_bundle_setup.bat --logs-menu` for the interactive diagnostic
menu, or `--logs-only --log-profile <profile>` for scripted collection. The
legacy `--logs-only` flag still collects the Everything profile. Import-error
packages write `import_error_scan.md` / `.json` for checksum, missing
subroutine, missing file, and ZEIA import/load evidence. Likely Causes Script
also tries to run the local protocol-builder `analyze` / `parse-fluent-log`
pipeline and writes `diagnosis.md` / `.json` into the collected log folder when
the Python CLI is available.

### Do not import the source ZEIA

| Import this | Not this |
| --- | --- |
| The published `<protocol>/<protocol>.zeia` under `ready-to-import/` | Any source/original ZEIA |
| Generated method from the packaged archive | Debug/probe ZEIAs from a build folder |

Source/original exports are audit inputs only. Importing one loads the old
project, not the generated script.

## Media deploy

Verification scripts attach teaching media to operator prompts. Default compiled
media prompts use RUP Standard (`SelectedImagePath`). Text-only confirmations
use plain User Prompt (`UserPromptStatement`). Legacy Worktable mode uses
`CustomDetailImageFilePath`. All deploy to the same TouchTools `Images` folder.

### One visual media slot per prompt

RUP prompt commands should carry at most one visual media file:
`media/<step_id>_video.gif` for on-screen motion or
`media/<step_id>_image.png` for a still image. When the operator workflow needs
both an image and a GIF/video, use two adjacent prompt commands. The exception is
one visual media file paired with an eligible audio/sound file. Replace
placeholder files in place before deploy; slot names must stay the same.

Convert a capture to the GIF slot when needed:

```powershell
python -m fluent_pipeline.cli video-to-gif path\to\capture.mp4 -o media\step_009_video.gif
```

(requires **ffmpeg** on PATH)

For RUP Worktable prompts (`CustomDetailImageFilePath`), prefer still PNG/image
media. The Worktable renderer can display animated GIFs but may flicker or render
portrait motion clips glitchily. If a Worktable prompt really needs motion,
normalize the GIF before deploy:

```powershell
python -m fluent_pipeline.cli normalize-worktable-gif media\step_003_video.gif `
  -o media\step_003_video.gif --overwrite
```

### Deploy to TouchTools

**Bundled support BAT:**

Run `run_tecan_bundle_setup.bat` at the bundle root. It is the sole published BAT
at the delivery root. Top-level menu:

1. Collect Logs
2. Collect/Install Drivers and Configs
3. Deploy TouchTools media
4. Settings
5. Exit

Log collection opens a profile submenu (Everything, In-Script errors, Tecan
Program Crash, Import errors). Driver/config opens collect snapshot / install
snapshot / collect method source. Deploy TouchTools copies `media/processed/`
into `TouchToolsData\Images\<ScriptName>_media\` with a progress bar and SHA-256
checks (`--deploy-touchtools` for non-interactive).

Install paths show the same ASCII progress bars as collect. If a step needs
Administrator rights, the BAT can offer to relaunch elevated. When finished it
can open the `temp_files\` results folder.

Check `tecan_bundle_setup.log` and `deploy_touchtools_images.log` under the
bundle root / temp_files as applicable.

**Option A — manual copy:**

Copy all files from `media/` (or `source/media/`) into the TouchTools Images
folder above. Basenames must match what the script references.

**Option B — path map (no bundle rewrite):**

From the protocol-builder tree, generate a deploy map without editing IR/XSCR:

```powershell
python -m fluent_pipeline.cli map-media ready-to-import\<project>\temp_files\<protocol> `
  --touchtools-dir "C:\ProgramData\Tecan\VisionX\TouchToolsData\Images"
```

See [PROTOCOL_BUILDER_GUIDE.md — map-media](PROTOCOL_BUILDER_GUIDE.md#mapping-media-slots-to-deployed-touchtools-paths-map-media).

Close Script Editor preview windows before deploy if files are locked.

## Readiness boundaries

A passing offline ZEIA archive is **not** the same as safe to run on hardware.

| Term | Meaning | Where to check |
| --- | --- | --- |
| `offline_validation.status: ready_to_import` | Required offline gates passed; generated ZEIA checksums and archive structure are acceptable for FluentControl import | Run output reports and `source/generation_manifest.json` |
| `review_state.status: import_ready_needs_review` | Offline validation passed, but at least one required gate still has a non-blocking review item | Run output reports and `source/generation_manifest.json` |
| `fluentcontrol_load_diagnostic.status: load_clean` | Script Editor opens the generated artifact without load errors | Optional Gate 27 output or manual Script Editor open on the instrument PC |
| `fluentcontrol_load_diagnostic.status: load_failed` | Optional Gate 27 found a Script Editor load problem; offline structure is still valid, but load-clean is false | Run output reports and `source/generation_manifest.json` |
| **simulation-clean** | Offline simulation passed (Gate 7) | Run output simulation report |
| `review_state.status: hardware_review_required` | Default post-validation handoff state. The archive is not a hardware-run certificate. | Operator confirms deck, labware, liquids, adapters/fingers, prompts, and instrument state on the target system |

`readiness_status: ready_to_import` or `import_ready_needs_review` is normal for
offline packaging when `readiness.fluentcontrol_load_diagnostic.status` is
`not_run`. Next step: open the method in Script Editor before treating it as
load-clean or running on hardware.

Scaffold runs (`workflow_status: scaffold_not_validated`) are **not** ready to
import — do not copy them into `ready-to-import/`.

## Quick checklist

- [ ] Protocol folder passes V2 validation during generation or with `validate-delivery-bundle`
- [ ] Published ZEIA is compile-validated (`ready_to_import: true`, not scaffold-only)
- [ ] Imported the published `<protocol>/<protocol>.zeia`, not a source/original export
- [ ] Read the protocol folder reports (checksums, missing dependencies)
- [ ] Deployed prompt media when required by the run output
- [ ] Applied `worktable_changes.md` manual setup
- [ ] Opened script in Script Editor and resolved load errors
- [ ] Confirmed instrument state before hardware run
