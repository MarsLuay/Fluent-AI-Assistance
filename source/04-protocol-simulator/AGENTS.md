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

> Detailed rules: [AGENTS.details/edit-boundaries.md](AGENTS.details/edit-boundaries.md). Read them before working in this area.

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
