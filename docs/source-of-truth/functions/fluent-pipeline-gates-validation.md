# Functions: fluent-pipeline-gates-validation

Source roots: `fluent_pipeline/` (10 files)

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `evaluate_zeia_parsed` | `gates/archive.py` | `(context)` | Check the imported source manifest or supplied source ZEIA archives. | see source |
| `evaluate_protocol_ir_schema` | `gates/ir.py` | `(context)` | Validate canonical IR and ZEIA-derived labware label/catalog contracts. | see source |
| `a200_adapter_catalog_issues` | `gates/ir.py` | `(ir, preferred_label_catalogs)` | Compatibility wrapper around ZEIA preferred label/catalog checks. | see source |
| `ValidationContext` | `gates/models.py` | class | Artifacts computed once and supplied to registered readiness evaluators.  Keep this context data-onl | , |
| `RegisteredGateEvaluator` | `gates/registry.py` | class | One statically registered evaluator and its reviewed artifact contract. | , |
| `readiness_evaluator_registry` | `gates/registry.py` | `()` | see source | see source |
| `readiness_evaluator` | `gates/registry.py` | `(gate_id)` | see source | see source |
| `evaluate_tip_boxes` | `gates/worktable.py` | `(context)` | see source | see source |
| `evaluate_carriers` | `gates/worktable.py` | `(context)` | see source | see source |
| `evaluate_device_aliases` | `gates/worktable.py` | `(context)` | see source | see source |
| `evaluate_deck_layout` | `gates/worktable.py` | `(context)` | see source | see source |
| `_evaluate_worktable_resource (priv)` | `gates/worktable.py` | `(context, gate_id, label_plural, detail_key, items)` | Match the worktable patch severity model for comparable resources. | see source |
| `ReadinessGateStatus` | `readiness.py` | class | Canonical readiness statuses for required gate evaluation. | , |
| `normalize_readiness_gate_status` | `readiness.py` | `(value)` | Return the canonical readiness status for a gate value, if known. | see source |
| `coerce_readiness_gate_status` | `readiness.py` | `(value)` | Normalize known statuses and preserve unknown ones as fail-closed strings. | see source |
| `normalize_readiness_gate_policy` | `readiness.py` | `(value)` | Normalize a selected readiness policy into canonical gate statuses. | see source |
| `readiness_policy_name` | `readiness.py` | `(value)` | Return a stable name for a normalized readiness policy. | see source |
| `readiness_policy_statuses` | `readiness.py` | `(value)` | Return the sorted canonical statuses accepted by a readiness policy. | see source |
| `gate_status_in_policy` | `readiness.py` | `(value, policy)` | Return whether a gate status is allowed by the selected policy. | see source |
| `build_canonical_readiness` | `readiness.py` | `()` | see source | see source |
| `readiness_status_from_readiness` | `readiness.py` | `(readiness)` | see source | see source |
| `embed_readiness` | `readiness.py` | `(payload)` | see source | see source |
| `_read_registry_payload (priv)` | `readiness_gates.py` | `()` | see source | see source |
| `ReadinessGateDefinition` | `readiness_gates.py` | class | class | , |
| `ReadinessGateDefinition.display_label` | `readiness_gates.py` | `()` | see source | see source |
| `ReadinessGateDefinition.gate_label` | `readiness_gates.py` | `()` | see source | see source |
| `ReadinessGateDefinition.classification_label` | `readiness_gates.py` | `()` | see source | see source |
| `ReadinessGateDefinition.is_required_offline_gate` | `readiness_gates.py` | `()` | see source | see source |
| `ReadinessGateDefinition.is_optional_diagnostic` | `readiness_gates.py` | `()` | see source | see source |
| `ReadinessGateDefinition.approval_context_key` | `readiness_gates.py` | `()` | see source | see source |
| `ReadinessGateDefinition.capability_record` | `readiness_gates.py` | `()` | see source | see source |
| `ReadinessGateDefinition.typescript_record` | `readiness_gates.py` | `()` | see source | see source |
| `readiness_gate_registry_version` | `readiness_gates.py` | `()` | see source | see source |
| `readiness_gates` | `readiness_gates.py` | `()` | see source | see source |
| `readiness_gate` | `readiness_gates.py` | `(gate_id)` | see source | see source |
| `registered_readiness_gate_evaluators` | `readiness_gates.py` | `()` | Return statically registered evaluators after checking registry parity.  This deliberately imports a | see source |
| `readiness_gate_request_spec_paths` | `readiness_gates.py` | `(gate_id)` | see source | see source |
| `readiness_gate_approval_context_keys` | `readiness_gates.py` | `(gate_id)` | see source | see source |
| `readiness_gate_request_spec_approved` | `readiness_gates.py` | `(request_spec, gate_id)` | see source | see source |
| `active_validation_gate_tuples` | `readiness_gates.py` | `()` | see source | see source |
| `required_offline_gate_count` | `readiness_gates.py` | `()` | see source | see source |
| `optional_diagnostic_gate_count` | `readiness_gates.py` | `()` | see source | see source |
| `render_readiness_gate_registry_markdown` | `readiness_gates.py` | `()` | see source | see source |
| `render_readiness_gate_registry_typescript` | `readiness_gates.py` | `()` | see source | see source |
| `LintFinding` | `spec_lint.py` | class | A single linter finding with severity, message, and location path. | , |
| `LintResult` | `spec_lint.py` | class | Structured result of linting a request spec. | , |
| `LintResult.errors` | `spec_lint.py` | `()` | see source | see source |
| `LintResult.warnings` | `spec_lint.py` | `()` | see source | see source |
| `LintResult.ok` | `spec_lint.py` | `()` | True when there are no error-level findings. | see source |
| `LintResult.add` | `spec_lint.py` | `(severity, location, message)` | see source | see source |
| `lint_request_spec_file` | `spec_lint.py` | `(path)` | Load a request spec from disk (without normalizing) and lint it.  Linting works on the RAW spec so u | see source |
| `_parse_spec_text (priv)` | `spec_lint.py` | `(text, suffix)` | see source | see source |
| `lint_request_spec` | `spec_lint.py` | `(spec)` | Lint an in-memory (raw) request spec mapping and return findings. | see source |
| `_lint_recipe_step (priv)` | `spec_lint.py` | `(step, location, result)` | Lint one recipe step. Returns True if it would emit a body IR step. | see source |
| `render_lint_report` | `spec_lint.py` | `(result)` | Render a human-readable report grouped by severity with a summary. | see source |
| `validate_ready_to_import` | `validation.py` | `()` | Run the required gates before a bundle can be copied to ready-to-import. | see source |
| `scaffold_validation_report` | `validation.py` | `(reason)` | Report emitted when ready validation cannot run (scaffold / no compiled XSCR).  A scaffold is explic | see source |
| `render_validation_markdown` | `validation.py` | `(report)` | see source | see source |
| `validation_failure_message` | `validation.py` | `(report)` | see source | see source |
| `_is_required_offline_gate (priv)` | `validation.py` | `(gate)` | see source | see source |
| `_gate_requires_review (priv)` | `validation.py` | `(gate)` | see source | see source |
| `_gate_zeia (priv)` | `validation.py` | `(source_manifest, source_projects)` | Compatibility facade for the registered source-archive evaluator. | see source |
| `_gate_ir_schema (priv)` | `validation.py` | `(ir, error)` | Compatibility facade for the registered protocol-IR evaluator. | see source |
| `_a200_adapter_catalog_issues (priv)` | `validation.py` | `(ir, preferred_label_catalogs)` | Compatibility facade for ZEIA preferred label/catalog checks. | see source |
| `_gate_worktable_resource (priv)` | `validation.py` | `(gate_id, label_plural, detail_key, items, diff_present)` | Legacy helper retained for direct test and monkeypatch compatibility. | see source |
| `_gate_tip_boxes (priv)` | `validation.py` | `(diff)` | Compatibility facade for the registered tip-box evaluator. | see source |
| `_gate_carriers (priv)` | `validation.py` | `(diff)` | Compatibility facade for the registered carrier evaluator. | see source |
| `_gate_device_aliases (priv)` | `validation.py` | `(diff)` | Compatibility facade for the registered device-alias evaluator. | see source |
| `_gate_deck_layout (priv)` | `validation.py` | `(diff, context)` | Compatibility facade for the registered deck-layout evaluator. | see source |
| `_evaluate_registered_gate (priv)` | `validation.py` | `(gate_id, context)` | Run a registered evaluator, preserving test monkeypatch facades.  Production evaluation uses the typ | see source |
| `_gate_checksums (priv)` | `validation.py` | `(compiled_xscr, source_projects, context)` | Verify the generated ZEIA will carry valid FluentControl checksums.  FluentControl validates ``<Chec | see source |
| `_gate_generated_zeia (priv)` | `validation.py` | `(source_projects, context)` | Validate the packaged generated ZEIA as a one-file import artifact.  The other gates check the IR an | see source |
| `_gate_subroutine_additions (priv)` | `validation.py` | `(source_projects, context)` | Validate the datastore metadata of subroutines ADDED to the generated ZEIA.  Replacing an existing s | see source |
| `_gate_subroutine_calls_resolve (priv)` | `validation.py` | `(protocol_ir, source_manifest, compiled_xscr, context, source_projects)` | Backward-compatible wrapper for the legacy gate helper name/signature. | see source |
| `_gate_command_inventory (priv)` | `validation.py` | `(compiled_inventory, source_manifest, context)` | Validate the literal name strings the compiled XSCR command XML uses.  The earlier gates resolve lab | see source |
| `_manifest_name_inventory (priv)` | `validation.py` | `(source_manifest, alias_maps)` | Build the per-category set of names the source context actually provides. | see source |
| `_load_or_derive_ir (priv)` | `validation.py` | `(protocol_ir, draft_path)` | see source | see source |
| `_canonical_first_instance_label (priv)` | `validation.py` | `(value)` | Treat FluentControl's optional ``[001]`` suffix as the first base instance. | see source |
| `_normalized_number (priv)` | `validation.py` | `(value)` | see source | see source |
| `_parse_well (priv)` | `validation.py` | `(value)` | see source | see source |
