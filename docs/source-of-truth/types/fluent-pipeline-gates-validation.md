# Types: fluent-pipeline-gates-validation

| Symbol | File | Notes |
| --- | --- | --- |
| `LintFinding` | `spec_lint.py` | A single linter finding with severity, message, and location path. |
| `LintResult` | `spec_lint.py` | Structured result of linting a request spec. |
| `ReadinessGateDefinition` | `readiness_gates.py` | class |
| `ReadinessGateStatus` | `readiness.py` | Canonical readiness statuses for required gate evaluation. |
| `RegisteredGateEvaluator` | `gates/registry.py` | One statically registered evaluator and its reviewed artifact contract. |
| `ValidationContext` | `gates/models.py` | Artifacts computed once and supplied to registered readiness evaluators.  Keep this context data-onl |
