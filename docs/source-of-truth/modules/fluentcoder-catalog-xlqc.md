# Module: fluentcoder-catalog-xlqc

**Paths:** `source/03-protocol-builder/libs/fluentcoder/fluentcoder/catalog/xlqc.py`, `catalog/catalog.py`, `catalog/indexer.py`  
**Purpose:** Index install/ZEIA liquid classes into SQLite; resolve name → GUID for renderer.  
**Public surface:** `load_xlqc`, `XlqcLiquidClass`, `resolve_liquid_class_by_name`, `open_index`, `index_exists`, indexer `build_index`  
**Depends on:** `xml_compat`, xcmp helpers `_find`/`_local`/`_text`  
**Invariants:** Filename GUID is authority (ignore inner UniqueId); exact SQL name match (no instance-suffix strip here).  
**Related functions:** [functions/fluentcoder-catalog-xlqc.md](../functions/fluentcoder-catalog-xlqc.md)  
**Related types:** [types/fluentcoder-catalog-xlqc.md](../types/fluentcoder-catalog-xlqc.md)
