# Catalog system

The catalog system makes the FluentControl install the single source of
truth for labware identity and geometry. It has three pieces:

1. **The .xcmp parser** — typed dataclasses + ElementTree parser for
   `.xcmp` (component), `.xwsp` (workspace), and `.xsit` (site) files.
2. **The category-inference rules** — `FunctionalGroup` primary, name
   substring overrides, name substring fallback, default `fixed_deck`.
3. **The SQL index** — one row per catalog `<ObjectName>` linking name +
   inferred category + file path + scalar attributes.

## Files at a glance

```
fluentcoder/catalog/
├── xcmp.py            Typed parser for .xcmp / .xwsp / .xsit; lru-cached on path.
├── xcon.py            On-demand .xcon connector parser.
├── inference.py       infer_category(comp) — FunctionalGroup + substring rules.
├── category_overrides.py  Optional TOML name → category overrides.
├── paths.py           Multi-install index DB path resolution.
├── catalog.py         SQL queries: resolve_by_name, find_components, …
├── indexer.py         build_index() walks install, infers, writes rows.
├── install_index.db   Default built artifact (SQLite, inside the package).
├── indexes/           Per-install keyed DBs (install_<hash>.db).
├── fc_install.py      Bridge to the upstream fluentcontrol_core (legacy).
└── database.py        Legacy recipe database and lookup helpers.
```

## The .xcmp parser

`fluentcoder/catalog/xcmp.py`

A FluentControl component is a verbose namespaced XML document. The parser
matches on local-name only (ignoring namespace prefixes) so it stays robust
across FC versions.

### Key dataclasses

```python
@dataclass(frozen=True)
class XcmpComponent:
    guid: str
    name: str                                # top-level <ObjectName>
    file_path: Path
    dim_mm: (float, float, float) | None     # carrier or labware footprint
    functional_group: str | None             # 'Labware.Microplate' / 'Carrier.Hotel' / …
    footprint: str | None                    # 'Microplate' / 'Tube' / …
    renderer: str | None                     # mesh family
    is_lid: bool
    arrangement: XcmpArrangement | None      # site grid
    pipettable: XcmpPipettable | None        # well grid + cavity
    sub_component_names: tuple[str, ...]
    mesh_object_names: tuple[str, ...]
    site_guids: tuple[str, ...]
    custom_attrs: dict[str, str]             # Force, VendorName, PartNumber, …

@dataclass(frozen=True)
class XcmpArrangement:
    sites_in_x: int
    sites_in_y: int
    sites_in_z: int
    site_spacing_mm: (x, y, z)
    position_in_parent_mm: (x, y, z)
    site_offsets_mm: dict[int, (x, y, z)]    # per-site offset
    allowed_grip_modes: dict[int, tuple[str, ...]]   # site_index -> CGA names

@dataclass(frozen=True)
class XcmpPipettable:
    x_wells: int                             # cols
    y_wells: int                             # rows
    x_spacing_mm: float
    y_spacing_mm: float                      # may be negative (top-down rows)
    first_well_mm: (x, y, z)                 # A1 position
    cavity: XcmpCavity | None                # well shape + computed volume
    z_heights: dict[str, float]              # ZTravel, ZStart, ZDispense, …

@dataclass(frozen=True)
class XcmpCavity:
    shape: str                               # 'TruncatedCone' / 'Cylinder' / …
    height_mm: float | None
    diameter_top_mm: float | None
    diameter_bottom_mm: float | None
    @property
    def volume_ul(self) -> float | None     # geometric volume of the cavity
```

### Where the source-of-truth fields live in the XML

For a 96-well plate's .xcmp:

```
Payload/PayloadData/CarrierOrLabwareTemplate/
├── Dimension/X|Y|Z              ← labware footprint mm
├── FunctionalGroup              ← e.g. 'Labware.Microplate' (category source)
├── FootPrint                    ← e.g. 'Microplate'
├── Renderer                     ← mesh family name
├── IsLid                        ← lid flag
├── Arrangements/ArrangementTemplate/
│   ├── SitesInX|Y|Z             ← site grid (1×1 for plate-as-labware)
│   ├── SiteSpacingInX|Y|Z       ← mm
│   ├── PositionInParent/X|Y|Z   ← origin offset
│   ├── SiteOffsets/             ← key→vector map
│   └── AllowedGripModes/        ← per-site CGAs
├── Pipettable/                  ← only present for pipettable labware
│   ├── XNumberOfWells           ← e.g. 12
│   ├── YNumberOfWells           ← e.g. 8
│   ├── XSpacing | YSpacing      ← mm
│   ├── PositionOfFirstWell      ← A1 mm
│   ├── ZHeights/                ← ZTravel, ZStart, ZDispense, …
│   └── Compartments/Cavity/CavityDefinition/ShapeList/CavityShape/
│       ├── Height
│       ├── DiameterBottom
│       └── DiameterTop
└── CustomAttributes/            ← Force, Locking, VendorName, PartNumber, …
```

### Computed well volume

Cavity volume comes from the cavity geometry. For a truncated cone:

```
V = (π · h / 3) · (r_bottom² + r_bottom · r_top + r_top²)
```

`XcmpCavity.volume_ul` (since 1µL = 1mm³) computes this for `TruncatedCone`
and `Cylinder` shapes; other shapes return `None`. For 96 Well Flat:

```
height = 10.9 mm
diameter_bottom = 6.58 mm     → r1 = 3.29
diameter_top = 6.96 mm        → r2 = 3.48
V ≈ 392.47 µL
```

This becomes each well's `max_volume_ul` when the catalog row is parsed.

### .xwsp loader

```python
@dataclass(frozen=True)
class XwspWorkspace:
    guid: str                                   # .xwsp file-stem GUID
    name: str
    file_path: Path
    base_worktable_guid: str | None
    base_worktable_name: str | None
    occupants: tuple[WorkspaceOccupant, ...]            # sites with labware
    available_sites: tuple[(site_path, base_loc), ...]  # ALL visited sites
    location_names: tuple[str, ...]
    referenced_labware_names: tuple[str, ...]

@dataclass(frozen=True)
class WorkspaceOccupant:
    site_path: tuple[int, ...]
    site_index: int
    catalog_name: str
    base_location_identifier: str | None    # logical location (e.g. 'Nest')
    base_location_connector_identifier: str | None
```

`guid` is the workspace document identity used for catalog lookup. fluentcoder
takes it from the installed `.xwsp` filename, because that is what production
`.xscr` workspace references resolve against. Internal component references in
the XML stay auxiliary metadata (`base_worktable_guid` /
`base_worktable_name`) and do not replace the workspace document GUID.

`available_sites` includes every site frame the walker visited, occupied or
not — `Worktable.from_workspace` uses this to build the valid-slots
whitelist. `occupants` is the subset with a labware label attached.

### Caching

Both `load_xcmp` and `load_xwsp` are wrapped in `lru_cache`. Re-parsing the
same file is a dict lookup; the parser walks the XML at most once per file
per process.

## Category inference

`fluentcoder/catalog/inference.py:25`

`infer_category(comp: XcmpComponent) -> str` returns one of:

```python
CATEGORIES = (
    "plate", "trough", "tip_box", "magnet_rack", "tube_rack",
    "wash_station", "waste_chute", "hotel", "adapter", "fixed_deck",
)
```

### Rule order (first match wins)

1. **Magnet override** — name matches `\bmagnet\b` or `magniflex`. Always
   wins, regardless of FunctionalGroup. This catches "Landscape Nest Magnet
   Teleshake Segment" (which has FG `Carrier.Deck Segment`) as `magnet_rack`.

2. **Adapter override** — name matches `\badapter\b` or `\beva\b`, **and**
   not `adapter\s*nest` / `adapter\s*segment` (those are deck nests), **and**
   `dim.z < 30 mm` (real adapters are flat). Wins over FunctionalGroup.

3. **FunctionalGroup map** —

   | FunctionalGroup | Category |
   |---|---|
   | `Labware.Microplate` | `plate` |
   | `Labware.Deep Well` | `plate` |
   | `Labware.MCA96 DiTi` / `Labware.MCA96 Adapter DiTi` / `Labware.MCA384 DiTi` / `Labware.MCA384 Adapter DiTi` / `Labware.FCA DiTi` | `tip_box` |
   | `Labware.Trough` | `trough` |
   | `Labware.Wash and Waste` | `wash_station` or `waste_chute` (decided by name substring) |
   | `Labware.Tube` | `tube_rack` (single tube treated as 1×1 rack) |
   | `Labware.Miscellaneous` | `fixed_deck` (RoboColumns etc.) |
   | `Carrier.Hotel` | `hotel` |
   | `Carrier.Nest` | `fixed_deck` |
   | `Carrier.Deck Segment` / `Carrier.Grid Segment` / `Carrier.Base Unit` / `Carrier.Device` / `Carrier.Miscellaneous` | `fixed_deck` |
   | `Carrier.Runner` | *omitted from map* — name disambiguates (see below) |

4. **Substring fallback** (only fires when FG didn't match):

   | Pattern | Category |
   |---|---|
   | `waste\s*chute` / `waste\s*trough` | `waste_chute` |
   | `wash\s*station` / `wash.*cleaner` / `\bwash\b` | `wash_station` |
   | `\bhotel\b` / `\bstack\d` / `passive\s*stack` | `hotel` |
   | `\bbox\b` / `\bditi\b` / `mca96.*ul` / `fca,?\s*\d+ul` / `filtered` / `nested` (and matches a 96 or 384 site grid) | `tip_box` |
   | `eppendorf` / `falcon` / `cryo` / `\btube\b` / `vacutainer` | `tube_rack` |
   | `\brunner\b` / `\bdownholder\b` / `\bstand\b` / `\bholder\b` (after tube_rack check) | `fixed_deck` |
   | `\d+\s*well` / `\bpcr\b` / `microplate` (and has `Pipettable`) | `plate` |
   | `\btrough\b` / `\breservoir\b` | `trough` |

5. **Default** — `fixed_deck`.

### Why this order

The substring "Tube Runner" exists in the install — physically a runner
holding tubes. Logically fluentcoder wants to address its tube positions. So
`tube_rack` substring runs **before** the `\brunner\b` exclusion; once
"Tube" is detected, it routes correctly. A "Trough Runner" is structural
(holds trough labware on top), so the runner exclusion catches it after
the tube_rack check. This matters because `Carrier.Runner` is **deliberately
omitted** from the FG map.

### Distribution on a real install

On a 629-component install the rules produce:

```
fixed_deck      354     (deck segments, base units, devices, structural)
tip_box          95     (MCA96 / MCA384 / FCA tip box variants)
tube_rack        63     (tube runners, falcon, eppendorf, cryo)
plate            50     (96-well, 384-well, deep-well variants)
trough           25     (true troughs and reservoirs)
hotel            11     (multi-Z plate storage)
waste_chute      11     (waste destinations)
wash_station      8     (tip wash stations)
adapter           6     (head accessories like EVA)
magnet_rack       6     (24-magnet plates, MagniFlex, magnet teleshake)
```

23 known catalog entries are pinned in
`tests/test_inference_known_samples.py`; the rules pass all 23.

## Category overrides

`fluentcoder/catalog/category_overrides.py`

Optional `category_overrides.toml` maps FluentControl `ObjectName` → category
string. Overrides apply after built-in inference rules and take effect at
index time and on `resolve_by_name` without rebuild.

Search order:

1. `FLUENTCODER_CATEGORY_OVERRIDES` (explicit file path)
2. `fluentcoder/catalog/category_overrides.toml` (next to the module)
3. `<install>/category_overrides.toml`
4. `fluentcoder/_assets/config/category_overrides.toml`

Example (`category_overrides.toml.example`):

```toml
"My Misclassified Runner" = "tube_rack"
```

```python
from fluentcoder.catalog import load_category_overrides
overrides = load_category_overrides()
```

## SQL index

`fluentcoder/catalog/catalog.py`

The index lives at `fluentcoder/catalog/install_index.db` (inside the package
— so `pip install` ships an empty schema and the first import populates it).

### Schema

```sql
CREATE TABLE install (
    install_path  TEXT PRIMARY KEY,
    fingerprint   TEXT NOT NULL,
    built_at      TEXT NOT NULL
);

CREATE TABLE components (
    guid          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    grid_x        INTEGER, grid_y INTEGER,    -- well grid for labware,
                                               -- site grid for carriers
    dim_x_mm      REAL, dim_y_mm REAL, dim_z_mm REAL,
    site_count    INTEGER
);
CREATE INDEX components_by_name     ON components(name);
CREATE INDEX components_by_category ON components(category);

CREATE TABLE workspaces (guid TEXT PRIMARY KEY, name TEXT NOT NULL, file_path TEXT NOT NULL);
CREATE INDEX workspaces_by_name ON workspaces(name);

CREATE TABLE sites (guid TEXT PRIMARY KEY, file_path TEXT NOT NULL);

CREATE TABLE liquid_classes (
  guid       TEXT PRIMARY KEY,    -- the .xlqc filename GUID;
                                  -- this is what the .xscr's <Reference TypeId="LiquidClass"> uses
  name       TEXT NOT NULL,       -- ObjectName from inside the .xlqc
  head       TEXT,                -- 'Fca' / 'Mca' / 'LiHa' / NULL — from <PipettingDeviceType>
  file_path  TEXT NOT NULL
);
CREATE INDEX liquid_classes_by_name ON liquid_classes(name);

CREATE TABLE connectors (
  guid            TEXT NOT NULL,
  name            TEXT NOT NULL,
  component_guid  TEXT NOT NULL,
  site_guid       TEXT NOT NULL,
  is_default      INTEGER NOT NULL,
  file_path       TEXT NOT NULL,
  install_key     TEXT NOT NULL,
  PRIMARY KEY (install_key, guid)
);
CREATE INDEX connectors_by_name ON connectors(install_key, name);
CREATE INDEX connectors_by_site ON connectors(install_key, site_guid);
CREATE INDEX connectors_by_component ON connectors(install_key, component_guid);
```

The `liquid_classes` table is populated by walking
`SystemSpecific/LiquidClasses/*.xlqc` during `build_index`. The renderer
calls `resolve_liquid_class_by_name(name)` at render time to fill the
`.xscr`'s top-level `<Reference TypeId="LiquidClass">`. When the index
is empty (offline / dev), the renderer falls back to the GUID hardcoded
in `_assets/config/generation.yaml`.

### Public query API

```python
from fluentcoder.catalog import (
    resolve_by_name,            # (name) -> CatalogEntry | None
    find_components,            # (pattern) -> list[CatalogEntry]    (LIKE %pattern%)
    list_by_category,           # (category) -> list[CatalogEntry]
    category_counts,            # () -> dict[str, int]
    resolve_workspace_by_name,  # (name) -> WorkspaceEntry | None
    resolve_liquid_class_by_name,  # (name) -> LiquidClassEntry | None
    resolve_connector_by_guid,  # (guid) -> ConnectorEntry | None
    install_info,               # () -> dict | None  (install_path, fingerprint, built_at)
    index_exists,               # () -> bool
    open_index,                 # context manager for direct SQL
    load_category_overrides,    # () -> dict[str, str]
    index_connector_paths,      # ([paths]) -> int  (targeted .xcon indexing)
    load_xcon, parse_connector, # on-demand .xcon parsing
)
```

### `CatalogEntry`

```python
@dataclass(frozen=True)
class CatalogEntry:
    guid: str
    name: str
    category: str
    file_path: Path
    grid_x: int | None
    grid_y: int | None
    dim_x_mm: float | None
    dim_y_mm: float | None
    dim_z_mm: float | None
    site_count: int | None
```

## Indexer

`fluentcoder/catalog/indexer.py`

```python
def build_index(
    install_path: Path | None = None,   # default: $FLUENTCODER_FC_INSTALL or built-in
    db_path: Path | None = None,        # default: fluentcoder/catalog/install_index.db
) -> dict[str, int]
```

Walks `<install>/SystemSpecific/Worktable/Components/`, `Workspaces/`,
`Sites/`, and (when sites exist) connectors referenced by indexed `.xsit`
files under `Connectors/`. For each `.xcmp`:

1. `load_xcmp(path)` → typed component view.
2. `infer_category(comp)` → category string (with optional overrides).
3. Insert one row into `components` with cached scalar attributes.

Workspaces and sites are also indexed. Connectors referenced by indexed
sites are parsed via `load_xcon` and stored in `connectors` — not a full
install walk of all ~14k `.xcon` files. Use `index_connector_paths([...])`
for targeted connector indexing.

The install row records a fingerprint — a sha-256 of
sorted `(relative_path, mtime)` tuples under the worktable subtree — so we
can cheaply check whether the install has drifted since the last build.

The build is **idempotent** — it `DELETE`s all rows first, then re-inserts.
On a 629-component install the build takes 5–15 seconds.

### Default install path

`fluentcoder/catalog/indexer.py:21`

```python
DEFAULT_INSTALL_PATH = Path(r"C:\ProgramData\Tecan\VisionX\Database")
```

Override with the `FLUENTCODER_FC_INSTALL` environment variable, or pass
`install_path=` explicitly.

### Multi-install index DB paths

`fluentcoder/catalog/paths.py`

Non-default install directories use a keyed index at
`catalog/indexes/install_<hash>.db` so multiple FluentControl installs can
coexist on one machine. Override the DB path explicitly with
`FLUENTCODER_INDEX_DB`. CI/offline tests set `FLUENTCODER_TEST_CATALOG_DB`
to `tests/fixtures/synthetic_catalog/install_index.db` (takes priority over
the packaged default index).

### Auto-build on first import

`fluentcoder/catalog/__init__.py:46-66`

```python
def ensure_index() -> None:
    # 1. If index missing → build from default install.
    # 2. If fingerprint drifted → rebuild (unless FLUENTCODER_NO_AUTO_REBUILD=1).
    # 3. If install unreachable → no-op; offline fallback handles empty index.
```

`ensure_index()` runs from `fluentcoder/__init__.py` on first import. It's
silent when the index is current, slow-but-once when building, and a no-op
if the install isn't reachable. Indexing failures are swallowed — an
exception during indexing must never break `import fluentcoder`.

## `Worktable.from_workspace`

`fluentcoder/worktable.py:67`

```python
@classmethod
def from_workspace(
    cls,
    name: str,                    # workspace ObjectName, e.g. '780_Empty'
    *,
    auto_place: bool = True,
    protocol_name: str = "",
    comment: str = "",
) -> "Worktable"
```

Resolution flow:

1. Catalog index must exist; raises `MissingSimValueError` with a hint if not.
2. Resolve the installed workspace in this order:
   - exact workspace GUID match (if provided)
   - exact workspace name match
   - if both hit the same `.xwsp` file, accept it
   - if both hit different files, raise `ValueError` for ambiguity
3. `load_xwsp(path)` → typed workspace view.
4. **Build `valid_slots`** from `available_sites`. Each entry is
   `(site_path, base_location_identifier)`. For each visited site:
   - Use `base_location_identifier` if present, else fall back to the
     workspace's first `LocationGroupName`, else `'Site'`.
   - Translate the site index from 0-based (XWSP) to 1-based (FluentControl
     positions).
5. **Auto-place occupants** (if `auto_place=True`). For each occupant:
   - Resolve the occupant's catalog name (`resolve_by_name`).
   - Dispatch `category → Python class` via
     `fluentcoder.labware.CATEGORY_TO_CLASS` (defaults to `FixedDeck`).
   - Synthesize a unique label `f"{catalog_name}@{position}"`.
   - Call `wt.place(...)` — which validates against `valid_slots`.

After this, `wt.place(...)` raises `InvalidSlotError` for any slot not in
`valid_slots`.

### CATEGORY_TO_CLASS

`fluentcoder/labware/__init__.py:36`

```python
CATEGORY_TO_CLASS: dict[str, type[Labware]] = {
    "plate":        Plate,
    "trough":       Trough,
    "tip_box":      TipBox,
    "magnet_rack":  MagnetRack,
    "tube_rack":    TubeRack,
    "wash_station": WashStation,
    "waste_chute":  WasteChute,
    "hotel":        Hotel,
    "adapter":      Adapter,
    "fixed_deck":   FixedDeck,
}
```

Authors can override this mapping (e.g. to plug in a behaviorally-richer
subclass) before calling `from_workspace`.

## Offline / no-install fallback

When `index_exists()` is False — typical on CI or a dev box without
FluentControl — the labware classes synthesise a generic well grid from
their *taxonomic* shape (`Plate96.taxonomic_grid = (8, 12)`). The wells get
a family default `max_volume_ul`:

```
Plate96       350 µL    Trough25mL    25,000 µL    MCA100Box   capacity=100
Plate96Deep  1000 µL    Trough100mL  100,000 µL    MCA200Box   capacity=200
Plate384       90 µL    Waste        300,000 µL    FCA50Box    capacity=50
                                                    FCA200Box   capacity=200
                                                    FCA1000Box  capacity=1000
```

mm fields stay `None`. `Worktable.from_workspace(...)` raises immediately
(no way to fake a workspace without the .xwsp file).

A `CatalogIndexMissing` `UserWarning` fires once per process on the first
synthesised construction so the user knows they're offline.

## Refresh strategies

| When the index is | Behavior |
|---|---|
| Missing | Auto-built on first import (silent on success). Manually via `fluentcoder catalog refresh`. |
| Stale (install changed) | Auto-rebuilt on import when fingerprint drifts. Opt out with `FLUENTCODER_NO_AUTO_REBUILD=1`, then use `fluentcoder catalog refresh`. |
| Corrupt | `index_exists()` returns False, `ensure_index` rebuilds on next import. |

### Offline synthetic catalog (CI)

`tests/fixtures/synthetic_catalog/` holds a tiny FluentControl worktable tree
(common plates, FCA tip variants, and a sample SAT workspace location) plus a
generated `install_index.db`. Build or refresh:

```
python tests/fixtures/synthetic_catalog/bootstrap.py
```

Tests use the `synthetic_catalog` pytest fixture or set
`FLUENTCODER_TEST_CATALOG_DB` to the fixture DB path.

Force rebuild from Python:

```python
from fluentcoder.catalog.indexer import build_index
build_index()                                  # default install path
build_index(install_path="C:/Custom/Install")  # explicit
```

Or via CLI:

```
fluentcoder catalog refresh
fluentcoder catalog refresh --install C:\Custom\Install
```
