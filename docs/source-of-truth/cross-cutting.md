# Cross-cutting

## Config

- FluentCoder: `libs/fluentcoder/fluentcoder/_assets/config/generation.yaml` , site opt-in only; liquid_class guid/name empty by default.
- Env: `FLUENTCODER_LIQUID_CLASSES_JSON` for portable catalog path; `FLUENTCODER_ROOT` / `FLUENTCODER_PYTHON` for alternate fluentcoder checkout.
- Renderer config keys: `liquid_classes_catalog_path`, `liquid_classes_json`, `device` / `cga_device` / `liha_device`.
- Path constants live in `fluent_pipeline/config.py` (`READY_TO_IMPORT_DIR`, `SHARED_TEMP_DIR`, `SHARED_BUILD_DIR`, `LOGS_DIR`, caches).

## Persistence

- SQL catalog index (fluentcoder `catalog.py`) , install-scoped labware/workspaces/liquid_classes metadata.
- Portable JSON under project context / package: `liquid_classes.json`, `labware_catalog.json`, etc. (`ready-to-import/` gitignored).
- Shared caches: `ready-to-import/_shared/temp_files/cache/catalog`, `.../zeia-references`.
- Project-reader multi-ZEIA index default: `ready-to-import/_shared/temp_files/build/tecan_project_index.sqlite`.

## Error model

- Python application services own `authoring_status` (`status`, findings, artifacts, allowed action, next action, live handoff actions); CLI and MCP do not derive competing states.
- `RenderError` on missing DeviceAlias/AvailableID or alias→AvailableID cross-fill (`Instrument=` in AvailableID).
- xlqc parse: `ValueError` when XML exceeds `max_xml_bytes`.
- Catalog miss → empty GUID string (no invent); generation.yaml guid only if site-configured.
- CLI artifact outputs must stay under `ready-to-import/<project>/temp_files/` (`cli/runtime.py`).

## Events / telemetry

- Workflow event JSONL under `ready-to-import/_shared/temp_files/logs/`.
- No product telemetry without opt-in + docs (vault policy).

## ZEIA fail-closed

Shared rule across renderer device bindings, liquid-class GUID resolution, and simulator hardwareProfile: prefer mined ZEIA evidence; refuse silent product invents.

## Packaging

- Public write owner: `exports.py` (`export_ready_to_import` / publish helpers).
- Internal ZEIA writers: Fluent DLL archive writer, portable archive writer, env-gated full ZEIA copy.
- `delivery_bundle.py` validates V2 folders; it does not publish.
- Bundle setup BAT writes `error_logs_*`, `tecan_method_source`, and setup logs under the bundle's own `temp_files/`.
