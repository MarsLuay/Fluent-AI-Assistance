## Edit Boundaries

Codex may edit:

- `src/**`
- `scripts/**`
- `index.html`
- `vite.config.ts`
- `tsconfig.json`
- `package.json`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `README.md`
- this `AGENTS.md`

Codex must not hand-edit:

- `public/models/fluent/**` generated meshes/textures/manifests (except `local/.gitkeep` + `local/README.md`, and legacy `textures/README.md` + `textures/.gitkeep`)
- `public/models/fluent/local/**` (gitignored host rebuilds: `*.glb`, `manifest.json`, `textures/**`, registry/connector/procedural)
- `public/models/fluent/textures/**` except `README.md` / `.gitkeep` (legacy stub; rebuild into `local/textures/`)
- `.cache/**`
- any generated `.glb`, `.jpg`, or manifest/registry/connector/procedural JSON
  under `public/models/fluent` (rebuild into `local/` instead; never add tracked
  `*.glb` / `manifest.json` / `registry.json` / `connector-graph.json` /
  `procedural-specs.json` / texture JPGs beside the tracked models root)

Need regenerate assets? Use owning script, do not patch output:

```bash
npm run extract:fluent-meshes -- /path/to/FullExport.zeia \
  --mesh-guids-from <labware_catalog.json>   # GUID-list rebuild → local/*.glb
npm run build:zeia-sample-cache  # optional; requires a local sample ZEIA export when present
python3 ../tools/simulator/extract_fluent_textures.py  # -> public/models/fluent/local/textures/
python3 ../tools/simulator/build_fluent_registry.py   # writes public/models/fluent/local/registry.json
python3 ../tools/simulator/build_connector_graph.py   # writes public/models/fluent/local/connector-graph.json
python3 ../tools/simulator/build_procedural_fallback_specs.py  # writes public/models/fluent/local/procedural-specs.json
python3 ../tools/simulator/merge_fluent_mesh_libraries.py --apply
# preserve pinlist = install/ZEIA Components mesh GUIDs (+ optional --preserve-from labware_catalog.json)
# never a baked DEFAULT_PRESERVE_SIM_GUIDS host list
```

Sample-cache rebuild runs only if a local ZEIA is set via `TECAN_SIMULATOR_SAMPLE_ZEIA` or found under `ready-to-import/*/source/original-sources/`.

Host DB mesh/registry/connector/procedural output is **local-only** (`public/models/fluent/local/`, gitignored): rebuild cache for this machine, **not product law**. Asset URLs are `/models/fluent/local/<meshGuid>.glb`. Prefer, in order: package ZEIA `connector_graph.json` (full Snap/`Connectors/*.xcon` walk from the same DataStore as host rebuild) / `labware_catalog.json`, then `local/` rebuild from install/ZEIA DataStore. Missing both → no Snap graph (null), not a shipped JSON. CapHolder / `Falcon50_Cap_nest_*` pins live only in local rebuilds. Mesh merge preserve GUIDs come from that install/ZEIA Components list (or `local/preserve-mesh-guids.json`), not baked product pinlists.

