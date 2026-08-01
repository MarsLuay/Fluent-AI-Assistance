# Protocol Simulator

Layer 4 of the Fluent AI-Assistance workspace.

This is a real TypeScript/Vite application for inspecting generated protocol
bundles, worktable geometry, and Fluent asset caches. The protocol-builder CLI
can discover it and launch it with `python3 -m fluent_pipeline.cli launch-simulator`,
and the local launcher in `source/tools/` uses the same app folder.

## Common Commands

From this folder:

```bash
npm install
npm run dev
npm test
npm run build
npm run preview
npm run extract:fluent-meshes
npm run build:zeia-sample-cache  # optional; requires a local sample ZEIA export when present
python3 ../tools/simulator/extract_fluent_textures.py  # -> public/models/fluent/local/textures/
python3 ../tools/simulator/build_fluent_registry.py   # -> public/models/fluent/local/ (gitignored)
python3 ../tools/simulator/build_connector_graph.py
python3 ../tools/simulator/merge_fluent_mesh_libraries.py
```

No tracked `*.glb` / `manifest.json` / `registry.json` / `connector-graph.json` /
`procedural-specs.json` under `public/models/fluent/`. Rebuild meshes + host
indexes from your FluentControl install or ZEIA into `public/models/fluent/local/`
(gitignored) — **rebuild-only, not product law**. Asset URLs:
`/models/fluent/local/<meshGuid>.glb`. Package imports may ship
`source/connector_graph.json` from full ZEIA geometry. Until then, Snap/registry
loaders return null and the deck has no host GLBs. Host CapHolder GUIDs and
`Falcon50_Cap_nest_*` pins belong only in local rebuilds.

The `npm test` command runs the worktable geometry checks and the mesh archive
selection checks.
`npm run build:zeia-sample-cache` rebuilds the cached ZEIA sample bundle used by
the local launcher. It only succeeds when a local sample ZEIA export exists under
`ready-to-import/*/source/original-sources/*.zeia` (or `TECAN_SIMULATOR_SAMPLE_ZEIA`).

## Folder Map

- `scripts/assets/` - Node wrappers over `source/tools/simulator/*`
- `scripts/test/` - geometry / archive / readiness / IR / hardware checks
- `scripts/cache/` - ZEIA sample cache builder
- `src/` - simulator UI, parsing logic, data loaders, and simulation state.
- `public/models/fluent/` - tracked placeholder only (no host GLBs / texture JPGs).
- `public/models/fluent/local/` - host/ZEIA mesh GLBs, `manifest.json`, registry,
  connector, procedural rebuilds (gitignored; not product law).
- `public/models/fluent/local/textures/` - decoded `.xtx` images + texture
  `manifest.json` (gitignored; `/models/fluent/local/textures/<guid>.jpg`).
- `public/models/fluent/textures/` - legacy stub (`README.md` / `.gitkeep` only);
  do not write host manifests here.
- `index.html`, `vite.config.ts`, `tsconfig.json`, `package.json`, and
  `CONTRIBUTING.md` - hand-edited app configuration and project guidance.
- `package-lock.json` - npm-managed dependency lockfile.
- `SECURITY.md` - security contact and reporting guidance for this app.

## Editing Boundaries

Keep the hand-authored app code separate from the generated cache it consumes:

- Hand-edited simulator sources live in `src/**`, `scripts/**`, and the config
  files in this folder.
- Generated simulator assets live under `public/models/fluent/**` and should be
  rebuilt, not edited directly.
- Generated local cache data may also appear under `.cache/**` during launch or
  sample-cache builds.
- `package-lock.json` should change through npm, not by manual editing.

If you need to refresh the generated assets, run the relevant helper in
`source/tools/` and then re-run the simulator tests.

## Verification

Run these after app or simulator changes:

```bash
npm test
npm run build
```

If you touch launch or discovery behavior, also smoke-test the local launcher:

```bash
python3 ../tools/simulator/launch_simulator.py --no-open
```
