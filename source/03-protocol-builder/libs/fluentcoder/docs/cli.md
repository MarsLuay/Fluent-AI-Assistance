# CLI

`fluentcoder/cli.py`

The `fluentcoder` command is the operational front-end for the package.
Four top-level subcommands plus a `catalog` group.

```
fluentcoder compile    <path/to/protocol.py>  [--output OUT] [--deterministic]
fluentcoder simulate   <path/to/protocol.py>  [--json] [--strict] [--fail-on-opaque]
                       [--record-snapshots | --no-snapshots | --delta-snapshots]
                       [--subroutine-dir DIR] [--subroutine-xscr FILE]
fluentcoder decompile  <path/to/script.xscr>  [--output OUT.py] [--strict]
                       [--simulate] [--simulate-strict] [--fail-on-opaque]
                       [--record-snapshots | --no-snapshots | --delta-snapshots]
                       [--subroutine-dir DIR] [--subroutine-xscr FILE]
fluentcoder catalog refresh [--install <PATH>] [--db <PATH>]
fluentcoder catalog info
fluentcoder catalog find <pattern> [--category CAT]
```

Deterministic `WorkspaceDelta` GUIDs are available on both paths: CLI
`fluentcoder compile --deterministic` and Python `wt.compile(..., deterministic=True)`.
Snapshot recording mode is exposed on `fluentcoder simulate` (see below) and
maps to `wt.simulate(record_snapshots=...)`.

The `fluentcoder` script is registered via `pyproject.toml`'s
`[project.scripts]` block; running `pip install -e .` from the repo root
makes the command available.

## `compile`

```
fluentcoder compile examples/simple_transfer.py
fluentcoder compile examples/simple_transfer.py -o /tmp/out.xscr
fluentcoder compile examples/simple_transfer.py --deterministic
```

Loads the input `.py` file, calls its `build_worktable()` factory (or uses
a top-level `wt: Worktable` if no factory exists), renders the IR via
`Worktable.compile()`, and writes the `.xscr` to the given path (default:
input filename with the `.xscr` suffix).

For stable `WorkspaceDelta` GUIDs (byte-comparable across runs), pass
`--deterministic` or call `wt.compile(path, deterministic=True)` from Python.

The protocol script must therefore expose either:

```python
def build_worktable() -> Worktable: ...
```

or

```python
wt = ...      # built at module level
```

The CLI loader is in `cli.py:_load_protocol` (`cli.py:101`).

## `simulate`

```
fluentcoder simulate examples/simple_transfer.py
fluentcoder simulate examples/simple_transfer.py --json
fluentcoder simulate decompiled_protocol.py --strict --fail-on-opaque --coverage
fluentcoder simulate protocol.py --subroutine-dir path/to/subroutines
fluentcoder simulate protocol.py --subroutine-xscr path/to/SUB_Foo_v1.xscr
```

Loads the protocol the same way as `compile`, but instead of rendering it
runs `wt.simulate()` and prints a per-step summary.

`--subroutine-dir` registers all `.xscr` files under a directory;
`--subroutine-xscr` registers one file (repeatable). Both feed a
`SubroutineRegistry` so `SubRoutineStep` bodies are inlined during simulation.

Snapshot recording (mutually exclusive; default is full per-step deep-copy):

| Flag | Python API equivalent | Behavior |
|---|---|---|
| *(default)* | `record_snapshots=True` | Deep-copy twin state after every step |
| `--no-snapshots` | `record_snapshots=False` | Keep only the final snapshot (lower memory) |
| `--delta-snapshots` | `record_snapshots="delta"` | Lightweight per-step diffs of changed labware volumes and tip state |
| `--record-snapshots` | `record_snapshots=True` | Explicit default; useful when overriding a wrapper script |

`fluentcoder decompile --simulate` accepts the same snapshot flags when it
runs the post-emit simulator pass.

For decompiled or production-style validation, prefer
`simulate --strict --fail-on-opaque`. `--strict` requires a bound
workspace plus strict slot/catalog semantics. `--fail-on-opaque` upgrades
unmodeled runtime or raw commands from a soft `passed_with_opaque` report
to an exit-1 validation failure.

Default output (text mode):

```
  step   0 AddLabwareStep            labware= 1  tips=  0  tip_vol=0.0 µL
  step   1 AddLabwareStep            labware= 2  tips=  0  tip_vol=0.0 µL
  step   2 AddLabwareStep            labware= 3  tips=  0  tip_vol=0.0 µL
  step   3 GetHeadAdapterStep        labware= 3  tips=  0  tip_vol=0.0 µL
  step   4 PickUpTipsStep            labware= 3  tips= 96  tip_vol=0.0 µL
  step   5 AspirateStep              labware= 3  tips= 96  tip_vol=1920.0 µL
  step   6 DispenseStep              labware= 3  tips= 96  tip_vol=0.0 µL
  step   7 SetTipsBackStep           labware= 3  tips=  0  tip_vol=0.0 µL
  step   8 DropHeadAdapterStep       labware= 3  tips=  0  tip_vol=0.0 µL
```

With `--json`, each snapshot is emitted as a JSON record:

```json
{
  "step_index": 5,
  "step_type": "AspirateStep",
  "labware": ["SourcePlate", "DestPlate", "Tips"],
  "mca_adapter": "EVA[001]",
  "mca_tip_box": "Tips",
  "mca_tip_volume_total_ul": 1920.0
}
```

Useful for piping into `jq` or feeding a downstream tool.

The JSON payload includes the structured simulator report:

- `status`: `passed`, `passed_with_opaque`, or `failed`
- `failure`: `null` on success, otherwise `{category, exception_type, message, ...}`

When `simulate` exits nonzero and a report exists, JSON mode still prints
the report to stdout and returns exit code 1. Text mode prints the failure
category in stderr, for example `Simulation failed [workspace_binding]`.

## `decompile`

```
fluentcoder decompile examples/simple_transfer.xscr
fluentcoder decompile some_lab.xscr -o some_lab.py
fluentcoder decompile some_lab.xscr --strict
```

Inverse of `compile`. Parses the `.xscr` into the Pydantic `Protocol`
IR, then emits a self-contained fluentcoder Python module with a
`build_worktable()` factory. Default output path is the input with
`.py` extension.

Supported prompt types include plain `UserPromptStatement`, image-capable
`RUPStandardStatement` (`SelectedImagePath` →
`wt.user_prompt(..., image_path=..., rup_kind="standard")`), and
`RUPWorktableStatement` (`CustomDetailImageFilePath` →
`wt.user_prompt_worktable(...)` or legacy `user_prompt` without `rup_kind`).
Subroutine calls decode `variable_mappings_start` / `variable_mappings_end`.

Output:

```
Decompiled examples/simple_transfer.xscr -> examples/simple_transfer.py
  groups: 2, steps: 9
```

If any step decoded as `GenericStep` (a type the parser doesn't yet
understand), the count is reported. With `--strict`, presence of any
unrecognised step is an error (exit 1). Without `--strict`, the
decompiler emits a `# [decompiler] unsupported step: <name>` comment
in place and continues.

The decompiled `.py` injects a stand-in `default_reagent` and fills
every Plate/Trough so simulation runs cleanly out of the box. Replace
with real `Reagent(...)` instances to model identity (e.g. beads with
`pinned_when_magnetized=True`). See [decompile.md](decompile.md) for
the full per-step emit table and round-trip parity guarantees.

## `catalog refresh`

```
fluentcoder catalog refresh
fluentcoder catalog refresh --install C:\Custom\Tecan\Database
fluentcoder catalog refresh --db /tmp/alt-index.db
```

Drops and rebuilds the SQL catalog index. By default reads from
`C:\ProgramData\Tecan\VisionX\Database` (override with `--install` or the
`FLUENTCODER_FC_INSTALL` env var) and writes to the resolved index DB
(default: `fluentcoder/catalog/install_index.db`; non-default installs use
`catalog/indexes/install_<hash>.db`; override with `--db` or
`FLUENTCODER_INDEX_DB`).

Output:

```
Catalog index rebuilt:
  components     629
  workspaces     104
  sites          571
  fixed_deck     354
  tip_box        95
  tube_rack      63
  plate          50
  trough         25
  hotel          11
  waste_chute    11
  wash_station   8
  adapter        6
  magnet_rack    6
```

Run this after FluentControl updates that ship new components, or after
adding custom labware to your install.

## `catalog info`

```
fluentcoder catalog info
```

```
Install path : C:\ProgramData\Tecan\VisionX\Database
Built at     : 2026-04-27T00:10:15
Fingerprint  : aa6c5febd25e10576c5b211772f7eb19f7922655d856b752c0c58b1b63dc85c8
Component categories:
  fixed_deck     354
  tip_box        95
  ...
```

Quick sanity check: did the index build, when, against which install,
what's its content distribution.

If the index is empty:

```
Catalog index is empty. Run `fluentcoder catalog refresh`.
```

(exit code 1)

## `catalog find`

```
fluentcoder catalog find magnet
fluentcoder catalog find "96 Well" --category plate
```

Substring search (case-insensitive `LIKE %pattern%`) over component names.
Optional `--category` filters by inferred category.

Output:

```
  [magnet_rack  ] 2 Landscape 7mm Nest Magnet Teleshake Segment
  [magnet_rack  ] 24 Magnet Plate
  [magnet_rack  ] Landscape Nest Magnet Teleshake Segment

3 match(es).
```

Exit 0 on hits, exit 1 on no matches.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `FLUENTCODER_FC_INSTALL` | `C:\ProgramData\Tecan\VisionX\Database` | Where the catalog indexer reads from. |
| `FLUENTCODER_INDEX_DB` | *(derived from install)* | Explicit catalog index DB path. |
| `FLUENTCODER_TEST_CATALOG_DB` | — | Offline/synthetic catalog DB for CI (takes priority). |
| `FLUENTCODER_NO_AUTO_REBUILD` | — | Set to `1` to skip fingerprint-driven auto-rebuild on import. |
| `FLUENTCODER_CATEGORY_OVERRIDES` | — | Absolute path to `category_overrides.toml`. |

## Running without `pip install`

You can run the CLI directly without installing:

```
python -m fluentcoder.cli catalog info
python -m fluentcoder.cli compile examples/simple_transfer.py
```

This is the pattern the test scripts use.
