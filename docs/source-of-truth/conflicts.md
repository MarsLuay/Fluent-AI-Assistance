# Open conflicts and duplicates

Last updated: 2026-08-09

| ID | Kind | Severity | Symbols / paths | Evidence | Canonical owner | Status |
| --- | --- | --- | --- | --- | --- | --- |
| D001 | dupe | warn | `load_xlqc` vs `parse_xlqc` | Both parse `.xlqc`; former metadata-only for SQL index; latter params/`liquid_classes.json` v2. Docstrings name the seam. | Keep both; seam = catalog metadata vs portable params | accepted-dual |
| C001 | conflict | block | `resolve_liquid_class_guid` vs renderer inline JSON fallback | Fixed: `liquid_class_guid_from_catalog_entries` mirrors export matching | `liquid_class_guid_from_catalog_entries` / `resolve_liquid_class_guid` | fixed |
| D002 | dupe | warn | `_find`/`_local_name`/`_text` in `liquid_classes_export.py` vs `xcmp._find`/`_local`/`_text` | Same nav shape; **`_text` contracts differ**: pipeline returns `""`, xcmp returns `None` when empty. Cross-package (fluent_pipeline vs fluentcoder). Unifying would risk empty-vs-None bugs or leak private xcmp imports. | Keep both; seam = pipeline export nav vs fluentcoder catalog nav | accepted-dual |
| D003 | dupe | warn | `strip_fluent_instance_suffix` | Consolidated into `fluent_naming.strip_fluent_instance_suffix` (+ `INSTANCE_SUFFIX_RE`). | `fluent_pipeline/fluent_naming.py` | fixed |
| D004 | dupe | warn | `resolve_worktable_datastore` / `discover_worktable_datastore` | Shared helper covering Worktable dir + Components/Connectors. | `fluent_pipeline/worktable_datastore.py` | fixed |
| D005 | dupe | warn | `normalize_operator_prompt_text` / `prompt_has_media_boilerplate` | Owner is `policies/prompt_text.py`. Compat facades remain. | `policies/prompt_text.py` (+ compat facade) | fixed |
| D006 | dupe | warn | `is_user_prompt_command` | Disambiguated by input kind. | Distinct names by input kind | fixed |
| D007 | dupe | warn | `runtime_error_for_validate_failure` | Shared `api_v2/validate_runtime.runtime_error_for_validate_failure(..., kind=…)`. | `api_v2/validate_runtime.py` | fixed |
| D008 | dupe | warn | `validation.py` `_gate_*` vs `gates/` package | Fixed: all 27 active evaluators are package-owned and registry-addressable; `validation.py` retains orchestration and compatibility wrappers only. | `fluent_pipeline/gates/` | fixed |
| D009 | dupe | warn | `generation_workflow.py` vs `workflows/generation/` | Fixed: canonical orchestration lives in `workflows/generation/workflow.py`; the historical module is a thin compatibility facade. | `fluent_pipeline/workflows/generation/` | fixed |
| C002 | conflict | warn | AGENTS.md fake CLI verbs vs `parser.py` | Docs claimed `video-to-gif` / `normalize-worktable-gif` / `minimal-edit-diff` CLI commands that are not in parser. | Library APIs + `process-media`; docs updated 2026-08-01 | fixed |
| D010 | dupe | warn | Flat `source/tools/*.py` vs nested subpackages | Reorganized into `simulator/`, `api_v2/`, `connectors/`, `registry/`, `prompt/`, `common/`. Legacy `from tecan_tools import <mod>` kept via `__getattr__`. | Nested subpackages + `__getattr__` shims | fixed |

## Notes

- LC GUID cascade (SQL → JSON → site yaml) is intentional, not a conflict.
- DeviceAlias/AvailableID fail-closed is single-owner in `renderer.py` (+ DropFingers gate in api_v2).
- Packaging public owner is `exports.py`; three internal ZEIA writers are deliberate backends (Fluent DLL / portable / env-gated full copy).
- Request spec and protocol IR are versioned **dicts**, not dataclasses named `RequestSpec` / `ProtocolIR`.
- Deliberate duals still open as **accepted-dual**: D001, D002.
- No open **block** conflicts.
- 2026-08-01: `scripts/` and `source/tools/` (+ simulator `scripts/`) reorganized into topic subfolders; docs/entrypoints updated. No new block conflicts.
