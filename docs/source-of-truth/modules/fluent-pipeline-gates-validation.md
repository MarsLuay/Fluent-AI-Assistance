# Module: fluent-pipeline-gates-validation

**Paths:** `source/03-protocol-builder/fluent_pipeline/` → 11 files
**Purpose:** Readiness gates, IR/worktable/archive gates, validation and spec lint.
**Public surface:** see [functions/fluent-pipeline-gates-validation.md](../functions/fluent-pipeline-gates-validation.md)
**Depends on:** fluentcoder / shared tecan libs (varies by file)
**Invariants:** All 27 active gate implementations are package-owned and registry-addressable; `validation.py` orchestrates and keeps compatibility wrappers only.
**Related functions:** [functions/fluent-pipeline-gates-validation.md](../functions/fluent-pipeline-gates-validation.md)

## Files

- `gates/__init__.py`
- `gates/archive.py`
- `gates/evaluators.py`
- `gates/ir.py`
- `gates/models.py`
- `gates/registry.py`
- `gates/worktable.py`
- `readiness.py`
- `readiness_gates.py`
- `spec_lint.py`
- `validation.py`
