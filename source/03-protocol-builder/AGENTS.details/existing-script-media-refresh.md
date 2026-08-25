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

