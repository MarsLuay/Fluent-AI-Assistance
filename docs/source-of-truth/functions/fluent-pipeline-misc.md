# Functions: fluent-pipeline-misc

Source roots: `fluent_pipeline/` (4 files)

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `repair_powershell_pipelines` | `bundle_setup.py` | `(setup_bat)` | Remove cmd escape carets that become literal tokens inside PowerShell commands. | see source |
| `setup_bat_findings` | `bundle_setup.py` | `(setup_bat)` | Return blocking diagnostics-script defects caught before handoff. | see source |
| `TubeFixingCase` | `tube_fixing_harness.py` | class | One bounded diagnostic case.  ``source_status`` deliberately distinguishes source-backed values from | , |
| `TubeFixingHarnessError` | `tube_fixing_harness.py` | class | Raised when a source-native test harness cannot be built safely. | , |
| `tube_fixing_case_matrix` | `tube_fixing_harness.py` | `()` | Return the finite, bounded 2 x 2 test matrix for companion artifacts. | see source |
| `build_tube_fixing_xscr` | `tube_fixing_harness.py` | `(source_bundle, output_xscr)` | Build and validate a standalone native-only cap diagnostic XSCR.  ``source_bundle`` is an imported f | see source |
| `validate_tube_fixing_xscr` | `tube_fixing_harness.py` | `(xscr_path)` | Validate the standalone/native-only invariants of a generated harness. | see source |
| `render_tube_fixing_harness_markdown` | `tube_fixing_harness.py` | `(validation)` | Render concise operator-facing matrix and structural validation notes. | see source |
| `resolve_tube_fixing_entries` | `tube_fixing_harness.py` | `(source_bundle)` | Resolve main/cap/fingers XSCR entry paths inside a ZEIA.  Prefer explicit paths. Otherwise discover  | see source |
| `_read_required_entries (priv)` | `tube_fixing_harness.py` | `(source_bundle, required)` | see source | see source |
| `_parse_xscr (priv)` | `tube_fixing_harness.py` | `(payload, label)` | see source | see source |
| `_build_root (priv)` | `tube_fixing_harness.py` | `()` | see source | see source |
| `_replace_payload_references (priv)` | `tube_fixing_harness.py` | `(payload, main_payload)` | see source | see source |
