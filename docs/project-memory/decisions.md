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
