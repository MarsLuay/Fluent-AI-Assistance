# Functions: fluent-pipeline-protocol-ir

Source roots: `fluent_pipeline/` (8 files)

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `validate_command_registry_name_provenance` | `command_registry.py` | `(payload)` | Validate FluentControl name provenance metadata for registry v2 payloads. | see source |
| `load_command_registry_with_provenance` | `command_registry.py` | `()` | Load the shared registry and enforce v2 FluentControl name provenance. | see source |
| `lookup_command_by_operation` | `command_registry.py` | `(operation)` | Return the default registry entry for a canonical protocol IR operation. | see source |
| `registry_fluentcontrol_name` | `command_registry.py` | `(command_name)` | Return the FluentControl command label shown in the UI, when known. | see source |
| `registry_fluentcontrol_name_for_operation` | `command_registry.py` | `(operation)` | Return the default FluentControl UI command label for an IR operation. | see source |
| `registry_fluentcontrol_name_source` | `command_registry.py` | `(command_name)` | Return how the registry fluentcontrol_name was sourced. | see source |
| `registry_fluentcontrol_name_confidence` | `command_registry.py` | `(command_name)` | Return confidence for the registry fluentcontrol_name. | see source |
| `registry_fluentcontrol_name_metadata` | `command_registry.py` | `(command_name)` | Return fluentcontrol_name plus provenance metadata when known. | see source |
| `apply_rga_move_pattern_policy` | `ir/rga_move_policy.py` | `(ir)` | Require physical RGA/gripper moves to come from mined source windows. | see source |
| `convert_unsafe_rga_adapter_moves_to_prompts` | `ir/rga_move_policy.py` | `(ir)` | Backward-compatible wrapper for the stricter RGA source-pattern policy. | see source |
| `WellState` | `liquid_state.py` | class | class | , |
| `validate_liquid_state` | `liquid_state.py` | `(ir)` | Validate liquid volumes, well capacity, dead volume, and tip carryover. | see source |
| `render_liquid_state_markdown` | `liquid_state.py` | `(report)` | Render a compact liquid-state validation report. | see source |
| `liquid_state_failure_message` | `liquid_state.py` | `(report)` | see source | see source |
| `_resolve_volume_ul (priv)` | `liquid_state.py` | `(value, ir)` | see source | see source |
| `_parse_well (priv)` | `liquid_state.py` | `(value)` | see source | see source |
| `protocol_ir_from_path` | `protocol_ir.py` | `(path)` | Load a Python draft, XSCR, GWL, or ZEIA archive into canonical IR. | see source |
| `protocol_ir_from_python` | `protocol_ir.py` | `(path)` | see source | see source |
| `protocol_ir_from_xscr` | `protocol_ir.py` | `(path)` | see source | see source |
| `_xscr_nested_leaf_supported (priv)` | `protocol_ir.py` | `(command_object)` | True when a nested XSCR Object can become a protocol-IR step.  ``ApplicationDriverMacro`` / ``Legacy | see source |
| `_xscr_leaf_command_objects (priv)` | `protocol_ir.py` | `(statements)` | Yield executable XSCR commands inside conditional/default wrappers. | see source |
| `_root_script_groups (priv)` | `protocol_ir.py` | `(root, parent_by_child)` | Yield native root ScriptGroup containers in document order. | see source |
| `protocol_ir_from_gwl` | `protocol_ir.py` | `(path)` | see source | see source |
| `protocol_ir_bundle_from_zeia` | `protocol_ir.py` | `(path)` | see source | see source |
| `_protocols_from_imported_project_fallback (priv)` | `protocol_ir.py` | `(path)` | see source | see source |
| `load_protocol_ir` | `protocol_ir.py` | `(path)` | see source | see source |
| `write_protocol_ir` | `protocol_ir.py` | `(ir, path)` | see source | see source |
| `load_ir_payload` | `protocol_ir.py` | `(path)` | see source | see source |
| `write_ir_payload` | `protocol_ir.py` | `(payload, path)` | see source | see source |
| `render_python_draft` | `protocol_ir.py` | `(ir)` | Render a canonical protocol IR as a fluentcoder Python draft. | see source |
| `_render_python_draft_from_validated_ir (priv)` | `protocol_ir.py` | `(ir)` | Render an already validated canonical protocol IR. | see source |
| `_declared_variables_for_render (priv)` | `protocol_ir.py` | `(ir)` | Collect FluentControl variables that must exist before generated commands use them. | see source |
| `_expression_imports_for_render (priv)` | `protocol_ir.py` | `(steps, variables)` | see source | see source |
| `_collect_expression_imports (priv)` | `protocol_ir.py` | `(expression, imports)` | see source | see source |
| `render_gwl` | `protocol_ir.py` | `(ir)` | Render simple aspirate/dispense steps as a Gemini WorkList draft. | see source |
| `render_recreate_markdown` | `protocol_ir.py` | `(ir)` | see source | see source |
| `is_ir_bundle` | `protocol_ir.py` | `(payload)` | see source | see source |
| `protocol_filename` | `protocol_ir.py` | `(ir, suffix)` | see source | see source |
| `_render_python_step (priv)` | `protocol_ir.py` | `(step, labware_vars)` | see source | see source |
| `render_execute_application_xml` | `protocol_ir.py` | `(params)` | Synthesize an ExecuteApplicationStatement XML object. | see source |
| `render_execute_vb_script_xml` | `protocol_ir.py` | `(params)` | Synthesize an ExecuteVbScriptStatement XML object. | see source |
| `_rup_allowed_value_text (priv)` | `protocol_ir.py` | `(value)` | Return a FluentControl RUP AllowedValues item.  RUPVariableStatement ``AllowedValues`` is a semicolo | see source |
| `render_rup_variable_statement_xml` | `protocol_ir.py` | `(params)` | Synthesize an RUPVariableStatement XML object. | see source |
| `is_setup_group_name` | `protocol_ir_compat.py` | `(name)` | Return true for group names that should collapse into one setup group. | see source |
| `canonical_setup_group_name_for_steps` | `protocol_ir_compat.py` | `(steps)` | Use one stable setup group name for generated FluentControl scripts. | see source |
| `normalize_setup_groups` | `protocol_ir_compat.py` | `(ir)` | Collapse setup-ish IR step groups into one Setup group. | see source |
| `normalize_group_hierarchy` | `protocol_ir_compat.py` | `(ir)` | Preserve a stable group hierarchy hook for legacy callers. | see source |
| `normalize_runtime_variable_prompt_instructions` | `protocol_ir_compat.py` | `(ir)` | Normalize runtime variable prompt instructions to concise operator text. | see source |
| `protocol_is_prompt_only` | `protocol_ir_compat.py` | `(ir)` | True when the protocol has steps but performs no liquid handling. | see source |
| `write_placeholder_image_slot` | `protocol_ir_compat.py` | `(dest)` | Write a valid dummy PNG to ``dest``. | see source |
| `media_slot_filename` | `protocol_ir_compat.py` | `(slot, kind)` | see source | see source |
| `media_slot_relative_path` | `protocol_ir_compat.py` | `(slot, kind)` | see source | see source |
| `prompt_has_media_boilerplate` | `protocol_ir_compat.py` | `(text)` | see source | see source |
| `normalize_operator_prompt_text` | `protocol_ir_compat.py` | `(text)` | see source | see source |
| `prompt_looks_like_external_initialization_check` | `protocol_ir_compat.py` | `(text)` | see source | see source |
| `resolve_verification_prompt_rup_kind` | `protocol_ir_compat.py` | `(params)` | see source | see source |
| `prompt_step_is_deck_presence_check` | `protocol_ir_compat.py` | `(params)` | see source | see source |
| `annotate_verification_prompts_with_media` | `protocol_ir_compat.py` | `(ir)` | see source | see source |
| `force_worktable_prompt_images` | `protocol_ir_compat.py` | `(ir)` | Route every image-bearing prompt through RUP Worktable.  RUPWorktableStatement has no sound field. W | see source |
| `route_unbound_worktable_prompts_to_standard` | `protocol_ir_compat.py` | `(ir)` | Route legacy unbound Worktable prompts to the Standard prompt renderer.  A RUPWorktableStatement is  | see source |
| `sync_verification_prompt_target_labware` | `protocol_ir_compat.py` | `(ir)` | see source | see source |
| `sanitize_worktable_prompt_variable_labware_bindings` | `protocol_ir_compat.py` | `(ir)` | see source | see source |
| `apply_default_verification_worktable_bindings` | `protocol_ir_compat.py` | `(ir)` | see source | see source |
| `collect_media_placeholders` | `protocol_ir_compat.py` | `(ir)` | see source | see source |
| `prompt_image_media_slots` | `protocol_ir_compat.py` | `(ir)` | see source | see source |
| `prompt_step_image_path` | `protocol_ir_compat.py` | `(params)` | see source | see source |
| `prompt_step_media_path` | `protocol_ir_compat.py` | `(params)` | see source | see source |
| `prompt_step_worktable_media_path` | `protocol_ir_compat.py` | `(params)` | see source | see source |
| `prompt_step_worktable_binding` | `protocol_ir_compat.py` | `(params)` | see source | see source |
| `media_slot_specs` | `protocol_ir_compat.py` | `(ir)` | see source | see source |
| `sound_path_specs_from_ir` | `protocol_ir_compat.py` | `(ir)` | see source | see source |
| `deployed_media_path` | `protocol_ir_compat.py` | `(touchtools_dir, filename)` | see source | see source |
| `worktable_pipeline_video_slots` | `protocol_ir_compat.py` | `(specs)` | see source | see source |
| `required_media_slot_specs` | `protocol_ir_compat.py` | `(specs)` | see source | see source |
| `build_media_path_map_from_specs` | `protocol_ir_compat.py` | `(specs, touchtools_dir)` | see source | see source |
| `build_media_path_map` | `protocol_ir_compat.py` | `(ir, touchtools_dir)` | see source | see source |
| `build_media_path_map_from_placeholder_rows` | `protocol_ir_compat.py` | `(rows, touchtools_dir)` | see source | see source |
| `resolve_touchtools_images_dir` | `protocol_ir_compat.py` | `()` | see source | see source |
| `touchtools_media_subfolder` | `protocol_ir_compat.py` | `(script_name)` | Per-script folder under TouchTools Images. | see source |
| `resolve_touchtools_media_subfolder` | `protocol_ir_compat.py` | `(ir)` | Derive the TouchTools deploy subfolder for one protocol IR. | see source |
| `render_media_path_map_markdown` | `protocol_ir_compat.py` | `(path_map)` | see source | see source |
| `apply_media_path_map_to_xscr` | `protocol_ir_compat.py` | `(xscr_path, path_map)` | Rewrite bundle-relative prompt media paths to deployed TouchTools absolutes. | see source |
| `rewrite_flat_touchtools_media_paths_in_xscr` | `protocol_ir_compat.py` | `(xscr_path)` | Insert a per-script subfolder into already-absolute TouchTools media paths. | see source |
| `apply_touchtools_media_path_map_to_xscr` | `protocol_ir_compat.py` | `(xscr_path, path_map)` | Rewrite bundle-relative and flat absolute prompt media paths in one XSCR. | see source |
| `apply_deployed_touchtools_media_paths` | `protocol_ir_compat.py` | `(xscr_path, ir)` | Rewrite bundle-relative prompt media paths to deployed TouchTools absolutes. | see source |
| `Operation` | `protocol_ir_schema.py` | class | class | , |
| `ProtocolIRIssue` | `protocol_ir_schema.py` | class | class | , |
| `ProtocolIRIssue.as_dict` | `protocol_ir_schema.py` | `()` | see source | see source |
| `ProtocolIRValidationError` | `protocol_ir_schema.py` | class | Raised when protocol IR fails schema validation. | , |
| `ProtocolIRSchemaInfo` | `protocol_ir_schema.py` | class | Public metadata for one registered protocol IR schema version. | , |
| `ProtocolIRSchemaInfo.as_dict` | `protocol_ir_schema.py` | `()` | see source | see source |
| `OperationSpec` | `protocol_ir_schema.py` | class | Typed operation contract used by validation, JSON Schema, and docs. | , |
| `OperationSpec.as_dict` | `protocol_ir_schema.py` | `()` | see source | see source |
| `protocol_ir_json_schema` | `protocol_ir_schema.py` | `(version)` | Return the JSON Schema for a protocol IR version. | see source |
| `protocol_ir_bundle_json_schema` | `protocol_ir_schema.py` | `()` | Return the JSON Schema for a protocol IR bundle document. | see source |
| `protocol_ir_schema_versions` | `protocol_ir_schema.py` | `()` | Return registered protocol IR schema versions. | see source |
| `operation_specs` | `protocol_ir_schema.py` | `()` | Return the operation enum with required field contracts. | see source |
| `protocol_ir_schema_markdown` | `protocol_ir_schema.py` | `(version)` | Render concise schema documentation for humans and Codex. | see source |
| `register_protocol_ir_migration` | `protocol_ir_schema.py` | `(from_version, to_version, migrator)` | Register one directed migration edge and optional schema for its target. | see source |
| `protocol_ir_migration_path` | `protocol_ir_schema.py` | `(from_version, to_version)` | Return the registered migration path from one version to another. | see source |
| `validate_protocol_ir_document` | `protocol_ir_schema.py` | `(payload)` | Validate a single protocol IR as written, optionally after migration. | see source |
| `validate_protocol_ir_bundle_document` | `protocol_ir_schema.py` | `(payload)` | Validate a bundle as written, optionally after migrating contained protocols. | see source |
| `migrate_protocol_ir` | `protocol_ir_schema.py` | `(payload)` | Migrate and normalize a protocol IR payload to the requested version. | see source |
| `migrate_protocol_ir_bundle` | `protocol_ir_schema.py` | `(payload)` | Normalize a ZEIA-derived bundle and migrate every contained protocol. | see source |
| `validate_protocol_ir` | `protocol_ir_schema.py` | `(payload)` | Return schema issues for a normalized protocol IR payload. | see source |
| `_validate_step_assignment_target (priv)` | `protocol_ir_schema.py` | `(issues, path)` | see source | see source |
| `_validate_loop_count_expression (priv)` | `protocol_ir_schema.py` | `(issues, path, value)` | see source | see source |
| `_validate_v2_scalar_expression_consistency (priv)` | `protocol_ir_schema.py` | `(issues, payload)` | see source | see source |
| `_validate_v2_site_expression_alias_consistency (priv)` | `protocol_ir_schema.py` | `(issues, payload)` | see source | see source |
| `_validate_site_expression_alias_container (priv)` | `protocol_ir_schema.py` | `(issues, path, value)` | see source | see source |
| `_validate_expression_projection (priv)` | `protocol_ir_schema.py` | `(issues, path, container, legacy_key, expression_key, expression_factory)` | see source | see source |
| `_validate_expression_projection_list (priv)` | `protocol_ir_schema.py` | `(issues, path, container, legacy_key, expression_key, expression_factory)` | see source | see source |
| `_validate_cross_container_expression_projection (priv)` | `protocol_ir_schema.py` | `(issues, path)` | see source | see source |
| `_validate_protocol_ir_v1 (priv)` | `protocol_ir_schema.py` | `(payload)` | see source | see source |
| `assert_valid_protocol_ir` | `protocol_ir_schema.py` | `(payload)` | see source | see source |
| `validate_protocol_ir_bundle` | `protocol_ir_schema.py` | `(payload)` | see source | see source |
| `_assert_registered_future_payload (priv)` | `protocol_ir_schema.py` | `(payload, version)` | see source | see source |
| `_assert_v1_payload (priv)` | `protocol_ir_schema.py` | `(payload)` | see source | see source |
| `_normalize_v1 (priv)` | `protocol_ir_schema.py` | `(payload)` | see source | see source |
| `_normalize_v2 (priv)` | `protocol_ir_schema.py` | `(payload)` | see source | see source |
| `_normalize_rup_allowed_values (priv)` | `protocol_ir_schema.py` | `(value)` | see source | see source |
| `_normalize_rup_allowed_values_in_xml (priv)` | `protocol_ir_schema.py` | `(xml)` | see source | see source |
| `_normalize_rup_allowed_value_item (priv)` | `protocol_ir_schema.py` | `(value)` | see source | see source |
| `_normalize_step (priv)` | `protocol_ir_schema.py` | `(raw, index)` | see source | see source |
| `normalize_operation` | `protocol_ir_schema.py` | `(value)` | see source | see source |
| `operation_name` | `protocol_ir_schema.py` | `(operation)` | see source | see source |
| `_validate_steps (priv)` | `protocol_ir_schema.py` | `(issues, steps)` | see source | see source |
| `_validate_named_items (priv)` | `protocol_ir_schema.py` | `(issues, items, path)` | see source | see source |
| `_normalize_named_item (priv)` | `protocol_ir_schema.py` | `(raw, required_key)` | see source | see source |
| `_normalize_dependency (priv)` | `protocol_ir_schema.py` | `(raw)` | see source | see source |
| `_normalize_variable (priv)` | `protocol_ir_schema.py` | `(raw)` | see source | see source |
| `_try_parse_expression_mapping (priv)` | `protocol_ir_schema.py` | `(text)` | see source | see source |
| `_validate_expression (priv)` | `protocol_ir_schema.py` | `(issues, path, value)` | see source | see source |
| `_validate_expression_reference_list (priv)` | `protocol_ir_schema.py` | `(issues, path, value)` | see source | see source |
| `_validate_expression_with_semantics (priv)` | `protocol_ir_schema.py` | `(issues, path, value)` | see source | see source |
| `_require_text (priv)` | `protocol_ir_schema.py` | `(issues, path, value)` | see source | see source |
| `_validate_fc_variable_name (priv)` | `protocol_ir_schema.py` | `(issues, path, name)` | see source | see source |
| `_validate_add_labware_parameters (priv)` | `protocol_ir_schema.py` | `(issues, path, params)` | see source | see source |
| `_build_protocol_ir_v2_schema (priv)` | `protocol_ir_schema.py` | `()` | see source | see source |
| `worktable_name_from_ir` | `worktable_ir.py` | `(ir)` | see source | see source |
| `worktable_guid_from_ir` | `worktable_ir.py` | `(ir)` | see source | see source |
| `initialization_worktable_from_spec` | `worktable_ir.py` | `(spec)` | see source | see source |
| `execution_steps_from_report` | `worktable_ir.py` | `(report)` | see source | see source |
