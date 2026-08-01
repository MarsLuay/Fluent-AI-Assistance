# Module: fluent-pipeline-liquid-classes

**Paths:** `source/03-protocol-builder/fluent_pipeline/liquid_classes_export.py`  
**Purpose:** Mine ZEIA `*.xlqc` → portable `liquid_classes.json` (`tecan.liquid_classes.v2`).  
**Public surface:** `parse_xlqc`, `build_liquid_classes_catalog`, `write_liquid_classes_*`, `load_liquid_classes_catalog`, `alias_maps_from_liquid_classes_catalog`, `resolve_liquid_class_guid`, `resolve_liquid_classes_dir`, `discover_liquid_classes_dir`, `strip_fluent_instance_suffix` (owned by `fluent_naming`)  
**Depends on:** `fluent_pipeline.xml_compat`, `fluent_pipeline.runner.write_json`, `fluent_pipeline.fluent_naming`  
**Invariants:** Never invent LC name/GUID/params; filename stem = GUID; head×tip profiles from typed sets; top-level aspirate/dispense/mix are flattened summaries.  
**Related functions:** [functions/fluent-pipeline-liquid-classes.md](../functions/fluent-pipeline-liquid-classes.md)  
**Related types:** [types/fluent-pipeline-liquid-classes.md](../types/fluent-pipeline-liquid-classes.md)

## Seam vs fluentcoder

`fluentcoder.catalog.xlqc.load_xlqc` extracts **SQL catalog metadata only** (guid/name/heads). This module extracts **pipetting params**. Deliberate dual parsers , see `conflicts.md` D001.

XML nav helpers (`_find` / `_local_name` / `_text`) look like `xcmp` twins but **`_text` is `""` here vs `None` in xcmp** , accepted dual, conflicts D002.
