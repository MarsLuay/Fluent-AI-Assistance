# Readiness Gate Registry

Generated from `fluent_pipeline/data/readiness_gate_registry.json`. Do not edit by hand.

## Active Summary

- Required offline ready-to-import gates: `26`
- Optional diagnostics: `1`
- Total active entries: `27`
- Stable IDs are the contract; gate numbers are display labels only.

`Gate 27` is an optional FluentControl import/load diagnostic. It is not required for offline `ready_to_import` status; use it when a live provider is configured or manually open the generated artifact in Script Editor instead.

## Active Gates

| Gate | ID | Classification | Description | Implementation | Review Policy | Approval Key | CLI Flag | MCP Capability | Request Spec Path | Remediation | Artifact Inputs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Gate 1 | `zeia_parsed` | Required offline gate | Source ZEIA manifests or source archives are readable and free of import errors. | `fluent_pipeline.gates.archive:evaluate_zeia_parsed` |  |  |  |  |  |  | `source manifest, source project archives` |
| Gate 2 | `protocol_ir_schema` | Required offline gate | The canonical protocol IR loads and passes schema validation. | `fluent_pipeline.gates.ir:evaluate_protocol_ir_schema` |  |  |  |  |  |  | `protocol.ir.json, Python draft` |
| Gate 3 | `labware_resolves` | Required offline gate | Requested labware names resolve in the source context or approved aliases. |  |  |  |  |  |  |  |  |
| Gate 4 | `liquid_classes_resolve` | Required offline gate | Requested liquid classes resolve in the source context or approved aliases. |  |  |  |  |  |  |  |  |
| Gate 5 | `worklist_paths_valid` | Required offline gate | Any emitted worklist paths are present and valid for handoff. |  |  |  |  |  |  |  |  |
| Gate 6 | `python_draft_generated` | Required offline gate | The generated Python draft exists and defines the expected worktable builder. |  |  |  |  |  |  |  |  |
| Gate 7 | `simulation_passes` | Required offline gate | The offline simulator reports a passing result. |  |  |  |  |  |  |  |  |
| Gate 8 | `repair_plan_clear` | Required offline gate | The repair plan has no unresolved critical findings. |  |  |  |  |  |  |  |  |
| Gate 9 | `xscr_compiles` | Required offline gate | The compiled XSCR exists and the compile stage did not fail. |  |  |  |  |  |  |  |  |
| Gate 10 | `recreate_matches_ir` | Required offline gate | The recreate guide matches the canonical protocol IR structure. |  |  |  |  |  |  |  |  |
| Gate 11 | `post_compile_xscr_reinspect` | Required offline gate | The compiled XSCR parses back into usable canonical data without FluentControl findings. |  |  |  |  |  |  |  |  |
| Gate 12 | `xscr_ir_roundtrip_matches` | Required offline gate | The compiled XSCR preserves the expected IR structure and operation ordering. |  |  |  |  |  |  |  |  |
| Gate 13 | `volume_bounds_valid` | Required offline gate | Liquid-handling volumes remain inside the configured instrument limits. |  |  |  |  |  |  |  |  |
| Gate 14 | `well_ranges_valid` | Required offline gate | Explicit well references stay within the addressed labware geometry. |  |  |  |  |  |  |  |  |
| Gate 15 | `tip_capacity_valid` | Required offline gate | Selected tips can support the requested aspirate and dispense volumes. |  |  |  |  |  |  |  |  |
| Gate 16 | `liquid_class_compatible` | Required offline gate | Selected liquid classes are compatible with the operations they are used for. |  |  |  |  |  |  |  |  |
| Gate 17 | `no_unapproved_raw_xml` | Required offline gate | Unsupported raw XML does not ship unless it was explicitly reviewed and approved. |  |  |  |  |  |  |  |  |
| Gate 18 | `liquid_state_valid` | Required offline gate | The liquid-state model stays internally consistent for the generated protocol. |  |  |  |  |  |  |  |  |
| Gate 19 | `tip_boxes_resolve` | Required offline gate | Required tip boxes resolve in the source context or approved aliases. | `fluent_pipeline.gates.worktable:evaluate_tip_boxes` |  |  |  |  |  |  | `protocol.ir.json, source manifest` |
| Gate 20 | `carriers_resolve` | Required offline gate | Required carriers resolve in the source context or approved aliases. | `fluent_pipeline.gates.worktable:evaluate_carriers` |  |  |  |  |  |  | `protocol.ir.json, source manifest` |
| Gate 21 | `device_aliases_resolve` | Required offline gate | Required device aliases resolve in the source context or approved aliases. | `fluent_pipeline.gates.worktable:evaluate_device_aliases` |  |  |  |  |  |  | `protocol.ir.json, source manifest` |
| Gate 22 | `deck_layout_consistent` | Required offline gate | Deck positions stay consistent with the source worktable unless the change was explicitly approved. | `fluent_pipeline.gates.worktable:evaluate_deck_layout` | blocking | `deck_layout_changes` | `--approve-deck-layout` | `approve_deck_layout` | `review.deck_layout` | `protocol-builder worktable-diff` | `protocol.ir.json, source manifest` |
| Gate 23 | `checksums_valid` | Required offline gate | Edited ZEIA entries carry checksums FluentControl can accept during import. |  |  |  |  |  |  |  |  |
| Gate 24 | `generated_zeia_valid` | Required offline gate | The packaged generated ZEIA is readable, consistent, and packaged with the expected datastore metadata. |  |  |  |  |  |  |  |  |
| Gate 25 | `command_inventory_resolves` | Required offline gate | Literal compiled command names resolve in the source context or approved aliases. |  |  |  |  |  |  |  |  |
| Gate 26 | `subroutine_dependencies_valid` | Required offline gate | Subroutine calls resolve cleanly and package the expected Script dependencies. |  |  |  |  |  |  |  |  |
| Gate 27 | `fluent_context_check` | Optional diagnostic | Optional live diagnostic that imports or opens the generated artifact in FluentControl or Script Editor. It is not required for offline ready-to-import status. |  |  |  |  |  |  |  |  |
