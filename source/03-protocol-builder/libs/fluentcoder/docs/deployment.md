# Deployment — getting a compiled `.xscr` into FluentControl

This doc covers the step *after* `fluentcoder compile`: you have a
`.xscr` file on disk, now you need FluentControl to open it. The
compile path itself is in [compile-path.md](compile-path.md). This document
keeps the public review surface to the fluentcoder-facing workflow; local
reverse-engineering notes are intentionally not included.

## The short version

```
fluentcoder compile examples/simple_transfer.py -o /tmp/build.xscr
```

…then either

- **(A) Drop-in** — rename the file to a fresh `<guid>.xscr`, copy
  into `C:\ProgramData\Tecan\VisionX\DataBase\UserSpecific\`, launch
  FluentControl. The script appears under its `<ObjectSubfolderPath>`.
- **(B) Shell-patch** — overwrite the payload of an existing
  UserSpecific "shell" `.xscr` (its filename GUID stays the same) and
  open FC against it. This is what `fluentcoder.authoring.fluentcontrol_shell`
  does for automated UI validation.

(A) is for "publish a new script". (B) is for "iterate fast against
the same slot". Both are documented below.

## Why this works

`.xscr` files are XML datastore objects under `UserSpecific\<guid>.xscr`.
FluentControl enumerates that directory at startup and reads each
file's `<Payload>` for `ObjectName` / `ObjectSubfolderPath` /
`Reference`. There is no separate index or registry — the directory
*is* the catalogue. Any file with:

1. a unique filename GUID,
2. a `<Reference><Guid>` that points at an existing
   `WorktableWorkspace` in the same datastore, and
3. a `<Checksum>` accepted by `Tecan.VisionX.DataStore.VxDataStore`,

loads exactly like a script created in the GUI.

`fluentcoder compile` already produces files satisfying (2) and (3) —
the renderer wires up the workspace reference and the post-render
hook embeds a valid checksum via `fluentcontrol_core`. (1) is on
the deployer (you).

## Path A — drop-in

### Prerequisites

- FluentControl **closed**. The datastore is enumerated at startup;
  copying while FC is running is unsupported. Verify no `SystemSW.exe`
  process is running.
- A compiled `.xscr` from `fluentcoder compile`.

### Steps

```powershell
# 1. Compile.
fluentcoder compile examples/simple_transfer.py -o D:/staging/build.xscr

# 2. Generate a fresh GUID.
$newGuid = [guid]::NewGuid().ToString()
"FILE_GUID=$newGuid"

# 3. Verify the checksum is valid (sanity check — compile already does this).
# If fluentcontrol_core is not installed, install or expose it in your own env.
python -c "from fluentcontrol_core.checksum import inspect_checksum; import json; print(json.dumps(inspect_checksum('D:/staging/build.xscr'), indent=2))"

# 4. Drop into the datastore under the new GUID.
Copy-Item "D:/staging/build.xscr" `
          "C:/ProgramData/Tecan/VisionX/DataBase/UserSpecific/$newGuid.xscr"
```

Launch FluentControl. The script appears under whatever
`<ObjectSubfolderPath>` it was authored with (this is set in the
renderer config at `fluentcoder/_assets/config/generation.yaml`, and is
authored-side, not deployer-side).

### What to edit before the drop, if anything

Usually nothing — `fluentcoder compile` produces a self-consistent file.
Two cases where you may want to edit:

- **Naming collisions.** If the same `<ObjectName>` already exists in
  the same `<ObjectSubfolderPath>`, FC will show two entries with the
  same name. Edit `<ObjectName>` to disambiguate, then rerun
  `rewrite_checksum(path, in_place=True)` from
  `fluentcontrol_core.checksum` to refresh the `<Checksum>`.
- **Cloning.** If the source is itself a UserSpecific file (e.g. you
  decompiled then recompiled) and you want both old and new visible
  in the tree, generate a fresh `<VxWorkspaceDelta><Identifier>` GUID
  in addition to the filename GUID, then re-checksum.

In both cases, **never** edit the file after the checksum without
recomputing it — the loader rejects mismatches.

### Required vs. preserved fields when editing

| Field | Cloning a file | Why |
|---|---|---|
| Filename GUID | New | Must be unique in `UserSpecific\` |
| `<ObjectName>` | Change to new name | Disambiguates in the script tree |
| `<ObjectSubfolderPath>` | Optional | Folder location in tree |
| `<Reference><Guid>` | Keep | Workspace must already exist in datastore |
| `<Reference><ObjectName>` | Keep | Cosmetic match against the workspace |
| `<BaseWorkspaceName>` (= workspace GUID) | Keep | Cross-checked against `<Reference>` |
| `<VxWorkspaceDelta><Identifier>` | New | Per-script delta identity |
| `<Checksum>` | **Recompute last** | Validated on every load |

Do not edit `dataStoreVersion`, `Script version`, `dataVersion`, the
`*DataVn` command type names, or `<ObjectAttributes>` — these are
pinned to the installed FC build.

### Verification

```powershell
# Confirm checksum is valid after any edits.
python -c "from fluentcontrol_core.checksum import inspect_checksum; import json; print(json.dumps(inspect_checksum('C:/ProgramData/Tecan/VisionX/DataBase/UserSpecific/<FILE_GUID>.xscr'), indent=2))"
# Expect: is_valid: true, matches_stored_checksum: true
```

Then launch FC and check the script tree.

## Path B — shell-patch (fast iteration)

`fluentcoder.authoring.fluentcontrol_shell` keeps a long-lived UserSpecific
"shell" script (configured by path/GUID in local settings) and rewrites
*just the payload region* of that file in place. The shell GUID and
`<ObjectName>` never change, so FC continues to see the same single
entry — but its contents are now your latest compile output.

This is what the LM authoring loop uses for headless UI validation:
patch shell → open it in FC via UI automation → scrape the InfoPad for
errors → close, repeat. See `fluentcoder/authoring/fluentcontrol_shell.py`
and the entry points in `fluentcoder/authoring/tools.py`.

When to use shell-patching instead of drop-in:

- You want one stable entry in the script tree that always reflects
  the current build.
- You're driving FC from automation and don't want to manage a growing
  pile of GUIDs.
- You want to use the InfoPad-scraping validator
  (`fluentcontrol_shell.run_shell_validation`).

When *not* to use it:

- You want both old and new visible in the tree → use drop-in.
- You're publishing a versioned protocol → use drop-in with a
  meaningful `<ObjectName>`.

## Limitations

- **No semantic validation in the deploy step.** Drop-in only proves
  the *loader* accepts the file. Whether the script is *runnable* —
  worktable references resolve, `*DataVn` command versions match the
  FC build, liquid classes exist, etc. — is the editor's
  context-check subsystem, which runs at script-open time and is not
  exposed externally. The InfoPad-scraping path
  (`fluentcontrol_shell`) is the closest thing fluentcoder has to an
  automated context check.
- **No supported public API for inserting scripts.** The V2 runtime
  API (`Tecan.VisionX.API.V2.dll`) is for `RunMethod` / variable
  I/O, not authoring. The named-pipe `IWcfWorkspaceApi` is internal
  and version-sensitive. File drop is the only practical path today.
- **Workspace must pre-exist.** `<Reference><Guid>` must resolve to an
  existing `WorktableWorkspace` in
  `SystemSpecific\Worktable\<workspace-guid>.xwsp`. fluentcoder does not
  ship workspaces; they live in the FC install.
- **Hot-reload is not guaranteed.** FC enumerates `UserSpecific\` at
  startup. Don't drop files while FC is running.
- **FC build version sensitivity.** All of this was demonstrated
  against `3.5.7000.0` / `3.5.7.63142`. A different build may pin
  different `dataVersion` / `*DataVn` types.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `inspect_checksum` reports `is_valid: false` | File edited without re-checksumming | Run `rewrite_checksum(path, in_place=True)` |
| FC silently doesn't show the script | File copied while FC was running, or wrong directory | Close FC, verify path is `UserSpecific\<guid>.xscr`, relaunch |
| FC modal: "Invalid checksum" | Checksum stale (file edited after embed) | Recompute checksum, re-copy |
| FC modal: "Load operation failed" | `<Reference><Guid>` doesn't resolve, or `dataVersion` mismatch | Confirm workspace GUID exists in `SystemSpecific\Worktable\`; check FC build |
| Two entries with the same name in the tree | Cloned without renaming `<ObjectName>` | Edit `<ObjectName>`, re-checksum, re-copy |
| Editor opens but shows red dot / InfoPad errors | Semantic context-check fail (separate problem) | Use `fluentcontrol_shell.run_shell_validation` to capture errors; iterate the source `.py` |

## Demonstrated shape

The clone path has this shape when copying an existing script under a new GUID
and `<ObjectName>`:

| Field | Value |
|---|---|
| Source | `C:\ProgramData\...\UserSpecific\<source-guid>.xscr` |
| Target filename | `C:\ProgramData\...\UserSpecific\<new-guid>.xscr` |
| `<ObjectName>` | `<new-script-name>` |
| Workspace ref | Existing workspace GUID/name kept |
| New `<VxWorkspaceDelta><Identifier>` | Fresh GUID |
| Final `<Checksum>` | Recomputed checksum |
| Result | Loads in FC under the configured script group/name |

Same procedure works for any compiled fluentcoder output — the only
difference when cloning a hand-authored source is that `fluentcoder
compile` emits a checksum-valid file from the start, so you usually
skip the recompute step unless you edit afterwards.

## Related docs

- [compile-path.md](compile-path.md) — IR → renderer → `.xscr` (the
  step before deployment)
- [decompile.md](decompile.md) — `.xscr` → `.py` (the inverse)
- [cli.md](cli.md) — `fluentcoder compile` command surface
- `fluentcoder/authoring/fluentcontrol_shell.py` — shell-patch + UI
  validation entry points
- Local FluentControl notes and checksum helpers are intentionally not part of
  the public review surface.
