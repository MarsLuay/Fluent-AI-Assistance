# Protocol Simulator Contract

Contract for local 3D protocol simulator in
`source/04-protocol-simulator/`.

Use folder for TypeScript/Vite app, launch flow, mesh/texture asset ingest, simulator checks.

## Command Rules

- Prefer local npm scripts in this folder.
- Use repo Python generators in `../tools/` to refresh derived mesh, texture, registry, connector data.
- Do not hand-edit generated asset trees under `public/models/fluent/**`.
- Do not hand-edit `.cache/**` launch cache data.
- Treat `package-lock.json` as npm-managed output, not manual-edit file.
- Do not bake host/install mesh GUIDs into `src/data/labwareCatalog.ts`. Static TS catalog is soft well/dim templates only (aliases); it does **not** index `meshNameHints` as install mesh identity. Dims + WorktableMesh GUID→GLB come from import: ZEIA `labware_catalog.json` (`mesh_guid` / `mesh_guids` / `mesh_names`, plus `pipettable` wells/cavity, `grip`, `site_templates`, `compatible_components` when mined) and/or Components `.xcmp` via `registerLabwareCatalogFromDefinitions` (never component GUIDs as GLB stems).
- Do not invent catalog names (CapHolder_long_44mm, Falcon tube runners, 61mm Nest, Resolvex, …) in `labwareCatalog.ts` / `DeckScene.tsx` / `parsers.ts` when ZEIA catalog / `.xcmp` definitions are missing — use exact worktable/ZEIA names only.
- Static catalog: soft well-grid **format** templates only (96/384/24/reservoir; rows/cols/pitch; footprint/volume = 0). No tip SKUs, filter plates, or Source/Destination invent aliases — those come from ZEIA.
  Not indexed for `resolveLabwareGeometry` — dims come only from ZEIA
  `labware_catalog.json` / `.xcmp`. Generic placeholder is the sole non-ZEIA resolve
  fallback. No site devices, no named `/models/labware|devices/*.glb`, no static
  `meshNameHints`. Mesh identity is `/models/fluent/local/<meshGuid>.glb` from ZEIA.
  Keyword→profile: ZEIA `FunctionalGroup` first (`hardwareProfileFromZeia`). Exact
  phrases `tube holder` / `tube runner` only (FG is often `Carrier.Miscellaneous`).
  No filter/DWP/tip/adapter/falcon keyword invent.
- Do not commit host-derived mesh GLBs, `manifest.json`, `registry.json`, `connector-graph.json`, or `procedural-specs.json`. There are **no** tracked mesh libraries under `public/models/fluent/`. Rebuild into gitignored `public/models/fluent/local/` from your install/ZEIA (`extract_fluent_meshes` / `merge_fluent_mesh_libraries`), or load package `source/connector_graph.json` / `labware_catalog.json` from full ZEIA. Loaders return null / skip until local meshes exist. Never commit CapHolder host GUIDs or `Falcon50_Cap_nest_*` site pins.

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

## Verification

After app or simulator changes, run:

```bash
npm test
npm run build
```

If launch/discovery behavior changed, smoke-test local launcher:

```bash
python3 ../tools/simulator/launch_simulator.py --no-open
```

If protocol-builder integration changed, also verify simulator discoverable through `python3 -m fluent_pipeline.cli launch-simulator`.