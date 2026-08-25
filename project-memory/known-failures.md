# Known failures and limits

- Offline `ready-to-import` validation does not prove FluentControl Script
  Editor load-clean status or hardware readiness. Review in Script Editor and
  validate on the target instrument before execution.
- Generation is intentionally blocked until the project has been imported and
  inspected through the documented bootstrap flow.
- Missing ZEIA device bindings, catalog evidence, or other required source
  contracts cause fail-closed behavior instead of guessed parameters.
- MCP does not install FluentControl drivers, write to instrument-side
  `ProgramData`, automate the FluentControl UI, or perform hardware motion.
- Generated handoff artifacts and runtime scratch can be stale or incomplete;
  the source pipeline and its checks remain authoritative.
