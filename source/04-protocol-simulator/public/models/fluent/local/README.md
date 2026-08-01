# Local Fluent meshes / registry / connector / procedural specs

**Rebuild-only machine cache — not product law.**

These files are gitignored host/ZEIA dumps for *this* machine’s simulator. They are
not the Fluent world for clones, CI, or shipped product. Prefer package
`source/labware_catalog.json` / `source/connector_graph.json` /
`source/connector_coverage.json` (from full ZEIA) when present; use `local/`
only as a per-install rebuild.

Populate (meshes keyed by WorktableMesh GUID, never tracked at `../`):

```bash
# Textures → local/textures/ (gitignored; portable manifest labels only)
python3 source/tools/simulator/extract_fluent_textures.py --install /path/to/DataStoreOrHostDb

# Selective rebuild from labware_catalog mesh GUID list (preferred)
python3 source/tools/simulator/extract_fluent_meshes.py /path/to/FullExport.zeia \
  --mesh-guids-from ready-to-import/<ctx>/temp_files/labware_catalog.json

# Or full ZEIA mesh dump into local/ (still gitignored)
python3 source/tools/simulator/extract_fluent_meshes.py /path/to/FullExport.zeia

# Merge host DataStore meshes into the local library
python3 source/tools/simulator/merge_fluent_mesh_libraries.py --apply

python3 source/tools/simulator/build_fluent_registry.py      # -> local/registry.json
python3 source/tools/simulator/build_connector_graph.py      # -> local/connector-graph.json
python3 source/tools/simulator/build_procedural_fallback_specs.py  # -> local/procedural-specs.json
```

Outputs (never commit):

- `*.glb` + `manifest.json` — install/ZEIA mesh library (`/models/fluent/local/<guid>.glb`)
- `textures/*` + `textures/manifest.json` — decoded `.xtx` images (`/models/fluent/local/textures/<guid>.jpg`)
- `registry.json` — host-db / ZEIA DataStore asset index
- `connector-graph.json` — Snap/.xcon edges for this install
- `procedural-specs.json` — includes CapHolder site/nest pins from that install
- `preserve-mesh-guids.json` — optional mesh GUID pinlist for extract/merge
  (else mined from install/ZEIA Components / `labware_catalog.json`)

Simulator loaders read `local/` when present and return null / skip meshes when
missing. Do not recreate tracked `*.glb` / `manifest.json` / `registry.json` /
`connector-graph.json` beside this folder under `../`.
