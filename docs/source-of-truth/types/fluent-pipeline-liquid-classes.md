# Types: fluent-pipeline-liquid-classes

Schema version string: `tecan.liquid_classes.v2` (`LIQUID_CLASSES_SCHEMA_VERSION`).

## Catalog document

| Field | Type | Notes |
| --- | --- | --- |
| `schema_version` | string | `tecan.liquid_classes.v2` |
| `source` | string | e.g. `zeia_xlqc` |
| `entry_count` | int | |
| `entries` | array | see entry |
| `parse_errors` / `parse_error_count` | optional | capped errors |

## Entry (from `_catalog_entry`)

| Field | Notes |
| --- | --- |
| `name`, `guid`, `aliases` | filename GUID |
| `head`, `supported_heads` | from `PipettingDeviceType` |
| `profiles[]` | `{head, tip, aspirate?, dispense?, mix?, detection?, microscript_sections?, microscript?}` |
| `microscript` | `[{name, commands: [TypeLeaf…], commands_truncated?}]` , Object Type leaf names only |
| `aspirate` / `dispense` / `mix` / `empty_tips` | flattened summaries |
| `source_path` | optional |

Typed sets mined: `Tecan.Core.Pipetting.LiquidClassEquationSet`, `…DetectionAndPositioningSet`, `…LiquidSubClass`.
MicroScript bodies: ordered Object `Type` leaf names under `MicroScriptSection/Objects` (nested groups walked); payloads not mined.
