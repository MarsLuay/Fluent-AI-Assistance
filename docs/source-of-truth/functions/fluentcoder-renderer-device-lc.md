# Functions: fluentcoder-renderer-device-lc

Source roots: `libs/fluentcoder/fluentcoder/compiler/renderer.py`

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `_resolve_device_pair` | `renderer.py` | `(protocol, *, step_alias, step_available_id, role) -> (alias, id)` | Resolve without invent/cross-fill | none |
| `_require_device_pair` | same | `(alias, available_id) -> (alias, id)` | Fail closed empty or `Instrument=` in AvailableID | `RenderError` |
| `_assert_template_device_bindings` | same | `(template, params) -> None` | Gate templates with DeviceAlias/AvailableID/ModuleName | `RenderError` |
| `_resolve_liquid_class_guid` | same | `(name) -> str` | SQL → JSON → site yaml cascade | swallows catalog import errors |
| `_resolve_liquid_class_guid_from_json` | same | `(name) -> str` | JSON catalog path(s); pipeline helper then `liquid_class_guid_from_catalog_entries` | file I/O |
| `liquid_class_guid_from_catalog_entries` | same (module) | `(name, entries) -> str` | Instance-suffix + alias match; mirrors export | none |
