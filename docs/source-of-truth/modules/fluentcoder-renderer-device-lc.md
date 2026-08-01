# Module: fluentcoder-renderer-device-lc

**Paths:** `source/03-protocol-builder/libs/fluentcoder/fluentcoder/compiler/renderer.py`  
**Purpose:** Emit FluentControl XML; resolve liquid-class GUIDs; fail-closed device bindings.  
**Public surface (relevant):** `_resolve_liquid_class_guid`, `_resolve_liquid_class_guid_from_json`, `_resolve_device_pair`, `_require_device_pair`, `_assert_template_device_bindings`  
**Depends on:** catalog index, optional `fluent_pipeline.liquid_classes_export`, `generation.yaml`  
**Invariants:** No DeviceAlias↔AvailableID cross-fill; no empty device for templates needing `{{DeviceAlias}}`; LC GUID cascade SQL → JSON → site yaml only.  
**Related functions:** [functions/fluentcoder-renderer-device-lc.md](../functions/fluentcoder-renderer-device-lc.md)
