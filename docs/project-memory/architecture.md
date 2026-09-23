# Architecture

Fluent AI-Assistance is local tooling for Tecan FluentControl workflows:
project inspection, protocol generation, validation, ready-to-import packaging,
and a simulator/tooling surface.

```text
AI client and CLI
       |
MCP adapter and direct CLI
       |
fluent_pipeline Python API
       |
FluentCoder + integration + media + validation + packaging
```

The CLI, MCP adapter, and direct Python API share `fluent_pipeline` services.
The primary protocol-builder entrypoints are the CLI runtime,
`mcp_server.py`, bootstrap, application services, generation workflow, and
exports. The sibling packages provide shared Tecan helpers, project reading,
worklist building, simulator assets, and repository tools.

Canonical repository layers:

- `source/00-shared/`: shared helpers and command registry.
- `source/01-project-reader/`: ZEIA, XSCR, GWL, and pattern inspection. Its versioned canonical project model owns structural ZEIA detection, normalization, provenance, unknown-field retention, and worklist entities; protocol-builder context ingestion consumes those records and keeps snapshot evidence separate.
- `source/02-worklist-builder/`: structured worklist generation.
- `source/03-protocol-builder/`: protocol pipeline, CLI, MCP, validation, and packaging.
- `source/04-protocol-simulator/`: TypeScript/Vite simulator.
- `source/tools/`: simulator, API, connector, registry, prompt, and common tooling.
- `ready-to-import/`: generated local handoff bundles and scratch outputs.

Worklist semantics are shared across the worklist builder and protocol IR. The
typed GWL layer preserves raw TipMask spelling while exposing conservative
one-hot record-tip validation; the Load/Execute Worklist contract retains
source-backed fields and additive values; and the opt-in analyzer/optimizer
refuses to cross barriers or reorder units whose liquid/state dependencies are
not proven. Offline grouping is an inspectable scheduling opportunity, not a
FluentControl timing or hardware-readiness guarantee.

The generation flow is evidence-first: imported project data and shared
registries supply contracts, then request specs and protocol IR feed rendering,
validation, and packaging. Generated outputs and host-derived assets are not
canonical source files for hand edits.

The provider-neutral generation-context boundary is
`tecan.generation_context.v1`. It turns a validated request/spec into
deterministic task facets and carries only explicitly included evidence with a
selection reason, source provenance, source fingerprint, omissions, and
accounting. Unknown intent remains reviewable; this contract does not infer
capabilities or dump whole ZEIA/XSCR sources. Pattern ranking, budget
compaction, and one-shot workflow integration build on this boundary in later
pipeline layers.

Pattern selection is exposed as `tecan.generation_pattern_selection.v1` over
compact mined windows. It scores exact operation-family and source-evidence
matches, keeps ranked alternatives and omissions, preserves explicit pattern
or source-script overrides, and leaves ties, incompatible targets, and
low-confidence candidates in review rather than silently guessing.

Physical hardware readiness is owned by
`source/03-protocol-builder/fluent_pipeline/physical_readiness.py`. It
normalizes source/provenance-backed head, tip, labware, site, software,
configuration, device, and capability evidence; derives deterministic
interaction and verification fingerprints; and keeps required real-instrument
checks separate from offline readiness. Ready-to-import bundles persist the
machine-readable physical verification report, while FluentCoder simulation
reports the mechanical effects it cannot prove. Neither offline validation nor
simulation certifies physical safety or hardware-run approval.

Offline full-export end-to-end coverage lives in
`source/03-protocol-builder/tests/test_full_export_e2e.py` plus
`tests/full_export_e2e/feature_coverage_manifest.json`. The suite materializes
a synthetic complete ZEIA from an immutable recipe, walks import through
ready-to-import packaging, and fails CI when a live CLI/MCP/stage surface has
no mapped test. Passing it does not mean Script Editor or hardware readiness.

Cross-variant ZEIA compatibility is contract-tested by the fixture matrix at
`source/01-project-reader/tests/fixtures/zeia_compatibility/`. Its manifest
maps supported structural adapters to deterministic detection, canonical
normalization/index goldens, negative neighbors, malformed/dependency cases,
and a multi-archive folder scenario. Matrix recipes are sanitized synthetic
schema evidence only; they do not claim a FluentControl release.
