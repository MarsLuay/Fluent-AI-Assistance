# Glossary

Terms used across fluentcoder and the FluentControl ecosystem. Where a term
has a fluentcoder-specific meaning, that's noted explicitly.

## FluentControl / Tecan terms

**Adapter**
   A head accessory mounted on a pipetting head (e.g. EVA — Extended Volume
   Adapter for the MCA-96). Lives "on the head," not on a worktable slot.
   Catalog category: `adapter`.

**Carrier**
   A worktable component that holds labware (e.g. "3 Grid", a tube runner).
   Doesn't directly carry liquid. Distinguished from labware by the
   `Carrier.*` FunctionalGroup prefix in `.xcmp`.

**CGA**
   Centric Gripper Arm. The robotic arm that grips and moves labware on
   the worktable. CGA 1, CGA 2 etc. denote specific arms; per-site grip
   modes record which CGAs are allowed at which sites.

**DiTi**
   Disposable Tip. The MCA96 / MCA384 / FCA pipetting heads pick up DiTis
   from tip boxes. Catalog category: `tip_box`.

**EVA**
   Extended Volume Adapter — a flat head accessory that increases the
   tip-volume range of the MCA-96. Catalog name `EVA[001]` in fluentcoder's
   default; not an item placed on the worktable in the FluentControl
   protocol model.

**FCA**
   Flexible Channel Arm. The 8-channel pipetting head; supports per-channel
   addressing and column-wise transfers. Tip boxes for FCA: `FCA, 50ul SBS`,
   `FCA, 200ul Filtered`, etc.

**Footprint**
   The physical category of a labware (e.g. "Microplate", "Tube",
   "Reservoir"). Embedded in the `.xcmp`'s `<FootPrint>` element.

**FunctionalGroup**
   A structured taxonomy field embedded in each `.xcmp` (`<FunctionalGroup>`).
   Examples: `Labware.Microplate`, `Labware.Trough`, `Carrier.Hotel`,
   `Carrier.Grid Segment`. fluentcoder's category-inference uses this as the
   primary signal.

**Gripper**
   See CGA. fluentcoder's `Gripper` class wraps the IR steps that drive CGA
   moves.

**Hotel**
   Multi-Z plate storage carrier (vertical stack of plate sites). Catalog
   category: `hotel`. Plates are gripper-moved into hotel slots between
   protocol steps.

**LiHa**
   Liquid Handling Arm. The single-channel pipetting head; used for
   per-well precision work. Fluentcoder v1.1 doesn't author LiHa steps but
   the IR schema covers them.

**MCA96 / MCA384**
   Multi-Channel Arms with 96 / 384 parallel pipetting channels. fluentcoder
   v1.1 implements `MCA96Head`; MCA384 is in the IR but no head class yet.

**MagniFlex**
   A specific magnet-rack family from Tecan (e.g. `24 Eppendorf Adapter
   Magniflex`). Catalog category: `magnet_rack`. fluentcoder's name-substring
   override catches `magniflex` regardless of FunctionalGroup.

**Nest**
   A single-position holder on the worktable (e.g. "7mm Nest"). One labware
   sits in a nest. Catalog category: typically `fixed_deck` (catch-all).

**Pipettable**
   `<Pipettable>` element in a `.xcmp`. Carries the well grid:
   `XNumberOfWells`, `YNumberOfWells`, spacings, `PositionOfFirstWell`,
   well cavity geometry. Only present for labware that holds liquid.

**RGA**
   Robotic Gripper Arm. Mechanical arm separate from the pipetting heads;
   moves labware. fluentcoder's `Gripper.move()` emits `RgaTransferLabwareStep`.

**Runner**
   A linear carrier with multiple sites for tubes or troughs (e.g. "1x16
   15ml Falcon Tube Runner"). fluentcoder's inference distinguishes
   tube-bearing runners (`tube_rack`) from trough-bearing runners
   (`fixed_deck`) via the name substring.

**SBS**
   Society for Biomolecular Screening (now SLAS). The standard plate
   footprint (127.76 × 85.48 mm) used by 96- and 384-well microplates.

**Site**
   A position on a carrier where a labware can be placed. Identified by a
   GUID + a 0-based index within an arrangement. FluentControl uses
   1-based positions in the .xscr; fluentcoder translates at the boundary
   (XWSP read = +1).

**Worktable**
   The deck of the FluentControl instrument: a 2D arrangement of
   `(location, position)` slots where carriers and labware are placed.
   fluentcoder's `Worktable` class models this.

**Workspace**
   A configured worktable layout — a `.xwsp` file enumerates which sites
   are present, their location names, and which labware is initially
   placed where. fluentcoder's `Worktable.from_workspace(name)` loads one.

**.xcmp**
   FluentControl Worktable Component file. XML; contains a single
   labware-or-carrier definition with geometry, arrangement, well grid,
   custom attributes, mesh references.

**.xwsp**
   FluentControl Workspace file. XML; enumerates a worktable's sites and
   their occupancy.

**.xsit**
   FluentControl Site file. XML; per-site definition (connectors, allowed
   labware). Lightweight; fluentcoder indexes name → file_path only.

**.xcon**
   FluentControl Connector file. Defines how labware connects to sites.
   ~14,000 in a typical install; fluentcoder doesn't parse them.

**.xmsh**
   FluentControl Mesh file. 3D model. fluentcoder doesn't parse them.

**.xscr**
   FluentControl Script file (the rendered protocol). XML. The output of
   `Worktable.compile()`.

## fluentcoder terms

**Catalog index**
   The SQL artifact at `fluentcoder/catalog/install_index.db`. One row per
   catalog `<ObjectName>` linking name + category + file path + scalar
   attributes. Built once on first import; refreshable via CLI.

**Category**
   One of 10 strings fluentcoder uses to dispatch a Python class. See
   `fluentcoder/catalog/inference.py:CATEGORIES`: `plate`, `trough`,
   `tip_box`, `magnet_rack`, `tube_rack`, `wash_station`, `waste_chute`,
   `hotel`, `adapter`, `fixed_deck`.

**Catalog entry**
   A row from the `components` table; mirrored as
   `fluentcoder.catalog.CatalogEntry`. Carries name, category, file_path,
   plus cached scalar attributes (grid, dim_mm, site_count).

**Catalog name**
   The exact `<ObjectName>` value from a catalog entry's `.xcmp`. The
   string passed as `catalog=` to a labware constructor. Examples:
   `"96 Well Flat"`, `"MCA96, 100ul, Box"`, `"24 Magnet Plate"`.

**Class taxonomy**
   The 10-class behavioral hierarchy: `Plate`, `Trough`, `TipBox`,
   `MagnetRack`, `TubeRack`, `WashStation`, `WasteChute`, `Hotel`,
   `Adapter`, `FixedDeck`. Subclasses (`Plate96`, `MCA100Box`, etc.) fix
   the *taxonomic shape* but inherit behavior.

**FunctionalGroup primary, name fallback**
   The category-inference rule: use the .xcmp's `<FunctionalGroup>` first,
   override with name-substring rules for magnet/adapter, fall back to
   substring matches when FunctionalGroup is absent or ambiguous.

**Inferred category**
   The category column in the SQL index, set by `infer_category(comp)`
   during the indexer's walk. Stable per-install (re-runs of `build_index`
   produce the same categorization).

**IR**
   Intermediate Representation. A list of `Step` Pydantic objects that
   the renderer consumes. fluentcoder's authoring API emits IR; the simulator
   reads IR; the renderer turns IR into XML.

**Layered well contents**
   `Well.layers: list[Layer]` — bottom→top stack of `(reagent, volume_ul)`
   slices. The simulator maintains layered state through aspirate
   (top-down draw) and dispense (push or merge based on identity).

**Offline fallback / synthesised default**
   When the catalog index is empty (no FC install), labware classes use
   their `taxonomic_grid` and `offline_max_well_volume_ul` to build a
   generic well grid. mm geometry is `None`; `CatalogIndexMissing` warns
   once per process.

**Parity (v1)**
   Byte-equal `.xscr` output between fluentcoder and fluentdsl for the same
   input protocol — modulo one random WorkspaceDelta GUID. Verified by
   `tests/test_simple_transfer_parity.py`.

**Pinned reagent**
   A reagent with `pinned_when_magnetized=True`. Beads (paramagnetic) are
   the canonical example. The simulator skips pinned layers when
   aspirating from a magnetized labware.

**Protocol IR**
   The top-level `Protocol` Pydantic object holding name, comment,
   variables, and groups of steps. Built by `Worktable.to_protocol()`.

**Sim-time value**
   A concrete value the author provides for a runtime variable so the
   simulator can walk a deterministic path through loops, conditionals,
   and imports. Set via `wt.set_sim_value(name, value)`.

**Slot**
   A `(location_name, position_index)` tuple identifying a worktable spot.
   `place(labware, "Nest", 1)` puts labware at slot `("Nest", 1)`.

**Stack**
   The list of labware at a single slot, bottom→top. A `MagnetRack` placed
   at slot `("Nest", 7)`, then a Plate96 gripper-moved `onto` that rack,
   produces the stack `[MagnetRack, Plate96]`. The Plate96's
   `is_magnetized` reads the stack and sees the rack below.

**Snapshot**
   A frozen view of twin world state after one IR step. Deep-copied at
   creation. Indexed in `wt.snapshots[i]`.

**Taxonomic grid**
   A class-level constant declaring what shape *the class* means
   (`Plate96.taxonomic_grid = (8, 12)`). Used in offline mode to
   synthesise wells when no catalog data is available. Per-catalog
   facts override it when the catalog is loaded.

**Twin**
   The simulator-side world state — Worktable, Labware, Wells, Tips —
   built freshly by walking the IR. Always reconstructable; the simulator
   doesn't share state with author-side mutations.

**Valid slots**
   The set of `(location, position)` tuples a `from_workspace`-built
   worktable allows. `place()` raises `InvalidSlotError` for any slot
   outside this set.

**Project-owned fluentdsl lineage**
   Code that descends from the earlier project-owned fluentdsl
   implementation and is now maintained inside fluentcoder rather than imported
   as a dependency. This applies to the renderer, IR schema, legacy recipe
   database helpers, FluentControl install bridge, and `_assets/` reference
   material. Tecan/FluentControl-facing assets still need separate provenance
   review before public release.
