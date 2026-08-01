# Development

## Repo layout

```
fluentcoder\
├── pyproject.toml          ← Build config + console_scripts entry
├── README.md
├── docs\                   ← This documentation set (markdown)
├── examples\
│   └── simple_transfer.py  ← End-to-end example, exercised by parity test
├── fluentcoder\               ← The package
│   ├── __init__.py         ← Public API + first-import index build
│   ├── reagent.py          ← Reagent dataclass (33 LOC)
│   ├── worktable.py        ← Worktable + from_workspace + place/group/compile (261 LOC)
│   ├── gripper.py          ← Gripper (52 LOC)
│   ├── cli.py              ← `fluentcoder` CLI (161 LOC)
│   ├── labware\            ← 10 behavioral families (~511 LOC total)
│   │   ├── base.py         ← Labware + Layer + Well + offline-synthesis (290 LOC)
│   │   ├── plates.py       ← Plate, Plate96, Plate96Deep, Plate384 (37 LOC)
│   │   ├── troughs.py      ← Trough, Trough25mL, Trough100mL, Waste (47 LOC)
│   │   ├── tipboxes.py     ← TipBox, MCA*Box, FCA*Box (40 LOC)
│   │   ├── adapters.py     ← Adapter, EvaAdapter (21 LOC)
│   │   ├── magnet.py       ← MagnetRack (17 LOC)
│   │   ├── tuberack.py     ← TubeRack (20 LOC)
│   │   └── deckitems.py    ← WashStation, WasteChute, Hotel, FixedDeck (39 LOC)
│   ├── heads\              ← Pipetting heads
│   │   ├── mca96.py        ← MCA96Head + Tip
│   │   ├── mca384.py       ← MCA384Head
│   │   ├── liha.py         ← LiHa (tip_channels)
│   │   └── fca.py          ← FCAHead facade
│   ├── subroutines\        ← SubroutineRegistry for inline simulation
│   │   └── registry.py
│   ├── ir\                 ← Pydantic step models
│   │   └── schema.py       ← (611 LOC)
│   ├── compiler\           ← XML renderer
│   │   └── renderer.py     ← (1787 LOC)
│   ├── decompiler\         ← Phase B: .xscr → .py
│   │   ├── xscr_parser.py  ← XML → Pydantic Protocol IR (~330 LOC)
│   │   └── codegen.py      ← Protocol → Python source string (~280 LOC)
│   ├── catalog\            ← v1.1+ catalog system
│   │   ├── catalog.py      ← SQL queries (~210 LOC)
│   │   ├── indexer.py      ← Walk install + write rows
│   │   ├── inference.py    ← Category rules + overrides hook
│   │   ├── category_overrides.py  ← Optional TOML name → category map
│   │   ├── paths.py        ← Multi-install index DB resolution
│   │   ├── xcmp.py         ← .xcmp / .xwsp / .xsit parser
│   │   ├── xcon.py         ← .xcon connector parser (on-demand)
│   │   ├── xlqc.py         ← .xlqc liquid-class loader
│   │   ├── fc_install.py   ← Bridge to fluentcontrol_core
│   │   └── database.py     ← Legacy recipe database
│   ├── _assets\            ← Templates / reference / config
│   └── simulator\          ← The IR walker
│       ├── walk.py         ← Simulator class (346 LOC)
│       ├── snapshots.py    ← Snapshot dataclass (53 LOC)
│       └── invariants.py   ← Exception hierarchy (40 LOC)
└── tests\                  ← 40+ test modules (offline + install-backed)
    ├── fixtures\
    │   ├── simple_transfer_expected.xscr   ← Pinned golden XML for parity test
    │   ├── simple_transfer_fluentdsl_reference.py
    │   ├── synthetic_catalog/          ← Offline CI catalog + workspace tree
    │   ├── subroutines/                ← Sample subroutine .xscr files
    │   └── decompiled_corpus/
    ├── test_head_behavior.py           ← MCA partial pickup, LiHa channels
    ├── test_subroutines.py             ← Registry + variable mappings
    ├── test_category_overrides.py
    ├── test_multi_install_index.py
    ├── test_xcon_parser.py
    ├── test_tuberack.py                ← fill_tube, cap_closed
    ├── test_deterministic_workspace_delta.py
    ├── test_prompt_image_statement.py  ← RUPWorktableStatement round-trip
    └── ...
```

## Conventions

### Coding style

- Python ≥ 3.11. Module-level `from __future__ import annotations` everywhere.
- Pydantic v2 for IR schema; dataclasses for everything else.
- Public types are `frozen=True` when they represent values (Reagent,
  XcmpComponent, CatalogEntry).
- Module imports at top; lazy imports inside functions only when needed to
  break a circular dependency (e.g. `Labware.is_magnetized` lazily imports
  `MagnetRack` to avoid a circular import).

### Naming

- Internal helpers prefixed with `_`. `_warn_offline_once`, `_walk`,
  `_aspirate_one`.
- IR step types end in `Step` (`AddLabwareStep`, `AspirateStep`).
- Exception types end in `Error` (`MissingTipsError`).
- Behavioral classes are nouns (`Plate`, `Trough`, `MagnetRack`).
- Convenience subclasses fix shape: `Plate96`, `Plate384`, `MCA100Box`.

### Testing

- `pytest`. Run from the installed editable environment:
  `cd fluentcoder && python -m pytest tests/ -v`
- Tests that touch the real FluentControl install are guarded with
  `@pytest.mark.skipif(not _install_present(), ...)` so CI runs work
  unmodified. Catalog-dependent tests can opt into the offline
  `synthetic_catalog` fixture instead of skipping when the packaged index is
  empty.
- Tests should never rebuild the catalog index (the fixture is the
  install-driven build inside `ensure_index`); they query whatever is
  already there.

## Running tests

```
python -m pytest tests/ -v
```

Expected output (truncated):

```
tests\test_head_behavior.py ........                            [ 12%]
tests\test_subroutines.py .....                                 [ 18%]
tests\test_physical_invariants.py .......                         [ 25%]
...
============================== 200+ passed ==============================
```

Exact counts vary with optional install-backed and authoring dependencies.
The parity test (`test_simple_transfer_parity_xml`) runs offline against the
pinned `simple_transfer_expected.xscr` golden fixture using the
`synthetic_catalog` fixture. Catalog tests that need a real install skip when
unreachable; others use the `synthetic_catalog` fixture.

## Test inventory

| File | Tests | What it proves |
|---|---|---|
| `test_simple_transfer_parity.py` | 2 | fluentcoder's OO-authored simple_transfer renders XML matching the pinned golden fixture (GUID/checksum normalized); IR shape matches expectations. |
| `test_snapshot_introspection.py` | 2 | Layered well contents flow source → tip → dest; magnetized state toggles correctly with gripper stacking. |
| `test_physical_invariants.py` | 7 | Each invariant raises (occupied slot, missing adapter, missing tips, insufficient volume, overdraw, pinned aspirate on magnet). |
| `test_catalog_index_build.py` | 3 | Index builds against real install with expected category counts and known catalog entries. |
| `test_inference_known_samples.py` | 23 | Category inference correctly classifies a curated list of catalog names spanning every category. |
| `test_plate_construction_from_catalog.py` | 6 | Catalog-driven `Plate96` / `Trough100mL` / `MCA100Box` populate from real .xcmp data; offline behavior; error paths. |
| `test_worktable_from_workspace.py` | 4 | `from_workspace` registers valid slots; `InvalidSlotError` fires correctly; valid slots accepted. |

## Adding a new labware family

1. Subclass `Labware` in `fluentcoder/labware/<your_module>.py`.
2. Set `category = "..."`, `taxonomic_grid = (rows, cols)` if applicable,
   `offline_max_well_volume_ul = ...`.
3. Override `_post_populate(...)` if your family has special state to set
   up after the wells/dimensions are populated (see `Trough._post_populate`
   for an example that collapses parsed wells into a single pool).
4. Add the class to `fluentcoder/labware/__init__.py`'s exports + `CATEGORY_TO_CLASS`.
5. Re-export from `fluentcoder/__init__.py` if it should be in the top-level
   public API.
6. Update inference rules in `fluentcoder/catalog/inference.py` if your
   category requires new logic.

## Adding a new IR step type

The IR descends from the earlier project-owned fluentdsl implementation. If
you need a new step type, add it locally and update every consumer:

1. Add the step class to `fluentcoder/ir/schema.py` (Pydantic model with
   `step_type: Literal[StepType.X]`).
2. Add the StepType enum value.
3. Add it to the `Step` discriminated union.
4. Add to `STEP_TO_COMMAND_ID` if the renderer needs a command-ID mapping.
5. Wire a handler in `fluentcoder/simulator/walk.py:_dispatch`.
6. Add an authoring method on the appropriate object (Worktable, head,
   gripper).

The renderer is the part most likely to need updates — extending it means
editing `fluentcoder/compiler/renderer.py` and possibly
`fluentcoder/_assets/reference/commands.yaml`.

## Known limits / remaining open items

These are visible from the current codebase; documenting so contributors
know what remains open versus what has already shipped.

### Simulator

- **`snapshot_mode="full"` memory cost** — default per-step snapshots still
  deep-copy the full twin state. Long protocols with many large `TubeRack`s
  can produce snapshot lists >100 MB. Prefer `snapshot_mode="delta"` for
  per-step history with lower memory use, or `record_snapshots=False` /
  `snapshot_mode="final_only"` for one final snapshot only.

### Tests / CI

- Regenerate `tests/fixtures/simple_transfer_expected.xscr` when the
  `examples/simple_transfer.py` protocol or renderer output changes. Compile
  with the synthetic catalog, `deterministic=True`, then apply the same
  `_normalize_xml` rules used in `test_simple_transfer_parity.py`.

## Shipped improvements (formerly v1.2 candidates)

### Hardware coverage

- **MCA96, MCA384, LiHa, and FCA heads.** `MCA96Head` (`wt.mca96`),
  `MCA384Head` (`wt.mca384`), `LiHa` (`wt.liha`), and `FCAHead` (`wt.fca`).
- **Partial MCA tip pickup.** `MCA96Head.pick_up(..., tip_columns=...)`,
  `tip_count=...`, or `partial_columns` / `partial_rows`. The simulator
  tracks per-address tip occupancy on `TipBox` (not just `is_full`).
- **LiHa/FCA channel selection.** `get_tips(..., tip_channels=[1, 3, 5])`
  or single-channel `tip_index=...`.

### Simulator

- **Subroutine descent.** `SubroutineRegistry` +
  `wt.simulate(subroutine_registry=...)` inlines registered `.xscr` bodies
  with cycle detection (max depth 8) and `variable_mappings_start` /
  `variable_mappings_end` scope push/pop.
- **Author-side state isolation.** `simulate()` mutates twin state only;
  author-side `wt.slot_map`, well layers, and tip-box counts are unchanged.
- **`record_snapshots=False`.** Skips per-step deepcopy; keeps one final
  snapshot. Alias: `snapshot_mode="final_only"`.
- **Delta snapshot mode.** `record_snapshots="delta"` or
  `snapshot_mode="delta"` records per-step diffs of changed labware volumes
  and tip state instead of deep-copying the full twin after every step.
- **`CannotAspirateError`.** Raised for pinned layers on a magnetized plate
  and for closed tube caps (`TubeRack.set_cap(closed=True)`).

### Catalog

- **Auto-rebuild on install drift.** `ensure_index()` consults
  `fingerprint_matches()` on every import and rebuilds when the on-disk
  install differs. Opt out via `FLUENTCODER_NO_AUTO_REBUILD=1`.
- **Liquid-class catalog.** Walks `SystemSpecific/LiquidClasses/*.xlqc`.
- **Multiple FluentControl installs.** `FLUENTCODER_FC_INSTALL` per install;
  non-default installs auto-use `catalog/indexes/install_<hash>.db`. Override
  with `FLUENTCODER_INDEX_DB`.
- **Category overrides.** Optional `category_overrides.toml` maps catalog
  `ObjectName` → category. Search order: `FLUENTCODER_CATEGORY_OVERRIDES`,
  package `catalog/`, `<install>/`, `_assets/config/`.
- **`.xcon` connector parsing.** `load_xcon` / `parse_connector` for
  on-demand parsing; `build_index` indexes connectors referenced by indexed
  `.xsit` files into a `connectors` SQL table.
- **Opt-in full connector walk.** Default build indexes site-referenced
  connectors only. Pass `include_all_connectors=True` to `build_index`, set
  `FLUENTCODER_INDEX_ALL_CONNECTORS=1`, or call `index_connector_paths([...])`
  for targeted `.xcon` paths.
- **Synthetic catalog fixture.** `tests/fixtures/synthetic_catalog/` for
  offline CI. Build with `python tests/fixtures/synthetic_catalog/bootstrap.py`;
  tests use the `synthetic_catalog` pytest fixture or
  `FLUENTCODER_TEST_CATALOG_DB`.

### Compile / decompile path

- **Workspace binding.** `Worktable.from_workspace(...)` sets
  `workspace_guid` / `workspace_name`; `compile()` requires a bound workspace.
- **Deterministic `WorkspaceDelta` GUID.** `wt.compile(path, deterministic=True)`.
- **RUP image prompt decompiler.** Worktable image prompts decode to
  `UserPromptStep(image_path=..., rup_kind="worktable", ...)` and recompile to
  `RUPWorktableStatement`; legacy standard prompts decode/recompile via
  `rup_kind="standard"` to `RUPStandardStatement`.

### Authoring

- **Labware lookup ergonomics.** `wt.labware(label)`, `wt["label"]`, and
  `has_labware(label)` alongside `labware_by_label(label)`.
- **FluentControl variable tokens.** `wt.declare_fc_variable(name)` returns
  an `FCVariableToken` usable as `catalog=` / `labware_type` in IR steps and
  `add_labware(...)`.

### Labware

- **Default catalog per class.** `fluentcoder.defaults.set_catalog_defaults`
  or `Worktable.set_default_catalog` let authors omit `catalog=` on common
  classes when a project default is registered; explicit `catalog=` always
  wins and omitting both still raises.
- **`TubeRack.fill_tube()`.** Per-tube partial fill without touching
  neighbours; independent `Well.layers` per tube position.

## Quick recipes

### "How do I find the catalog name for a 96-deep-well plate?"

```
fluentcoder catalog find "deep" --category plate
```

### "How do I check what fluentcoder loaded from a specific .xcmp?"

```python
from fluentcoder.catalog import resolve_by_name, load_xcmp
entry = resolve_by_name("96 Well Flat")
comp = load_xcmp(entry.file_path)
print(comp.dim_mm, comp.functional_group, comp.pipettable.cavity.volume_ul)
```

### "How do I rebuild the catalog after a FluentControl update?"

```
fluentcoder catalog refresh
```

### "How do I test offline (no FluentControl install)?"

Set `FLUENTCODER_FC_INSTALL` to a directory that doesn't exist (or just rename
your install). On next import, `ensure_index` will be a no-op, and labware
classes will use the offline-synthesis path. A `CatalogIndexMissing`
warning fires once per process the first time a labware is constructed.

### "How do I override a misclassified catalog entry?"

Copy `fluentcoder/catalog/category_overrides.toml.example` to
`category_overrides.toml` next to the package catalog module, or set
`FLUENTCODER_CATEGORY_OVERRIDES` to an absolute path:

```toml
"My Misclassified Runner" = "tube_rack"
```

Overrides apply at index time and on `resolve_by_name` without rebuild.

### "How do I simulate with subroutine inlining?"

```python
from fluentcoder.subroutines import SubroutineRegistry

registry = SubroutineRegistry()
registry.register_directory("path/to/subroutines")
wt.simulate(subroutine_registry=registry)
```

CLI equivalent:

```
fluentcoder simulate protocol.py --subroutine-dir path/to/subroutines
```

### "How do I see what state was true at a given step?"

```python
wt.simulate()
for snap in wt.snapshots:
    if type(snap.step).__name__ == "DispenseStep":
        plate = snap.labware("DestPlate")
        print(f"step {snap.step_index}: A1 = {plate.well('A1').layers}")
```
