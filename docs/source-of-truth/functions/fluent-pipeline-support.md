# Functions: fluent-pipeline-support

Source roots: `fluent_pipeline/` (33 files)

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `load_alias_maps` | `aliases.py` | `(alias_dir)` | Load configured alias maps from YAML files. | see source |
| `alias_records` | `aliases.py` | `(alias_maps)` | Return aliases as stable, CLI-friendly records. | see source |
| `resolve_alias` | `aliases.py` | `(value, kind, alias_maps)` | Resolve one alias value for a named alias kind.  For labware/catalog kinds, Fluent instance suffixes | see source |
| `merge_alias_maps` | `aliases.py` | `()` | Merge alias map dicts; later maps override earlier keys. | see source |
| `load_alias_maps_with_context_catalog` | `aliases.py` | `(alias_dir)` | Load shipped alias YAML, then overlay ZEIA-derived catalogs. | see source |
| `alias_candidates` | `aliases.py` | `(value, kind, alias_maps)` | Return the original value followed by its resolved alias, if different. | see source |
| `normalize_protocol_ir_aliases` | `aliases.py` | `(protocol_ir, alias_maps)` | Return a copy of protocol IR with configured aliases normalized. | see source |
| `_normalize_field (priv)` | `aliases.py` | `(item, key, kind, alias_maps)` | see source | see source |
| `_load_yamlish (priv)` | `aliases.py` | `(path)` | see source | see source |
| `_load_simple_yaml (priv)` | `aliases.py` | `(text)` | see source | see source |
| `_parse_simple_yaml_key (priv)` | `aliases.py` | `(raw_key)` | see source | see source |
| `_parse_simple_yaml_scalar (priv)` | `aliases.py` | `(raw_value)` | see source | see source |
| `Evidence` | `compatibility.py` | class | One sourced claim used by the compatibility matrix. | , |
| `Evidence.as_dict` | `compatibility.py` | `()` | see source | see source |
| `ManualMetadata` | `compatibility.py` | class | Local metadata extracted from the cached FluentControl manual. | , |
| `ManualMetadata.as_dict` | `compatibility.py` | `()` | see source | see source |
| `ConnectorMetadata` | `compatibility.py` | class | Local metadata for a connector reference. | , |
| `ConnectorMetadata.as_dict` | `compatibility.py` | `()` | see source | see source |
| `CompatibilityRow` | `compatibility.py` | class | One FluentControl/manual/connector compatibility row. | , |
| `CompatibilityRow.as_dict` | `compatibility.py` | `()` | see source | see source |
| `TargetSetup` | `compatibility.py` | class | A setup to classify against known connector compatibility evidence. | , |
| `current_manual_target` | `compatibility.py` | `()` | Return the current cached-manual target setup. | see source |
| `build_compatibility_report` | `compatibility.py` | `()` | Build a serializable compatibility matrix from local and public evidence. | see source |
| `render_compatibility_markdown` | `compatibility.py` | `(report)` | Render a compatibility matrix report as Markdown. | see source |
| `_unitelabs_tested_builds (priv)` | `compatibility.py` | `(ai_dir)` | see source | see source |
| `_sort_builds (priv)` | `compatibility.py` | `(builds)` | see source | see source |
| `_fluentcontrol_label_for_build (priv)` | `compatibility.py` | `(build)` | see source | see source |
| `FinalizationReport` | `compiled_xscr_finalizer.py` | class | class | , |
| `FinalizationReport.record_change` | `compiled_xscr_finalizer.py` | `(change_id, summary)` | see source | see source |
| `FinalizationReport.as_dict` | `compiled_xscr_finalizer.py` | `()` | see source | see source |
| `derive_protocol_ir_for_finalization` | `compiled_xscr_finalizer.py` | `(source)` | Resolve the best available source IR for compiled XSCR finalization. | see source |
| `finalize_compiled_xscr` | `compiled_xscr_finalizer.py` | `(xscr_path, protocol_ir, source_manifest, source_scripts, options)` | Finalize one compiled XSCR in place and verify the result. | see source |
| `render_compiled_xscr_finalization_markdown` | `compiled_xscr_finalizer.py` | `(report)` | Render a compact Markdown section for compile/finalization reports. | see source |
| `_resolved_source_scripts (priv)` | `compiled_xscr_finalizer.py` | `(source_scripts, protocol_ir)` | see source | see source |
| `_normalize_compiled_variable_declaration_namespaces (priv)` | `compiled_xscr_finalizer.py` | `(xscr_path)` | see source | see source |
| `_exclude_source_inherited_command_failures (priv)` | `compiled_xscr_finalizer.py` | `(compiled_report, source_scripts)` | Keep source-equivalent offline findings visible without failing output.  This is a roundtrip gate, n | see source |
| `_subtract_inherited_validation_failures (priv)` | `compiled_xscr_finalizer.py` | `(compiled_report, baseline_failures)` | Return residual validation failures plus matching source findings. | see source |
| `_validation_failure_fingerprint (priv)` | `compiled_xscr_finalizer.py` | `(failure)` | Compare stable command semantics, not generated command positions/text. | see source |
| `_required_subroutine_references (priv)` | `compiled_xscr_finalizer.py` | `(protocol_ir)` | see source | see source |
| `_first_payload_data_index (priv)` | `compiled_xscr_finalizer.py` | `(payload)` | see source | see source |
| `_safe_parse_error (priv)` | `compiled_xscr_finalizer.py` | `(path)` | see source | see source |
| `_root_variants (priv)` | `determinism.py` | `(root)` | All on-disk spellings a path root can take across JSON/YAML/Markdown. | see source |
| `normalize_artifact_text` | `determinism.py` | `(text, roots)` | Blank volatile values so two reproducible artifacts compare equal.  Replaces every known absolute pa | see source |
| `artifact_digest` | `determinism.py` | `(path, roots)` | SHA-256 of an artifact's normalized text content. | see source |
| `compare_artifact_maps` | `determinism.py` | `(files_a, files_b)` | Compare two logical-name -> path maps after normalization.  Returns a report dict with ``determinist | see source |
| `compare_run_dirs` | `determinism.py` | `(dir_a, dir_b)` | Compare two generation output directories for byte-identical artifacts.  Every regular file present  | see source |
| `render_determinism_report` | `determinism.py` | `(report)` | Human-readable summary of a determinism comparison. | see source |
| `build_expression_provenance_ledger` | `expression_provenance.py` | `(source_artifacts)` | see source | see source |
| `write_expression_provenance_ledger` | `expression_provenance.py` | `(path, ledger)` | see source | see source |
| `load_expression_provenance_ledger` | `expression_provenance.py` | `(path)` | see source | see source |
| `verify_expression_provenance_ledger` | `expression_provenance.py` | `(ledger, source_artifacts)` | see source | see source |
| `bind_protocol_ir_expression_provenance` | `expression_provenance.py` | `(protocol_ir, ledger)` | see source | see source |
| `source_preserved_expression_allowlist_from_bundle` | `expression_provenance.py` | `(bundle_root)` | see source | see source |
| `source_preserved_expression_context_from_bundle` | `expression_provenance.py` | `(bundle_root)` | see source | see source |
| `verify_protocol_ir_expression_provenance` | `expression_provenance.py` | `(protocol_ir, ledger_verification)` | see source | see source |
| `source_preserved_expression_allowlist_from_verified_ledger` | `expression_provenance.py` | `(protocol_ir, verification)` | see source | see source |
| `_load_bundle_protocol_ir (priv)` | `expression_provenance.py` | `(bundle_root)` | see source | see source |
| `_load_bundle_expression_provenance (priv)` | `expression_provenance.py` | `(bundle_root)` | see source | see source |
| `inspect_external_command` | `external_commands.py` | `(manifest)` | Return matching source usages plus declarations and assignment chains. | see source |
| `render_external_command_contract_markdown` | `external_commands.py` | `(report)` | see source | see source |
| `write_external_command_contract` | `external_commands.py` | `(report)` | see source | see source |
| `_normalize_path_text (priv)` | `external_file_dependencies.py` | `(value)` | see source | see source |
| `default_external_file_search_roots` | `external_file_dependencies.py` | `()` | Common writable locations where operators stash FluentControl externals. | see source |
| `_permission_sensitive_path (priv)` | `external_file_dependencies.py` | `(expected)` | True when the expected path is outside the user profile (often needs elevation). | see source |
| `_collect_required_paths (priv)` | `external_file_dependencies.py` | `()` | see source | see source |
| `audit_external_file_dependencies` | `external_file_dependencies.py` | `()` | Return a structured audit of external file dependencies for a generated script. | see source |
| `render_external_file_dependencies_markdown` | `external_file_dependencies.py` | `(report)` | Operator-facing checklist for external file installs. | see source |
| `write_external_file_dependency_artifacts` | `external_file_dependencies.py` | `(report)` | Write markdown + JSON reports under ``out_dir``. | see source |
| `stage_found_external_files` | `external_file_dependencies.py` | `(report)` | Copy installable external files into ``source/external-files/`` for handoff.  Stage both files found | see source |
| `write_external_file_install_bat` | `external_file_dependencies.py` | `(staged)` | Write a root BAT that installs staged external files to script paths. | see source |
| `local_fluent_database_root` | `fluent_library.py` | `(database)` | Return the local FluentControl database root. | see source |
| `list_local_fluent_scripts` | `fluent_library.py` | `(database)` | List script records stored in the local FluentControl database.  This scans only files on disk and n | see source |
| `resolve_local_fluent_script` | `fluent_library.py` | `(script)` | Resolve one saved FluentControl script by object name/path. | see source |
| `stage_local_fluent_script` | `fluent_library.py` | `(record, out_dir)` | Copy a resolved local FluentControl script into an analysis folder. | see source |
| `add_simulate_cli_flags` | `fluentcoder_cli_args.py` | `(parser)` | Register simulate-related flags on a protocol-builder argparse parser. | see source |
| `add_compile_cli_flags` | `fluentcoder_cli_args.py` | `(parser)` | Register compile-related flags on a protocol-builder argparse parser. | see source |
| `append_simulate_cli_args` | `fluentcoder_cli_args.py` | `(command)` | Extend a fluentcoder simulate command with subroutine and snapshot options. | see source |
| `append_compile_cli_args` | `fluentcoder_cli_args.py` | `(command)` | Extend a fluentcoder compile command with compile options. | see source |
| `build_simulate_command` | `fluentcoder_cli_args.py` | `(protocol)` | Build a fluentcoder simulate subprocess argument list. | see source |
| `build_compile_command` | `fluentcoder_cli_args.py` | `(protocol, output)` | Build a fluentcoder compile subprocess argument list. | see source |
| `main` | `fluentcoder_project_runner.py` | `(argv)` | see source | see source |
| `operator_fallback_worktables` | `initialization_worktables.py` | `(fallbacks)` | Return the short fallback list shown in the opening initialization comment. | see source |
| `InitializationWorktableCandidate` | `initialization_worktables.py` | class | class | , |
| `InitializationWorktablePlan` | `initialization_worktables.py` | class | class | , |
| `InitializationWorktablePlan.comment_text` | `initialization_worktables.py` | `()` | see source | see source |
| `workspace_catalog` | `initialization_worktables.py` | `(manifest)` | see source | see source |
| `_workspace_has_fca_waste (priv)` | `initialization_worktables.py` | `(workspace)` | Return true when workspace geometry includes FCA liquid/plastics waste. | see source |
| `_required_locations_from_ir (priv)` | `initialization_worktables.py` | `(ir)` | see source | see source |
| `_script_bound_worktable_ref (priv)` | `initialization_worktables.py` | `(subroutine, scripts)` | Return WorktableWorkspace name/guid bound to a script ObjectName. | see source |
| `detect_initialization_worktable_candidates` | `initialization_worktables.py` | `(manifest)` | see source | see source |
| `_zeia_binding_index (priv)` | `initialization_worktables.py` | `(manifest)` | Index exported ZEIA script→worktable bindings by worktable name/guid.  Prefer bindings for IR-called | see source |
| `build_initialization_worktable_plan` | `initialization_worktables.py` | `(manifest)` | see source | see source |
| `fallback_names_for_initialize_step` | `initialization_worktables.py` | `(plan)` | see source | see source |
| `annotate_initialization_worktable_comment` | `initialization_worktables.py` | `(ir, source_manifest, spec)` | Insert an operator-facing initialization comment as the first IR step. | see source |
| `resolve_instrument_config_dir` | `instrument_config.py` | `(path)` | Return the host directory that contains VisionX ``.config`` files. | see source |
| `list_installed_config_names` | `instrument_config.py` | `(path)` | List installed FluentControl/VisionX configuration names from ``.config`` files. | see source |
| `infer_expected_host_config` | `instrument_config.py` | `()` | Infer conservative host configuration hints from request/source evidence. | see source |
| `inspect_host_instrument_configs` | `instrument_config.py` | `(expected)` | Compare installed host configurations against expected exact names/patterns. | see source |
| `render_host_instrument_config_markdown` | `instrument_config.py` | `(report)` | Render host instrument configuration status for generated reports. | see source |
| `_normalize_expected (priv)` | `instrument_config.py` | `(expected)` | see source | see source |
| `request_wants_interactive_script` | `interactive_script.py` | `(request_spec)` | True when the request should use Query Variable steps instead of User Prompts. | see source |
| `prepare_interactive_recipe` | `interactive_script.py` | `(recipe)` | Rewrite non-media recipe prompts as ``query_variable`` steps when appropriate. | see source |
| `ensure_recipe_variable_declaration` | `interactive_script.py` | `(variables)` | Add or update one FluentControl variable record for a query step. | see source |
| `variable_name_from_question` | `interactive_script.py` | `(prompt)` | Derive a PascalCase variable name from operator question text. | see source |
| `labware_base_label` | `labware_contracts.py` | `(label)` | Strip Fluent instance suffixes like ``[001]`` / ``[platecount]``. | see source |
| `entry_catalog` | `labware_contracts.py` | `(entry)` | see source | see source |
| `is_a200_adapter_label` | `labware_contracts.py` | `(label)` | Legacy helper. Prefer ZEIA preferred-catalog maps over label heuristics. | see source |
| `preferred_label_catalogs_from_geometry` | `labware_contracts.py` | `(geometry)` | Return ``{normalized_base_label: catalog}`` when ZEIA placements agree.  Only unanimous label→catalo | see source |
| `preferred_label_catalogs_from_labware_catalog` | `labware_contracts.py` | `(catalog)` | Prefer component type names for instance/alias labels from ``labware_catalog.json``. | see source |
| `preferred_label_catalogs_from_manifest` | `labware_contracts.py` | `(manifest)` | Merge placement truth with optional ``labware_catalog.json`` alias hints. | see source |
| `resolve_preferred_label_catalogs` | `labware_contracts.py` | `()` | Best-effort preferred map from explicit inputs or ``source.context``. | see source |
| `label_catalog_mismatch_message` | `labware_contracts.py` | `()` | see source | see source |
| `label_catalog_issue` | `labware_contracts.py` | `()` | see source | see source |
| `ir_label_catalog_issues` | `labware_contracts.py` | `(ir, preferred_label_catalogs)` | see source | see source |
| `recipe_label_catalog_issues` | `labware_contracts.py` | `(recipe, preferred_label_catalogs)` | see source | see source |
| `a200_adapter_catalog_is_valid` | `labware_contracts.py` | `(catalog)` | Deprecated. Without a preferred map this only checks non-empty catalog. | see source |
| `a200_adapter_mismatch_message` | `labware_contracts.py` | `()` | Build a mismatch message using the preferred ZEIA catalog only.  ``expected`` or ``preferred_label_c | see source |
| `a200_adapter_catalog_issue` | `labware_contracts.py` | `()` | see source | see source |
| `ir_a200_adapter_catalog_issues` | `labware_contracts.py` | `(ir, preferred_label_catalogs)` | see source | see source |
| `legacy_driver_macros_in_subroutines` | `legacy_driver_subroutines.py` | `(resolved_dependencies, ir, source_manifest)` | Walk resolved subroutine trees and collect ``LegacyDriverMacro`` findings. | see source |
| `annotate_legacy_driver_subroutine_comments` | `legacy_driver_subroutines.py` | `(ir, source_manifest)` | Insert dependency comments before subroutine calls that use legacy driver macros. | see source |
| `legacy_driver_macros_from_validation_report` | `legacy_driver_subroutines.py` | `(validation_report)` | see source | see source |
| `validation_diff_check_for_legacy_driver_subroutines` | `legacy_driver_subroutines.py` | `(protocol_ir, validation_report)` | Surface legacy driver dependencies in ``validation_diff.md``. | see source |
| `load_pattern_windows` | `pattern_index.py` | `(db_path)` | Load exact mined command windows from a tecan-reader SQLite index. | see source |
| `summarize_pattern_windows` | `pattern_index.py` | `(windows)` | Return compact, IR-friendly pattern windows with exact command steps. | see source |
| `pattern_window_dependencies` | `pattern_index.py` | `(windows)` | Return dependency records for selected mined source windows. | see source |
| `pattern_window_refs` | `pattern_index.py` | `(windows)` | Return stable human-readable references for selected mined patterns. | see source |
| `_require_pattern_tables (priv)` | `pattern_index.py` | `(conn, database)` | see source | see source |
| `_loads (priv)` | `pattern_index.py` | `(value)` | see source | see source |
| `strip_media_placeholder` | `policies/prompt_text.py` | `(text)` | Remove the trailing operator-media marker block from prompt text. | see source |
| `prompt_has_media_boilerplate` | `policies/prompt_text.py` | `(text)` | Return true when prompt text carries operator-media boilerplate. | see source |
| `normalize_operator_prompt_text` | `policies/prompt_text.py` | `(text)` | Canonicalize prompt text for comparison, rendering, and validation. | see source |
| `prompt_text_is_placeholder` | `policies/prompt_text.py` | `(normalized)` | Return true when normalized prompt text looks like a placeholder. | see source |
| `sha256_path` | `provenance.py` | `(path)` | Return the SHA-256 digest for ``path`` when it is a readable file. | see source |
| `environment_provenance` | `provenance.py` | `()` | Return a stable execution-environment summary for generation manifests. | see source |
| `distribution_version` | `provenance.py` | `(distribution)` | Return the installed or source-tree version for ``distribution``. | see source |
| `policy_profile_sha256s` | `provenance.py` | `()` | Return digests for repository policy profile files used by generation. | see source |
| `repository_commit` | `provenance.py` | `()` | Return the current repository commit hash when Git metadata is available. | see source |
| `is_scantubes_subroutine` | `recipe_capbc_lint.py` | `(name)` | see source | see source |
| `recipe_step_variable_mappings` | `recipe_capbc_lint.py` | `(raw_step)` | Return ``variable_mappings_start`` from a recipe subroutine step. | see source |
| `explicit_gripper_values_from_recipe` | `recipe_capbc_lint.py` | `(recipe)` | Gripper values declared directly on the recipe (not mined from source scripts). | see source |
| `iter_recipe_subroutine_steps` | `recipe_capbc_lint.py` | `(recipe)` | Yield ``(location, step, subroutine_name)`` for each recipe subroutine call. | see source |
| `lint_capbc_scantubes_recipe` | `recipe_capbc_lint.py` | `(spec, recipe, result)` | Add warnings for CapBC / ScanTubes prep and deck-location gaps. | see source |
| `analyze_script` | `script_analysis.py` | `(ctx)` | Analyze one imported script and optionally write report artifacts. | see source |
| `render_script_analysis_markdown` | `script_analysis.py` | `(report)` | see source | see source |
| `mappings_include_tube_prep` | `subroutine_deck_locations.py` | `(mappings)` | True when variable mappings declare tube-prep / CapBC binding targets. | see source |
| `subroutine_needs_tube_prep` | `subroutine_deck_locations.py` | `()` | Enable tube/CapBC prep from declared vars , not CapBC name alone.  Name tokens (CapBC / ScanTubes) r | see source |
| `is_tube_prep_schema_name` | `subroutine_deck_locations.py` | `(name)` | True when a VariableDefinition / mapping name belongs to tube/CapBC prep schema. | see source |
| `canonicalize_prep_variable_name` | `subroutine_deck_locations.py` | `(name)` | Map CapBC subroutine targets (e.g. GripTubeClose) onto caller prep vars. | see source |
| `mine_prep_schema_from_mappings` | `subroutine_deck_locations.py` | `(mappings)` | Collect prep schema names from subroutine call VariableMappings. | see source |
| `mine_prep_schema_from_subroutine_decls` | `subroutine_deck_locations.py` | `(xscr_path)` | Return CapBC/tube prep VariableDefinitions from a subroutine ``.xscr``. | see source |
| `grip_values_from_subroutine_decls` | `subroutine_deck_locations.py` | `(declarations)` | Mine GripperClose/Open defaults from CapBC subroutine VariableDefinitions. | see source |
| `looks_like_tube_runner_name` | `subroutine_deck_locations.py` | `(name)` | Exact phrase gate , no bare ``tube`` / Falcon invent. | see source |
| `mine_tube_runner_from_placements` | `subroutine_deck_locations.py` | `(manifest, worktable_name)` | Mine ``TubeRunnerName`` / ``TubeLabwareTypeName`` from worktable placements. | see source |
| `collect_tube_prep_subroutine_names` | `subroutine_deck_locations.py` | `(recipe, ir)` | CapBC/ScanTubes (or mapping-declared tube-prep) subroutine names from recipe/IR. | see source |
| `resolve_subroutine_xscr_path` | `subroutine_deck_locations.py` | `(context, subroutine_name)` | Resolve an imported subroutine ``.xscr`` path by object name (same as scripts). | see source |
| `build_tube_prep_schema` | `subroutine_deck_locations.py` | `()` | Build CapBC/tube prep schema from subroutine decls + call mappings (+ known set).  Returns ``{names, | see source |
| `needs_prep_fixup` | `subroutine_deck_locations.py` | `(name, value)` | Return True when a CapBC prep default is missing or an invalid zero-like value. | see source |
| `is_capbc_subroutine` | `subroutine_deck_locations.py` | `(name)` | see source | see source |
| `_iter_set_variable_statements (priv)` | `subroutine_deck_locations.py` | `(xscr_path)` | Return ``(Name, Value)`` pairs in document order from a source XSCR. | see source |
| `extract_set_variable_defaults_from_xscr` | `subroutine_deck_locations.py` | `(xscr_path, variable_names)` | Collect ``SetVariableStatement`` values from a source script XSCR. | see source |
| `extract_set_variable_order_from_xscr` | `subroutine_deck_locations.py` | `(xscr_path, variable_names)` | Return first-seen ``SetVariableStatement`` Name order from a source XSCR.  When ``variable_names`` i | see source |
| `capbc_prep_emit_order` | `subroutine_deck_locations.py` | `(prep_defaults)` | Order CapBC prep vars: source XSCR SetVariable sequence, then fallbacks. | see source |
| `worktable_location_names` | `subroutine_deck_locations.py` | `(manifest, worktable_name)` | Return location/site names for a worktable from imported ZEIA geometry. | see source |
| `_location_candidates_from_scripts (priv)` | `subroutine_deck_locations.py` | `(manifest)` | Return ``(location, script_name)`` mined from imported startup variables.  Preferred CapBC/ScanTubes | see source |
| `resolve_tube_deck_location` | `subroutine_deck_locations.py` | `(recipe)` | Return ``(location_name, resolution_reason)`` for tube cap/scan subroutines.  Fail-closed resolution | see source |
| `resolve_capbc_prep_defaults` | `subroutine_deck_locations.py` | `(recipe)` | Resolve CapBC prep values from recipe overrides or mined ZEIA sources.  Schema (which prep vars exis | see source |
| `_prep_values_from_script_startup (priv)` | `subroutine_deck_locations.py` | `(manifest)` | Mine CapBC prep vars from imported script ``startup_variables`` defaults. | see source |
| `mapping_needs_input_sub_location_fix` | `subroutine_deck_locations.py` | `(mapping)` | see source | see source |
| `normalize_variable_mappings` | `subroutine_deck_locations.py` | `(mappings, deck_location)` | see source | see source |
| `normalize_recipe_subroutine_deck_locations` | `subroutine_deck_locations.py` | `(recipe, deck_location)` | see source | see source |
| `_normalize_ir_prep_variables (priv)` | `subroutine_deck_locations.py` | `(variables, prep_defaults)` | see source | see source |
| `format_set_variable_value` | `subroutine_deck_locations.py` | `(name, value)` | Format a prep default for IR / ``wt.set_variable`` (not XML-escaped). | see source |
| `build_set_variable_ir_step` | `subroutine_deck_locations.py` | `(variable, value)` | see source | see source |
| `reindex_ir_steps` | `subroutine_deck_locations.py` | `(steps)` | see source | see source |
| `emit_capbc_prep_set_variable_steps` | `subroutine_deck_locations.py` | `(ir, prep_defaults)` | Insert explicit ``set_variable`` IR steps before the first CapBC subroutine call.  Emit order prefer | see source |
| `apply_subroutine_deck_location_bindings` | `subroutine_deck_locations.py` | `(ir)` | Normalize CapBC prep bindings (deck location, grip widths, tube metadata) in IR. | see source |
| `apply_deck_location_fixups_to_xscr` | `subroutine_deck_locations.py` | `(xscr_path, deck_location)` | Backward-compatible wrapper for deck-location-only XSCR fixups. | see source |
| `apply_capbc_prep_fixups_to_xscr` | `subroutine_deck_locations.py` | `(xscr_path, prep_variables)` | Post-compile safety net for CapBC prep variables and ``InputSubLocation`` mappings. | see source |
| `clean_subroutine_reference` | `subroutine_dependencies.py` | `(value)` | see source | see source |
| `norm_subroutine_key` | `subroutine_dependencies.py` | `(value)` | see source | see source |
| `subroutine_calls_from_ir` | `subroutine_dependencies.py` | `(ir)` | see source | see source |
| `upsert_ir_subroutine_dependencies` | `subroutine_dependencies.py` | `(ir)` | see source | see source |
| `resolve_subroutine_dependencies` | `subroutine_dependencies.py` | `(ir, source_manifest)` | see source | see source |
| `validate_compiled_subroutine_references` | `subroutine_dependencies.py` | `(xscr_path, resolved_dependencies)` | see source | see source |
| `compiled_script_references` | `subroutine_dependencies.py` | `(root)` | see source | see source |
| `find_subroutine_record` | `subroutine_dependencies.py` | `(source_manifest, scripts, ref, parent)` | see source | see source |
| `_dedupe_equivalent_script_records (priv)` | `subroutine_dependencies.py` | `(scripts)` | Collapse duplicate context records for the same FluentControl Script identity. | see source |
| `subroutine_dependency_records_from_artifacts` | `subroutine_dependencies.py` | `(items)` | see source | see source |
| `normalize_subroutine_error_policy` | `subroutine_inlining.py` | `(value)` | see source | see source |
| `inline_problem_subroutine_calls` | `subroutine_inlining.py` | `(ir, source_manifest)` | Replace risky ``call_subroutine`` steps with local steps or prompts.  ``inline_local_on_error`` keep | see source |
| `_build_inline_plan (priv)` | `subroutine_inlining.py` | `(ir, source_manifest)` | see source | see source |
| `_placed_labware_labels (priv)` | `subroutine_inlining.py` | `(ir)` | Labels already on the deck from the labware table and emitted add_labware steps. | see source |
| `variable_names_from_xscr` | `subroutine_inlining.py` | `(path)` | Small public helper for future repair passes that need variable conflict checks. | see source |
| `variable_definitions_from_xscr` | `subroutine_variable_mappings.py` | `(xscr_path)` | see source | see source |
| `_has_local_main_references (priv)` | `subroutine_variable_mappings.py` | `(ir, name)` | Return true when a conflicting variable is used outside call-boundary plumbing.  Direct query/set co | see source |
| `reconcile_ir_subroutine_variable_definitions` | `subroutine_variable_mappings.py` | `(ir, lookup)` | Make main-script variable declarations agree with called subroutines.  FluentControl rejects generat | see source |
| `script_record_path` | `subroutine_variable_mappings.py` | `(record)` | see source | see source |
| `valid_mapping_targets_for_subroutine` | `subroutine_variable_mappings.py` | `(subroutine, lookup)` | see source | see source |
| `filter_variable_mappings` | `subroutine_variable_mappings.py` | `(mappings, valid_targets)` | see source | see source |
| `normalize_ir_subroutine_variable_mappings` | `subroutine_variable_mappings.py` | `(ir, lookup)` | Drop IR subroutine mappings whose target is absent from the called subroutine. | see source |
| `mapping_pairs` | `subroutine_variable_mappings.py` | `(mappings)` | see source | see source |
| `subroutine_mappings_match_for_parity` | `subroutine_variable_mappings.py` | `(ir_mappings, compiled_mappings)` | Compare IR vs compiled mappings, ignoring stale IR-only invalid targets. | see source |
| `build_script_lookup_from_manifest` | `subroutine_variable_mappings.py` | `(manifest)` | see source | see source |
| `list_templates` | `template_library.py` | `(templates_dir)` | Return installed template summaries. | see source |
| `template_path` | `template_library.py` | `(name, templates_dir)` | Resolve a template folder by name. | see source |
| `load_template_ir` | `template_library.py` | `(name, templates_dir)` | Load and validate a template's canonical IR. | see source |
| `load_request_schema` | `template_library.py` | `(name, templates_dir)` | Load the template-specific request schema. | see source |
| `template_info` | `template_library.py` | `(name, templates_dir)` | Return a detailed template inventory record. | see source |
| `step_trace_ref` | `traceability.py` | `(step, fallback_index)` | Return the stable trace reference for an IR step. | see source |
| `render_step_trace_comment` | `traceability.py` | `(step, fallback_index)` | Render a Python-only trace comment that survives repair copies. | see source |
| `build_traceability_map` | `traceability.py` | `()` | Build a durable map across request, IR, Python draft, and compiled XSCR. | see source |
| `render_traceability_markdown` | `traceability.py` | `(trace_map)` | Render a human-readable traceability artifact. | see source |
| `annotate_findings_with_trace` | `traceability.py` | `(findings, trace_map)` | Attach trace references to compiled/static findings when line or entity data matches. | see source |
| `annotate_runtime_report_with_trace` | `traceability.py` | `(report, trace_map)` | Attach trace references to line-specific runtime/load errors. | see source |
| `trace_reference_for_error` | `traceability.py` | `(message, trace_map)` | Return the best trace reference for a FluentControl line-specific error. | see source |
| `_normalized_number (priv)` | `traceability.py` | `(value)` | see source | see source |
| `_rewrite_namespace_prefix (priv)` | `variable_namespaces.py` | `(text, old, new)` | see source | see source |
| `_known_namespace_prefixes (priv)` | `variable_namespaces.py` | `(text)` | Return document-level aliases for namespaces FluentControl needs locally.  ElementTree may choose a  | see source |
| `_root_prefix_is_used_outside_variable_declarations (priv)` | `variable_namespaces.py` | `(text, prefix)` | Whether removing a root binding would break non-variable XML.  The namespace pass is intentionally s | see source |
| `localize_variable_declaration_namespaces` | `variable_namespaces.py` | `(text)` | Make VariableDeclarations safe for FluentControl's InnerXml deserializer.  FluentControl deserialize | see source |
| `variable_declaration_fragment_span` | `variable_namespaces.py` | `(text)` | see source | see source |
| `variable_declaration_fragment` | `variable_namespaces.py` | `(text)` | see source | see source |
| `variable_declaration_fragment_error` | `variable_namespaces.py` | `(text)` | see source | see source |
| `assert_variable_declarations_are_standalone` | `variable_namespaces.py` | `(text)` | see source | see source |
| `VariableReconciliationFailure` | `variable_reconciliation.py` | class | class | , |
| `VariableReconciliationFailure.as_dict` | `variable_reconciliation.py` | `()` | see source | see source |
| `_index_ir_variable_declarations (priv)` | `variable_reconciliation.py` | `(ir)` | Index every IR variable declaration by name, including startup_variables. | see source |
| `_collapse_identical_ir_variables (priv)` | `variable_reconciliation.py` | `(ir)` | Drop duplicate declarations with the same name and compatible fields across IR. | see source |
| `find_undeclared_variable_references` | `variable_reconciliation.py` | `(ir)` | Return unresolved variable references without mutating protocol IR. | see source |
| `ensure_referenced_variables_declared` | `variable_reconciliation.py` | `(ir)` | Legacy migration helper that materializes missing declarations.  Normal generation must call ``find_ | see source |
| `preflight_variable_reconciliation` | `variable_reconciliation.py` | `(ir)` | Run offline variable reconciliation before Python/XML generation.  Mutates ``ir`` to collapse exact  | see source |
| `validate_xscr_variable_declarations` | `variable_reconciliation.py` | `(xscr_path)` | Fail on duplicate declarations or defaults FluentControl cannot materialize. | see source |
| `failures_to_dicts` | `variable_reconciliation.py` | `(failures)` | see source | see source |
| `render_variable_reconciliation_markdown` | `variable_reconciliation.py` | `(report)` | see source | see source |
| `collect_variable_seeds` | `variable_seeds.py` | `()` | Merge IR and request.spec seeds for ``RuntimeController.SetVariableValue``. | see source |
| `variable_seeds_as_json` | `variable_seeds.py` | `(seeds)` | see source | see source |
| `apply_variable_seeds_offline` | `variable_seeds.py` | `(seeds)` | Offline scaffold for api-v2-034 SetVariableValue before PrepareMethod. | see source |
| `configure_workflow_sinks` | `workflow_events.py` | `()` | see source | see source |
| `reset_workflow_sinks` | `workflow_events.py` | `()` | see source | see source |
| `elapsed_ms` | `workflow_events.py` | `()` | see source | see source |
| `emit_workflow_event` | `workflow_events.py` | `(payload)` | see source | see source |
| `append_timing_phase` | `workflow_events.py` | `(record)` | see source | see source |
| `timing_summary` | `workflow_events.py` | `()` | see source | see source |
| `workflow_phase` | `workflow_events.py` | `(stage, message)` | Emit start/done events with duration_ms and since_previous_ms. | see source |
| `write_progress_line` | `workflow_events.py` | `(message)` | see source | see source |
| `diff_worktable_requirements` | `worktable_diff.py` | `(protocol_ir)` | Compare source ZEIA context/worktable data with protocol IR requirements. | see source |
| `render_worktable_changes_markdown` | `worktable_diff.py` | `(diff)` | see source | see source |
| `worktable_patch_from_diff` | `worktable_diff.py` | `(diff)` | Return a machine-readable worktable patch derived from a worktable diff. | see source |
| `render_worktable_patch_json` | `worktable_diff.py` | `(diff)` | see source | see source |
| `_select_workspace_from_script_refs (priv)` | `worktable_diff.py` | `(manifest, workspaces)` | Prefer Script→WorktableWorkspace GUIDs over archive workspaces[0] order. | see source |
| `_requirements_from_ir (priv)` | `worktable_diff.py` | `(ir)` | see source | see source |
| `_required_tip_boxes (priv)` | `worktable_diff.py` | `(source, requirements, alias_maps)` | see source | see source |
| `_required_labware_record (priv)` | `worktable_diff.py` | `(by_label, label, alias_maps)` | see source | see source |
| `_required_labware_label_exists (priv)` | `worktable_diff.py` | `(by_label, label, alias_maps)` | see source | see source |
| `_add_required_labware_label (priv)` | `worktable_diff.py` | `(by_label, label, record, alias_maps)` | see source | see source |
| `_add_required (priv)` | `worktable_diff.py` | `(values, value, alias_kind, alias_maps)` | see source | see source |
| `_add_required_path (priv)` | `worktable_diff.py` | `(values, value)` | see source | see source |
