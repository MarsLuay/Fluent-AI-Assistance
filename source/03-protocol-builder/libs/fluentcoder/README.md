# fluentcoder

fluentcoder is a Python object model for authoring, simulating, compiling, and
inspecting Tecan FluentControl protocols.

The public API is built around `Worktable`, labware, reagents, and pipetting
heads. Method calls emit protocol IR steps; the simulator walks that IR to
reconstruct liquid and deck state; the compiler renders `.xscr` XML that
FluentControl can load.

```text
Python authoring code
  -> Worktable / Labware / Reagent / Head methods
  -> Protocol IR
  -> Simulator snapshots and physical invariant checks
  -> .xscr XML for FluentControl
```

## Review Status

Fluent AI-Assistance (including fluentcoder) is released under the
[PolyForm Noncommercial License 1.0.0](../../../../LICENSE). Noncommercial use
is free; companies contact [marwanluay2005@gmail.com](mailto:marwanluay2005@gmail.com).
It is not a production or instrument-qualified release.

- See [NOTICE.md](NOTICE.md) for safety and FluentControl dependency boundaries.
- Some features require a locally licensed FluentControl installation.
- Generated `.xscr` files must be reviewed and validated before instrument use.
- Customize `_assets/config/generation.yaml` (see `generation.yaml.example`) for
  your workspace, device paths, and liquid-class GUID before compile/deploy.
- Release and robot-use gates are summarized in
  [docs/RELEASE_READINESS.md](docs/RELEASE_READINESS.md).

## What Works Today

- Python authoring API for worktables, labware, reagents, grouping, loops,
  and conditionals.
- Pipetting heads: `MCA96Head` (`wt.mca96`), `MCA384Head` (`wt.mca384`),
  `LiHa` (`wt.liha`), and `FCAHead` (`wt.fca`). MCA partial-column pickup
  (`tip_columns`, `tip_count`, `partial_columns`/`partial_rows`); LiHa/FCA
  per-channel pickup via `tip_channels`.
- `wt.declare_fc_variable(name)` for FluentControl variable tokens usable as
  `catalog=` / `labware_type` in IR steps and `add_labware(...)`.
- `TubeRack.fill_tube()` for per-tube partial fills; `set_cap()` blocks
  aspirate/dispense with `CannotAspirateError` when the cap is closed.
- `wt.call_subroutine()` with `variable_mappings_start` / `variable_mappings_end`;
  `SubroutineRegistry` for inline simulation descent from local `.xscr` files.
- Simulator with per-step snapshots, `snapshot_mode="delta"` for lightweight
  per-step diffs, or `record_snapshots=False` / `snapshot_mode="final_only"`
  for a single final snapshot; author-side state isolation, and physical checks:
  occupied slots, missing tips/adapters, overdraws, pinned-layer aspirates,
  closed tube caps, and insufficient volume.
- `.xscr` compiler with required workspace binding and optional
  `compile(..., deterministic=True)` for stable `WorkspaceDelta` GUIDs.
  Image prompts via `wt.user_prompt(..., image_path=..., rup_kind="standard")`
  compile to `RUPStandardStatement` (`SelectedImagePath`); without
  `rup_kind="standard"`, `image_path` compiles to `RUPWorktableStatement`
  (`CustomDetailImageFilePath`).
- `.xscr` decompiler including `RUPWorktableStatement` and
  `RUPStandardStatement` image prompts and subroutine variable mappings.
- Catalog indexing against a local FluentControl install: components,
  workspaces, sites, liquid classes, and site-referenced connectors
  (`.xcon`). Multi-install support via keyed index DBs; optional
  `category_overrides.toml`; offline `synthetic_catalog` test fixture.
- CLI entry points for compile, simulate (with `--subroutine-dir` /
  `--subroutine-xscr`), decompile, catalog, and prompt authoring flows.

## What Needs Review

The most useful feedback is on the domain model and workflow fit:

- Does the API match how FluentControl users think about protocols?
- Are worktables, labware, reagents, tips, and heads modeled at the right
  level?
- Which FluentControl commands or workflow patterns are missing?
- Are simulator checks catching useful mistakes?
- Would decompile-to-Python help with reviewing or modernizing existing
  methods?

## Quickstart

```bash
python -m pip install -e .
python -m pytest tests/ -q
```

Minimal authoring example:

```python
from fluentcoder import Worktable, Reagent, Plate96, MCA100Box

input_dna = Reagent("Input gDNA")

wt = Worktable(name="Simple transfer", comment="Move liquid from one plate to another")

wt.group("Setup")
src = wt.place(Plate96("SourcePlate", catalog="96 Well Flat"), "Nest", 1)
dst = wt.place(Plate96("DestPlate", catalog="96 Well Flat"), "Nest", 2)
tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 4)

src.fill_all(input_dna, 50.0)

wt.group("Transfer")
head = wt.mca96
head.mount_adapter()
head.pick_up(tips)
head.aspirate(src, 20.0, liquid_class="Water Free Single")
head.dispense(dst, 20.0, liquid_class="Water Free Single")
head.return_tips(tips)
head.drop_adapter()

wt.simulate()
print(wt.snapshots[-1].labware("DestPlate").well("A1").layers)

wt.compile("simple_transfer.xscr")
# wt.compile("simple_transfer.xscr", deterministic=True)  # stable WorkspaceDelta GUID
```

MCA partial-column pickup and LiHa channel selection:

```python
wt.mca96.pick_up(tips, tip_columns=[1, 2])          # two full columns
wt.mca96.pick_up(tips, partial_columns=2, partial_rows=8)

wt.liha.get_tips(tip_box, tip_channels=[1, 3, 5])   # channels 1, 3, 5 only
```

Subroutine calls with variable mappings (requires registered `.xscr` files):

```python
from fluentcoder import Worktable
from fluentcoder.ir.schema import VariableMapping
from fluentcoder.subroutines import SubroutineRegistry

registry = SubroutineRegistry()
registry.register_directory("path/to/subroutines")
wt.call_subroutine(
    r"TEST\SUB_FingerSelection_v1",
    variable_mappings_start=[VariableMapping(target="FingerSelection", source="MyFinger")],
)
wt.simulate(subroutine_registry=registry, record_snapshots=False)  # final snapshot only
```

Tube rack partial fill:

```python
from fluentcoder import TubeRack, Reagent

rack = wt.place(TubeRack("Tubes", catalog="Eppendorf 24"), "Nest", 3)
rack.fill_tube("A1", Reagent("Sample"), 80.0)
rack.set_cap("B1", closed=True)  # aspirate on B1 raises CannotAspirateError
```

A working version is in `examples/simple_transfer.py`.

## FluentControl Dependency

fluentcoder can run some authoring and simulator paths without FluentControl, but
install-backed catalog and workspace features need a local FluentControl
database. By default the catalog indexer looks for:

```text
C:\ProgramData\Tecan\VisionX\Database
```

Override this with `FLUENTCODER_FC_INSTALL` or the relevant CLI flag. Non-default
installs use a keyed index at `catalog/indexes/install_<hash>.db`; override the
DB path with `FLUENTCODER_INDEX_DB`. CI tests use `FLUENTCODER_TEST_CATALOG_DB`
pointing at `tests/fixtures/synthetic_catalog/install_index.db`. If no install
is reachable, catalog-backed tests should skip or fall back rather than making
the package impossible to import.

## CLI

```bash
fluentcoder compile examples/simple_transfer.py
fluentcoder simulate examples/simple_transfer.py
fluentcoder decompile path/to/protocol.xscr -o protocol.py
fluentcoder catalog info
fluentcoder catalog find magnet
fluentcoder catalog refresh
```

## Documentation

- [Release readiness checklist](docs/RELEASE_READINESS.md)
- [Reviewer guide](docs/reviewer-guide.md)
- [Architecture](docs/architecture.md)
- [Authoring API](docs/authoring.md)
- [Catalog system](docs/catalog.md)
- [Simulator](docs/simulator.md)
- [Compile path](docs/compile-path.md)
- [Deployment](docs/deployment.md)
- [CLI](docs/cli.md)
- [Decompiler](docs/decompile.md)
- [Manual test walkthrough](MANUAL_TEST.md)
- [Development](docs/development.md)
- [Glossary](docs/glossary.md)

## Repository Layout

```text
docs/       Documentation for reviewers and developers
examples/   Example authored protocols
fluentcoder/   Python package
tests/      Unit, integration, and regression tests
scripts/    Local development and validation helpers
```

## License

No license has been granted yet. The code is source-visible for review only.
See [NOTICE.md](NOTICE.md).
