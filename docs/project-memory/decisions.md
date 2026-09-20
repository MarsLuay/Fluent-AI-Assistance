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
