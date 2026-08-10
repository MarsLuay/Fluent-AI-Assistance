# Module: fluent-pipeline-generation

**Paths:** `source/03-protocol-builder/fluent_pipeline/` → 16 files
**Purpose:** Generation workflow, shared authoring status, request specs, IR planning, repair, and application service facades.
**Public surface:** see [functions/fluent-pipeline-generation.md](../functions/fluent-pipeline-generation.md)
**Depends on:** fluentcoder / shared tecan libs (varies by file)
**Invariants:** ZEIA-mined evidence over baked product law; fail closed when bindings are missing; Python derives one authoring/recovery status that CLI and MCP consume unchanged.
**Related functions:** [functions/fluent-pipeline-generation.md](../functions/fluent-pipeline-generation.md)
**Related types:** [types/fluent-pipeline-generation.md](../types/fluent-pipeline-generation.md)

## Files

- `application_services.py`
- `authoring_status.py`
- `generation_options.py`
- `generation_workflow.py`
- `ir_planner.py`
- `minimal_edit.py`
- `repair.py`
- `request_factory.py`
- `request_spec.py`
- `request_spec_resolver.py`
- `workflows/__init__.py`
- `workflows/generation/__init__.py`
- `workflows/generation/runner.py`
- `workflows/generation/stages.py`
- `workflows/generation/state.py`
- `workflows/generation/workflow.py`
