# Repository Tools

Repository-level Python and PowerShell utilities for the Fluent AI-Assistance
workspace.

## Layout

| Subfolder | What |
|-----------|------|
| `simulator/` | Mesh/texture extract, registry, connector graph, diagnose, launch |
| `api_v2/` | API V2 mining, assignments, roster, methods catalog |
| `connectors/` | Connector capability extractor |
| `registry/` | Command-registry provenance enrichment |
| `prompt/` | Prompt-builder app |
| `common/` | Shared helpers (`terminal_progress`, `paths`) |

Package import name remains `tecan_tools` (setuptools maps this folder). Prefer
`tecan_tools.simulator.*` etc.; legacy `from tecan_tools import extract_fluent_meshes`
still works via package `__getattr__`.

## Common Commands

From this folder:

```bash
python3 simulator/extract_fluent_meshes.py FullExport.zeia \
  --mesh-guids-from ../path/to/labware_catalog.json   # preferred: GUID-list → local/*.glb
python3 simulator/extract_fluent_meshes.py FullExport.zeia     # full ZEIA dump → local/ (gitignored)
python3 simulator/extract_fluent_textures.py
python3 simulator/build_fluent_registry.py     # writes .../public/models/fluent/local/registry.json
python3 simulator/build_connector_graph.py     # writes .../public/models/fluent/local/connector-graph.json
python3 simulator/build_procedural_fallback_specs.py  # writes .../local/procedural-specs.json
python3 simulator/merge_fluent_mesh_libraries.py --apply
# Preserve pinlist: --host-install Components (default), --preserve-from labware_catalog.json,
# and/or local/preserve-mesh-guids.json — not a baked DEFAULT_PRESERVE_SIM_GUIDS list.
python3 simulator/diagnose_model_coverage.py
python3 api_v2/extract_api_v2_improvements.py
python3 api_v2/assign_api_v2_agents.py
python3 api_v2/build_api_v2_retry_queue.py
python3 simulator/launch_simulator.py --no-open
powershell -ExecutionPolicy Bypass -File api_v2/list_api_v2_methods.ps1
```

Host-derived `*.glb` / `manifest.json` / `registry.json` / `connector-graph.json` /
`procedural-specs.json` go under `public/models/fluent/local/` (gitignored).
Rebuild-only for this machine — **not product law**. There are no tracked mesh
libraries or host JSON under `public/models/fluent/` — regenerate per machine
from host install or ZEIA DataStore (`--install` / `extract_fluent_meshes`), or
use package `source/connector_graph.json` from full ZEIA import. Do not commit
CapHolder host GUIDs or `Falcon50_Cap_nest_*` site pins.

`api_v2/launch_api_v2_implementation_agents.py` prints the prompts used to dispatch
API V2 issue work.

## File Roles

- Hand-edited source lives in the Python modules, the PowerShell helper, the
  package metadata, the README, and `api_v2/API_V2_AGENT_ROSTER.md`.
- `api_v2/api_v2_methods.json` is the checked-in input catalog for API V2 mining.
- Generated API V2 reports land under
  `ready-to-import/_shared/temp_files/build/api_v2/`
  (`api_v2_workflow_improvements.json`, `api_v2_agent_assignments.json`,
  `api_v2_retry_queue.json`, `api_v2_issues/{id}.md`).
- Simulator asset outputs written under
  `source/04-protocol-simulator/public/models/fluent/local/**` are generated and
  should not be edited here by hand.
- Host-derived mesh GLBs, mesh manifest, registry, connector, and procedural JSON
  belong in `public/models/fluent/local/` (gitignored). Do not create tracked
  `*.glb` / `manifest.json` / `registry.json` / `connector-graph.json` /
  `procedural-specs.json` under `public/models/fluent/`.

If a change updates the generator behavior, refresh the derived JSON or asset
cache instead of patching the output files directly.

## Verification

Run these after editing the Python tooling:

```bash
python3 -m compileall .
```

For API V2 workflow changes, also rerun the generators and confirm the derived
reports were rewritten:

```bash
python3 api_v2/extract_api_v2_improvements.py
python3 api_v2/assign_api_v2_agents.py
python3 api_v2/build_api_v2_retry_queue.py
```

If a tools change affects simulator assets, re-run the simulator tests in
`../04-protocol-simulator` afterward.
