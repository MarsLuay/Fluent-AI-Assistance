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
- `source/01-project-reader/`: ZEIA, XSCR, GWL, TWL, Scheduler, and pattern inspection. Its versioned canonical project model owns structural ZEIA detection, normalization, provenance, unknown-field retention, distinct worklist/task-input entities, Scheduler process/task entities, and ambiguous Base Worktable identity; protocol-builder context ingestion consumes those records and keeps snapshot evidence separate.
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

Subroutine execution modes are centralized as the verified set
`Synchronous`, `Asynchronous`, `JoinSubroutine`, and `FireAndForget`. Authored
API values are strict, while decompiled future values remain source-preserved.
Subroutine targets are classified only from explicit evidence: static paths,
explicit expression mappings, or unresolved values; dynamic names are never
inferred. The offline simulator reports deterministic launch/join/detach epochs;
joins consume matching asynchronous invocations and never launch fresh copies,
while unknown or unmatched lifecycle states remain reviewable rather than being
executed by assumption.

FluentControl expression semantics use the versioned
`tecan.expression_symbols.v1` catalog under FluentCoder's reference assets.
Catalog entries retain canonical spelling, signatures, provenance, contexts, and
version scope; semantic validation, imported-expression inventory, generation
policy, and symbol introspection consume the shared catalog services rather than
maintaining adapter-specific function lists. FluentControl editor colors and
unverified forum terminology are discovery-only and never semantic categories.

Target-aware deployment planning is owned by the shared
`application_services.plan_deployment` service and the
`tecan.deployment_plan.v1` builder. The CLI is a thin adapter over that service;
explicit source/target profiles, same-target versus cross-target mode, target
fingerprints, drift invalidation, and unknown-target blocking therefore have
one logic owner. Planning is artifact/profile-level and non-mutating: runtime
state and internal database/SVN metadata remain diagnostic evidence, never
portable deployment actions.

Canonical Protocol IR v2 exposes the source-backed low-level FluentControl motion
sequence as `move_axis_command`, `start_move_command`, and
`wait_for_async_response`. XSCR ingestion preserves typed execution fields,
ordering, source metadata, and additive XML through generated FluentCoder Python;
the simulator boundary contract is regenerated from the same Python schema.
These operations remain structural/offline validation only and do not prove
physical motion, calibration, alignment, clearance, or collision safety. MCA
pickup XSCR ingestion likewise keeps source-backed coordinates, offsets,
orientation, and tip-position expressions typed through renderer and
Decompiler round-trips; additive fields outside that contract remain
source-preserved rather than inferred. Source AddLabware/SetLocation rotation
is retained as placement context for MCA pickup validation and simulation.
Only a proven zero placement rotation resolves the existing authored address
contract; non-zero or unavailable transforms remain `cannot_determine` rather
than applying forum-observed 180-degree behavior. The simulator reports only
modeled logical selection and keeps physical readiness unverified. The
simulator's authoring command
catalog is the schema-backed
`source/04-protocol-simulator/src/data/controlBar.ts` catalog; editor toolbox
and restored-command handling validate operations against the generated
`PROTOCOL_IR_OPERATIONS` contract and reject or diagnose unknown commands. The
shared command registry maps the three verified motion statement IDs to those
canonical operations and typed field aliases; unsupported variants remain
unmapped and low-level non-typed driver commands remain passthrough.

The provider-neutral generation-context boundary is
`tecan.generation_context.v1`. It turns a validated request/spec into
deterministic task facets and carries only explicitly included evidence with a
selection reason, source provenance, source fingerprint, omissions, and
accounting. Unknown intent remains reviewable; this contract does not infer
capabilities or dump whole ZEIA/XSCR sources. Pattern ranking, budget
compaction, and one-shot workflow integration build on this boundary in later
pipeline layers. Its deterministic compaction keeps critical structured
evidence atomic, records duplicate and budget omissions, and exposes byte,
character, and estimated-token accounting. Repair-delta contexts further narrow
diagnostics to implicated lineage, contracts, and explicitly safe actions.

Pattern selection is exposed as `tecan.generation_pattern_selection.v1` over
compact mined windows. It scores exact operation-family and source-evidence
matches, keeps ranked alternatives and omissions, preserves explicit pattern
or source-script overrides, and leaves ties, incompatible targets, and
low-confidence candidates in review rather than silently guessing.

Generation context is assembled by the shared generation workflow after project
inspection and before IR planning. It ranks source scripts and mined pattern
windows against request facets, preserves explicit selections, and emits a
bounded JSON context pack plus a concise Markdown review artifact. The pack's
fingerprint and review status are carried into protocol IR, the generation
manifest, normalized artifact hashes, and ready-to-import report companions;
CLI, MCP, and one-shot callers therefore consume the same selection behavior.
Automatic selection is ready only when source-backed evidence is sufficient;
missing or ambiguous evidence remains an actionable review diagnostic.

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
