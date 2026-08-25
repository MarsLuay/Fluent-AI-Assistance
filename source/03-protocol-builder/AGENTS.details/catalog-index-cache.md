## Catalog Index Cache

- Imported full ZEIA worktable geometry also writes
  `ready-to-import/<ctx>/temp_files/labware_catalog.json` (and
  `source/labware_catalog.json` in packaged bundles). That file is the
  lab-specific labware name/component-GUID/`mesh_guid(s)`/dimension catalog,
  plus mined `pipettable` (wells/cavity), `grip` (`AllowedGripModes` + Force),
  `site_templates` (arrangement site GUIDs + xsit metadata when present), and
  `compatible_components` (xcmp refs + workspace occupancy).
  Entries keep `guid` (component) separate from `mesh_guid` / `mesh_guids`
  (WorktableMesh refs used for GLBs). Do not hardcode site labware names or
  host mesh GUIDs into `config/aliases/*.yaml` or the simulator static catalog;
  let import/package populate them. `ready-to-import/` stays gitignored.
- The same import writes `liquid_classes.json` (packaged as
  `source/liquid_classes.json`) from ZEIA `SystemSpecific/LiquidClasses/*.xlqc`
  (and manifest `.xlqc` objects): schema `tecan.liquid_classes.v2` with name,
  filename GUID, supported heads, head×tip `profiles[]` (EquationSet /
  DetectionAndPositioningSet scalars + Microscript section names and ordered
  Object ``Type`` command sequences — not full micro-command payloads), plus
  flattened aspirate/dispense/mix summaries for older consumers. Never invent
  liquid-class name/GUID in shipped `generation.yaml`.
- The same import writes `driver_macros.json` (macro_name/module_name mined from
  script `LegacyDriverMacro` / `ApplicationDriverMacro` usages) and
  `script_folder_bindings.json` (Scripts-folder tree + script↔worktable
  bindings). Init-worktable selection prefers those ZEIA bindings over soft
  filename scoring when present.
- Large ZEIA imports that skip detailed `worktable_geometry` (object-entry
  limit) still build `labware_catalog.json` from a Components `.xcmp` walk when
  possible.
- CapBC / tube-scan prep schema is mined at generate time
  (`subroutine_deck_locations`): CapBC subroutine VariableDefinitions + call
  VariableMappings as schema; GripperClose/Open from those decl defaults or
  source SetVariable (never invented widths); TubeRunnerName from worktable
  placements whose catalog contains the exact phrases `tube runner` /
  `tube holder`. CapBC in the subroutine name is only a soft secondary enable.
- fluentcoder `_assets/reference/labware.yaml` is an optional reference dump
  (Falcon/Resolvex rows). Renderer loads it only when
  `FLUENTCODER_USE_LABWARE_YAML=1`; prefer ZEIA `labware_catalog.json`.
- The same import writes sibling `connector_coverage.json` (packaged as
  `source/connector_coverage.json`): one coverage row per component that has
  connectors in **this** ZEIA/install geometry. `connector_graph.json` prefers a
  full `Connectors/*.xcon` Snap walk under the extracted DataStore (same scope as
  host `build_connector_graph.py --install`), not only connectors already mined
  into detailed `worktable_geometry` (large ZEIA imports skip that parse). Never
  assume soft site-labware family profiles in product source, and never bake host
  connector GUID totals.

- The fluentcoder catalog index (`install_index.db`) is expensive to build from a
  full ZEIA (~9 min cold) and is the dominant cost of a first `generate`.
  `ensure_project_catalog` (in `project_catalog.py`) caches it two ways: a
  per-context DB under
  `ready-to-import/<ctx>/temp_files/build/.fluentcoder_catalog/`,
  and a shared content-addressed cache under
  `ready-to-import/_shared/temp_files/cache/catalog/<hash>/`.
- The cache key is a SHA-256 over the catalog source files (Components, Workspaces,
  Sites, LiquidClasses) by relative path + size + content, so it is independent of
  mtimes. This matters because import re-extracts files with fresh mtimes, which
  invalidates the mtime-based per-context freshness check; the content hash lets a
  re-import or a differently named context reuse a prior build (a fast DB copy,
  ~seconds) instead of rebuilding.
- Hashing ~1.3k files / ~280 MB takes ~6 s and is only paid on a cold context
  (the in-context freshness fast path skips it). A cache write failure never
  blocks generation. To pre-seed the cache from an already-built context, copy its
  DB via `_store_in_shared_cache(project_catalog_db_path(ctx), _shared_cache_db_path(_catalog_content_hash(project_datastore_dir(ctx))))`.

