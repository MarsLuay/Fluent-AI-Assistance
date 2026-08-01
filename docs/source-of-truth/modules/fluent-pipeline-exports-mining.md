# Module: fluent-pipeline-exports-mining

**Paths:** `source/03-protocol-builder/fluent_pipeline/` → 12 files
**Purpose:** ZEIA/import mining and packaging: catalogs, liquid classes, connectors, drivers, bindings, ready-to-import exports.
**Public surface:** see [functions/fluent-pipeline-exports-mining.md](../functions/fluent-pipeline-exports-mining.md)
**Depends on:** fluentcoder / shared tecan libs (varies by file)
**Invariants:** ZEIA-mined evidence over baked product law; fail closed when bindings missing (see cross-cutting).
**Related functions:** [functions/fluent-pipeline-exports-mining.md](../functions/fluent-pipeline-exports-mining.md)
**Related types:** [types/fluent-pipeline-exports-mining.md](../types/fluent-pipeline-exports-mining.md)

## Files

- `bundle_lifecycle.py`
- `bundle_media.py`
- `connector_coverage_export.py`
- `connector_graph_export.py`
- `delivery_bundle.py`
- `driver_macros_export.py`
- `exports.py`
- `fluent_naming.py` (shared instance-suffix strip; also used by liquid-classes/aliases)
- `fluentcontrol_inventory.py`
- `labware_catalog_export.py`
- `script_folder_bindings_export.py`
- `worktable_datastore.py` (shared ZEIA/install worktable root resolve/discover)
- `worktable_geometry.py`
- `zeia_filesystem.py`
