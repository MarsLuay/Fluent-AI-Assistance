# Functions: fluent-pipeline-generation

Source roots: `fluent_pipeline/` (14 files)

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `GenerationResult` | `application_services.py` | class | class | , |
| `GenerationResult.to_dict` | `application_services.py` | `()` | see source | see source |
| `ProjectImportRequest` | `application_services.py` | class | class | , |
| `ProjectImportResult` | `application_services.py` | class | class | , |
| `ProjectImportResult.to_dict` | `application_services.py` | `()` | see source | see source |
| `ProjectInspectionRequest` | `application_services.py` | class | class | , |
| `ProjectInspectionResult` | `application_services.py` | class | class | , |
| `ProjectInspectionResult.to_dict` | `application_services.py` | `()` | see source | see source |
| `RequestSpecCreateRequest` | `application_services.py` | class | class | , |
| `RequestSpecCreateResult` | `application_services.py` | class | class | , |
| `RequestSpecCreateResult.to_dict` | `application_services.py` | `()` | see source | see source |
| `RequestSpecValidationRequest` | `application_services.py` | class | class | , |
| `RequestSpecValidationResult` | `application_services.py` | class | class | , |
| `RequestSpecValidationResult.to_dict` | `application_services.py` | `()` | see source | see source |
| `RepairPlanRequest` | `application_services.py` | class | class | , |
| `RepairPlanResult` | `application_services.py` | class | class | , |
| `RepairPlanResult.to_dict` | `application_services.py` | `()` | see source | see source |
| `RepairApplyRequest` | `application_services.py` | class | class | , |
| `RepairApplyResult` | `application_services.py` | class | class | , |
| `RepairApplyResult.to_dict` | `application_services.py` | `()` | see source | see source |
| `BundleVerificationRequest` | `application_services.py` | class | class | , |
| `BundleVerificationResult` | `application_services.py` | class | class | , |
| `BundleVerificationResult.to_dict` | `application_services.py` | `()` | see source | see source |
| `LogAnalysisRequest` | `application_services.py` | class | class | , |
| `LogAnalysisResult` | `application_services.py` | class | class | , |
| `LogAnalysisResult.to_dict` | `application_services.py` | `()` | see source | see source |
| `generate_protocol` | `application_services.py` | `(request)` | Run the complete generation workflow as one top-level operation. | see source |
| `import_project` | `application_services.py` | `(request)` | Import a ZEIA project context and optionally activate it. | see source |
| `inspect_project` | `application_services.py` | `(request)` | Load one imported project and surface its stable inspection artifacts. | see source |
| `create_request_spec` | `application_services.py` | `(request)` | Create and persist a request specification through the shared application layer. | see source |
| `validate_request_spec` | `application_services.py` | `(request)` | Lint a request specification through the shared application layer. | see source |
| `plan_repair` | `application_services.py` | `(request)` | Build a repair plan for a generated draft. | see source |
| `apply_repair` | `application_services.py` | `(request)` | Plan and apply repairs through one shared service path. | see source |
| `verify_bundle` | `application_services.py` | `(request)` | Validate a ready-to-import bundle through the shared application layer. | see source |
| `analyze_logs` | `application_services.py` | `(request)` | Parse one FluentControl log or scan the latest recent logs. | see source |
| `_load_project_context (priv)` | `application_services.py` | `(context_name)` | see source | see source |
| `GenerationOptions` | `generation_options.py` | class | class | , |
| `GenerationOptions.as_dict` | `generation_options.py` | `()` | Return durable request-spec generation options as a mapping. | see source |
| `GenerationOptions.runtime_dict` | `generation_options.py` | `()` | Return adapter-only runtime options excluded from request specs. | see source |
| `normalize_generation_options` | `generation_options.py` | `(options)` | see source | see source |
| `generation_options_from_request_spec` | `generation_options.py` | `(spec)` | see source | see source |
| `generation_options_from_cli_args` | `generation_options.py` | `(args)` | see source | see source |
| `_normalize_verification_prompt_rup (priv)` | `generation_options.py` | `(value)` | see source | see source |
| `fluent_version_requires_worktable_images` | `generation_options.py` | `(value)` | see source | see source |
| `ApprovalSet` | `generation_workflow.py` | class | class | , |
| `GenerationRequest` | `generation_workflow.py` | class | class | , |
| `_load_generation_context (priv)` | `generation_workflow.py` | `(context_name)` | see source | see source |
| `run_generation_workflow` | `generation_workflow.py` | `(request)` | Run or scaffold the official generation workflow. | see source |
| `inspect_generation_context` | `generation_workflow.py` | `(context, selected_scripts)` | see source | see source |
| `build_seed_protocol_ir` | `generation_workflow.py` | `()` | see source | see source |
| `render_generation_plan` | `generation_workflow.py` | `(intent, context, selection, stages)` | see source | see source |
| `render_rga_move_policy_markdown` | `generation_workflow.py` | `(policy)` | see source | see source |
| `render_context_inspection_markdown` | `generation_workflow.py` | `(inspection)` | see source | see source |
| `_build_readiness_profile (priv)` | `generation_workflow.py` | `()` | see source | see source |
| `render_generation_summary` | `generation_workflow.py` | `(manifest)` | see source | see source |
| `_normalized_artifact_hash (priv)` | `generation_workflow.py` | `(path, roots)` | see source | see source |
| `_load_manifest_dict (priv)` | `generation_workflow.py` | `(raw_path)` | see source | see source |
| `_context_with_request_sources (priv)` | `generation_workflow.py` | `(context, spec)` | Use every request-declared project as one generation context.  Request specs often use a small edite | see source |
| `_refresh_request_spec_full_zeia_export (priv)` | `generation_workflow.py` | `(spec, assessment)` | see source | see source |
| `_verify_full_zeia_export (priv)` | `generation_workflow.py` | `(context)` | see source | see source |
| `_write_blocked_full_zeia_manifest (priv)` | `generation_workflow.py` | `()` | see source | see source |
| `_render_full_zeia_export_markdown (priv)` | `generation_workflow.py` | `(assessment)` | see source | see source |
| `_normalize_compiled_variable_declaration_namespaces (priv)` | `generation_workflow.py` | `(xscr_path)` | Keep DataContract ``xsi:type`` prefixes in scope after XML reserialization.  ElementTree preserves n | see source |
| `_copy_source_workspace_data (priv)` | `generation_workflow.py` | `(xscr_path, source_scripts)` | Preserve native workspace-delta metadata for RUP Worktable prompts. | see source |
| `_required_subroutine_references (priv)` | `generation_workflow.py` | `(ir)` | see source | see source |
| `_first_payload_data_index (priv)` | `generation_workflow.py` | `(payload)` | see source | see source |
| `_stamp_approved_automated_verification_moves (priv)` | `generation_workflow.py` | `(ir, request_spec_doc)` | Keep baseline/preserved move_plate steps automated when the spec approves it. | see source |
| `_clear_verification_script_protocol_comment (priv)` | `generation_workflow.py` | `(ir)` | Do not publish the generation prompt as the FluentControl script comment. | see source |
| `_primary_context_source_projects (priv)` | `generation_workflow.py` | `(context)` | Return the ZEIA archive(s) that should be used as generated-output bases.  A project collection can  | see source |
| `_generation_ir_source_mode (priv)` | `generation_workflow.py` | `()` | Select the sole step source using the documented strict precedence. | see source |
| `_annotate_explicit_recipe_prompt_media (priv)` | `generation_workflow.py` | `(ir)` | Attach recipe prompt media slots before IR export, draft render, and compile. | see source |
| `_attach_regeneration_baseline_context (priv)` | `generation_workflow.py` | `(ir)` | Retain baseline provenance without implicitly replacing requested steps. | see source |
| `_matching_regeneration_baseline_script (priv)` | `generation_workflow.py` | `(context, protocol_name)` | Return the primary-project script matching the requested protocol identity. | see source |
| `_default_worktable (priv)` | `generation_workflow.py` | `(context, selected_scripts)` | Resolve the protocol worktable from script refs / recipe, not archive order.  Preference order: 1. R | see source |
| `finalize_media_slot_files` | `generation_workflow.py` | `(ir, media_dir)` | Convert dropped videos and normalize Worktable GIF slots in-place. | see source |
| `build_ir_from_recipe` | `generation_workflow.py` | `(recipe)` | Synthesize a verification-recipe protocol IR. | see source |
| `_normalize_ir_labware_labels_against_manifest (priv)` | `generation_workflow.py` | `(ir, manifest)` | Align synthesized dynamic labware labels with labels proven by context.  Some source scripts use var | see source |
| `_rewrite_ir_labware_labels (priv)` | `generation_workflow.py` | `(value, rewrites, parent_key)` | see source | see source |
| `_attach_source_move_patterns (priv)` | `generation_workflow.py` | `(ir, selected_source_records)` | Back verification moves with matching commands from ungenerated XSCRs. | see source |
| `_recipe_set_variable_payload (priv)` | `generation_workflow.py` | `(data)` | see source | see source |
| `_normalize_toggle_simulation_values (priv)` | `generation_workflow.py` | `(values, category_conditions)` | see source | see source |
| `_normalize_fluent_condition_expression (priv)` | `generation_workflow.py` | `(condition)` | see source | see source |
| `_add_labware_location_requires_raw_xml (priv)` | `generation_workflow.py` | `(location)` | see source | see source |
| `_render_add_labware_raw_xml (priv)` | `generation_workflow.py` | `(params)` | see source | see source |
| `synthesize_seed_ir` | `ir_planner.py` | `(ir)` | Populate a seed IR with ordered steps and inventory where possible.  The supplied ``ir`` is mutated  | see source |
| `_mark_synthesis_review_required (priv)` | `ir_planner.py` | `(ir)` | see source | see source |
| `_load_selected_source_ir (priv)` | `ir_planner.py` | `(script, warnings)` | see source | see source |
| `_build_planned_step (priv)` | `ir_planner.py` | `(operation, window, step, fields, source_path, warnings)` | see source | see source |
| `_normalize_fields (priv)` | `ir_planner.py` | `(value)` | see source | see source |
| `CommandRecord` | `minimal_edit.py` | class | class | , |
| `CommandRecord.to_dict` | `minimal_edit.py` | `()` | see source | see source |
| `compare_xscr_minimal_edit` | `minimal_edit.py` | `(original, edited)` | Compare two XSCR scripts and flag unapproved statement-level drift. | see source |
| `extract_xscr_command_records` | `minimal_edit.py` | `(path)` | see source | see source |
| `render_minimal_edit_markdown` | `minimal_edit.py` | `(report)` | see source | see source |
| `_normalize_text (priv)` | `minimal_edit.py` | `(value)` | see source | see source |
| `write_minimal_edit_reports` | `minimal_edit.py` | `(report, json_path, markdown_path)` | see source | see source |
| `RepairAction` | `repair.py` | class | class | , |
| `RepairAction.to_dict` | `repair.py` | `()` | see source | see source |
| `RepairEdit` | `repair.py` | class | class | , |
| `RepairEdit.to_dict` | `repair.py` | `()` | see source | see source |
| `RepairPlan` | `repair.py` | class | class | , |
| `RepairPlan.to_dict` | `repair.py` | `()` | see source | see source |
| `PythonSourceIndex` | `repair.py` | class | class | , |
| `RepairApplicationError` | `repair.py` | class | Raised when a structured repair no longer matches the recorded span. | , |
| `build_repair_plan` | `repair.py` | `(draft_path)` | see source | see source |
| `applicable_repair_actions` | `repair.py` | `(plan)` | Return the repairs that should be applied under the current approval mode. | see source |
| `_build_repair_edit (priv)` | `repair.py` | `(index)` | see source | see source |
| `apply_repair_plan` | `repair.py` | `(plan, output_path)` | see source | see source |
| `render_repair_markdown` | `repair.py` | `(plan)` | see source | see source |
| `_parse_number (priv)` | `repair.py` | `(value)` | see source | see source |
| `_load_simulation_json (priv)` | `repair.py` | `(path)` | see source | see source |
| `_ensure_fluentcoder_import (priv)` | `repair.py` | `(source, symbol)` | see source | see source |
| `build_approval_set_from_options` | `request_factory.py` | `(options)` | Build generation approvals from normalized generation options. | see source |
| `merge_generation_options_from_spec` | `request_factory.py` | `(spec, overrides)` | Return request-spec generation options with adapter overrides applied. | see source |
| `build_generation_request` | `request_factory.py` | `()` | Build a GenerationRequest from already-parsed adapter inputs. | see source |
| `build_generation_request_from_spec` | `request_factory.py` | `(spec_source)` | Build a GenerationRequest from a request.spec.yaml payload. | see source |
| `build_request_spec_create_request` | `request_factory.py` | `()` | Build a RequestSpecCreateRequest from adapter-normalized fields. | see source |
| `is_meta_verification_group_comment` | `request_spec.py` | `(text)` | see source | see source |
| `recipe_group_description` | `request_spec.py` | `(group)` | see source | see source |
| `recipe_step_type` | `request_spec.py` | `(raw_step)` | see source | see source |
| `recipe_step_produces_ir` | `request_spec.py` | `(raw_step)` | see source | see source |
| `recipe_subroutine_name` | `request_spec.py` | `(raw_step)` | see source | see source |
| `build_request_spec` | `request_spec.py` | `()` | Create a durable user-request contract for a generation run. | see source |
| `load_request_spec` | `request_spec.py` | `(path)` | Load and normalize a request spec from YAML or JSON. | see source |
| `write_request_spec` | `request_spec.py` | `(spec, path)` | Write a request spec as YAML or JSON. | see source |
| `normalize_request_spec` | `request_spec.py` | `(spec)` | Fill defaults and accept a minimal shorthand shape. | see source |
| `_normalize_verification_recipe (priv)` | `request_spec.py` | `(recipe)` | Light normalization for the declarative verification recipe. | see source |
| `recipe_worktable_patterns` | `request_spec.py` | `(recipe)` | Named worktable labware bindings copied from mined source patterns. | see source |
| `resolve_recipe_worktable_binding` | `request_spec.py` | `(raw_step)` | Resolve an optional worktable binding for a recipe prompt step. | see source |
| `extract_intent_checks` | `request_spec.py` | `(verbatim_prompt)` | Best-effort list of discrete requested checks from a free-text request. | see source |
| `verification_recipe` | `request_spec.py` | `(spec)` | Return the verification recipe if the spec declares usable groups. | see source |
| `_normalize_recipe_prompt_text (priv)` | `request_spec.py` | `(text)` | Normalize recipe prompt chrome only; keep ZEIA/operator/recipe wording.  Strips step numbers and han | see source |
| `_strip_hands_clear_trailer (priv)` | `request_spec.py` | `(prompt)` | Remove hands-clear / press-OK trailers only; never append Continue. | see source |
| `_normalize_pre_movement_prompt_text (priv)` | `request_spec.py` | `(prompt)` | Rewrite Next: movement prompts. Does not append Continue (caller does). | see source |
| `request_spec_generation_defaults` | `request_spec.py` | `(spec)` | Return generation CLI defaults carried by a request spec. | see source |
| `build_request_validation_diff` | `request_spec.py` | `()` | Compare the reviewed request contract with generated workflow outputs. | see source |
| `render_request_validation_diff_markdown` | `request_spec.py` | `(diff)` | Render a compact Markdown review artifact. | see source |
| `request_verbatim_prompt` | `request_spec.py` | `(spec)` | Return the exact user prompt recorded by the request spec. | see source |
| `_check_full_zeia_export (priv)` | `request_spec.py` | `(source)` | see source | see source |
| `_dump_simple_yaml (priv)` | `request_spec.py` | `(payload)` | Write the request-spec subset without requiring PyYAML. | see source |
| `_load_simple_yaml (priv)` | `request_spec.py` | `(text)` | see source | see source |
| `_parse_simple_yaml_scalar (priv)` | `request_spec.py` | `(raw_value)` | see source | see source |
| `RequestSpecCandidate` | `request_spec_resolver.py` | class | class | , |
| `normalize_protocol_stem` | `request_spec_resolver.py` | `(value)` | Normalize protocol/bundle labels to a comparable lowercase stem. | see source |
| `split_version_suffix` | `request_spec_resolver.py` | `(label)` | see source | see source |
| `is_latest_alias` | `request_spec_resolver.py` | `(value)` | see source | see source |
| `parse_latest_alias` | `request_spec_resolver.py` | `(value)` | Return an optional stem from ``latest`` or ``latest:<stem>`` aliases. | see source |
| `enumerate_request_spec_candidates` | `request_spec_resolver.py` | `()` | Collect request specs from delivery folders and per-project temp files. | see source |
| `resolve_latest_request_spec` | `request_spec_resolver.py` | `()` | Return the newest matching request.spec.yaml, if any. | see source |
| `bundle_dir_for_request_spec` | `request_spec_resolver.py` | `(spec_path)` | Return the ready-to-import bundle root when ``spec_path`` lives under one. | see source |
| `resolve_request_spec_path` | `request_spec_resolver.py` | `(spec)` | Resolve a CLI ``--spec`` value to a concrete request.spec.yaml path.  Returns ``(path, info)`` where | see source |
| `enumerate_ready_bundle_dirs_for_stem` | `request_spec_resolver.py` | `(stem)` | Return ready-to-import bundle roots for a protocol stem, highest version first. | see source |
| `ready_to_import_script_names` | `request_spec_resolver.py` | `(ready_to_import_dir)` | Collect FluentControl script names recorded in ready-to-import bundle metadata. | see source |
| `GenerationStage` | `workflows/generation/runner.py` | class | One ordered, synchronous generation workflow stage. | , |
| `GenerationStage.run` | `workflows/generation/runner.py` | `(state)` | Mutate shared state or raise without starting concurrent work. | see source |
| `GenerationStageRunner` | `workflows/generation/runner.py` | class | Run stages in declaration order against exactly one shared state object. | , |
| `GenerationStageRunner.run` | `workflows/generation/runner.py` | `(state)` | see source | see source |
| `LoadContextStage` | `workflows/generation/stages.py` | class | Load the requested project context with the legacy progress contract. | , |
| `LoadContextStage.run` | `workflows/generation/stages.py` | `(state)` | see source | see source |
| `GenerationState` | `workflows/generation/state.py` | class | Mutable state passed through one ordered generation-stage sequence. | , |
