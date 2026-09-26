# Decisions

- Keep CLI, MCP, and direct Python access on the same `fluent_pipeline`
  services so behavior and safety checks are shared.
- Treat the MCP server as a local stdio adapter; it does not open a network
  listener or provide arbitrary shell execution.
- Require imported-project evidence and shared registries before generation;
  fail closed when device bindings or catalog evidence are missing.
- Keep generated `ready-to-import` bundles and generated simulator/host assets
  out of hand-edited source fixes; change the generator or source pipeline.
- Preserve lab-agnostic defaults. Names and contracts must come from the
  user-provided project evidence, not invented examples or fixture data.
- Keep shallow `tecan.driver_macros.v1` as capability inventory. Persist
  per-usage DriverFramework parameter contracts (ExecutionSettings,
  `~Variable~` closure, companions, fingerprints) in the adjacent
  `tecan.driver_command_contracts.v1` artifact so incompatible same-macro
  usages are not collapsed. Generation selects an exact source-backed
  contract or fails deterministically on ambiguity; never rewrite
  `~Variable~` tokens to host absolute paths.
- Keep full-export readiness owned by the project-reader canonical model. The
  protocol-builder context, generation, validation, diagnostics, and one-shot
  consumers use its deterministic result rather than maintaining independent
  completeness heuristics; partial-export approval remains an explicit policy
  decision separate from the factual readiness classification.
- Keep the normal full-export-plus-request path owned by the shared
  `application_services.run_one_shot()` service. CLI adapters only normalize
  paths, render progress/results, and return its stage-specific status; import,
  readiness, request-spec validation, generation, simulation, compilation, and
  packaging remain the existing workflow's responsibility.
- Keep `tecan.generation_context.v1` provider/model agnostic and deterministic:
  validated request facets preserve unknown intent as reviewable, while every
  included evidence item carries a selection reason, source provenance, and
  stable fingerprint. Later ranking, budget, and workflow children consume
  this contract rather than widening it with implicit capabilities or raw
  ZEIA/XSCR dumps.
- Keep generation-context compaction deterministic and structure-preserving:
  critical source-backed evidence remains atomic, duplicate windows retain
  candidate metadata, budget omissions and byte/character/estimated-token
  accounting remain explicit, and repair-delta contexts allow only implicated
  diagnostics, lineage/contracts, and explicitly safe actions.
- Keep source-pattern selection deterministic and auditable: exact operation
  families and verified compatibility evidence outrank incidental text matches;
  explicit IDs, queries, and source scripts remain overrides; ties,
  incompatibility, and low confidence stay visible as review/omission records.
- Keep generation context selection in the shared generation workflow rather
  than duplicating it in CLI or MCP adapters. Use source-backed pattern evidence
  as sufficient evidence when it is the strongest available match, preserve
  manual selections, and persist the bounded context JSON/Markdown, selection
  diagnostics, fingerprint, and normalized hashes through IR and packaged
  reports.
- Keep external-device health as a conservative consumer of the source-backed
  `tecan.driver_command_contracts.v1` and `tecan.host_environment.v1` records:
  command roles, documented DriverFramework states, and license capability
  evidence require explicit provenance. #154 owns recovery policy, #155 owns
  per-command contracts, and #156 owns host/environment evidence; unknown
  evidence remains reviewable rather than becoming an inferred command,
  timeout, keepalive, or root-cause claim.
- Keep FluentControl motion compatibility as a separate, source-backed report:
  vendor issues 120382 and 120985 come from the Tecan revision-history registry,
  version/build evidence comes from #156, typed MCA384 arm moves come from
  canonical protocol IR, and PathFinder/RGA contouring signals come from
  structured logs. Missing version or trigger evidence remains unknown or
  trigger-not-present; motion analysis does not model geometry, physical
  readiness (#160), direct arm workarounds, or pipetting root cause.
- Keep generation and packaging target datastore identity explicit: an explicit
  `tecan.target_datastore.v1` profile is the only source for target-dependent
  script GUID rewrites and prerequisite checks; without one, package as
  target-unbound, consult no build-host datastore, and record the binding status
  and fingerprint in the handoff metadata and reports.
