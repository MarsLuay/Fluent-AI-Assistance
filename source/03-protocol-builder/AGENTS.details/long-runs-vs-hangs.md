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
