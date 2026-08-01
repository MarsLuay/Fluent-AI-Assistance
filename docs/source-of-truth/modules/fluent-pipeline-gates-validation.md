# Module: fluent-pipeline-gates-validation

**Paths:** `source/03-protocol-builder/fluent_pipeline/` → 10 files
**Purpose:** Readiness gates, IR/worktable/archive gates, validation and spec lint.
**Public surface:** see [functions/fluent-pipeline-gates-validation.md](../functions/fluent-pipeline-gates-validation.md)
**Depends on:** fluentcoder / shared tecan libs (varies by file)
**Invariants:** ZEIA-mined evidence over baked product law; fail closed when bindings missing (see cross-cutting).
**Related functions:** [functions/fluent-pipeline-gates-validation.md](../functions/fluent-pipeline-gates-validation.md)

## Files

- `gates/__init__.py`
- `gates/archive.py`
- `gates/ir.py`
- `gates/models.py`
- `gates/registry.py`
- `gates/worktable.py`
- `readiness.py`
- `readiness_gates.py`
- `spec_lint.py`
- `validation.py`
