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

