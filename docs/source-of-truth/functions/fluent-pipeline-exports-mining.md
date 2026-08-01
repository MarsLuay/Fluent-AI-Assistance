# Functions: fluent-pipeline-exports-mining

Source roots: `fluent_pipeline/` (12 files)

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `BundleRecord` | `bundle_lifecycle.py` | class | class | , |
| `BundleRecord.to_dict` | `bundle_lifecycle.py` | `()` | see source | see source |
| `lifecycle_metadata` | `bundle_lifecycle.py` | `()` | Return normalized lifecycle fields for metadata/manifests. | see source |
| `source_export_kind` | `bundle_lifecycle.py` | `(full_zeia_export)` | see source | see source |
| `verification_state_from_readiness` | `bundle_lifecycle.py` | `()` | see source | see source |
| `bundle_role_from_manifest` | `bundle_lifecycle.py` | `(manifest)` | see source | see source |
| `created_from_record` | `bundle_lifecycle.py` | `()` | see source | see source |
| `scan_bundle_lifecycle` | `bundle_lifecycle.py` | `()` | see source | see source |
| `archive_recommended_bundles` | `bundle_lifecycle.py` | `(records)` | see source | see source |
| `render_bundle_index` | `bundle_lifecycle.py` | `(records)` | see source | see source |
| `seed_prompt_media_from_bundle` | `bundle_media.py` | `(bundle_dir, media_dir)` | Copy real prompt media from prior ready-to-import bundle(s) into a new build. | see source |
| `resolve_touchtools_media_seed_dir` | `bundle_media.py` | `()` | Resolve a TouchTools ``Images/<Script>_media`` folder for media seeding. | see source |
| `seed_prompt_media_from_touchtools` | `bundle_media.py` | `(media_dir, touchtools_dir)` | Copy deployed TouchTools media into build slots (with image/video basename aliases). | see source |
| `stage_generation_media_originals` | `bundle_media.py` | `(build_dir, media_dir, media_ops)` | Copy raw captures and pre-process GIFs into ``source/media-originals/`` during generate. | see source |
| `_merge_media_resolve_policy (priv)` | `bundle_media.py` | `(policy)` | see source | see source |
| `_parse_prompt_number_from_basename (priv)` | `bundle_media.py` | `(name)` | see source | see source |
| `_first_media_prompt_after_step_number (priv)` | `bundle_media.py` | `(ir, after_num)` | First ``prompt_user`` step with media whose ``step_NNN`` id is greater than ``after_num``. | see source |
| `_resolve_visual_source_for_slot (priv)` | `bundle_media.py` | `(spec, inventory)` | see source | see source |
| `_resolve_audio_for_ir (priv)` | `bundle_media.py` | `(ir, inventory, media_dir)` | see source | see source |
| `resolve_prompt_media` | `bundle_media.py` | `(ir, media_dir)` | Resolve missing or placeholder prompt media after bundle/TouchTools seeding. | see source |
| `_stage_raw_video_captures (priv)` | `bundle_media.py` | `(media_dir, specs)` | Copy raw video captures beside GIF slots so dropped-video conversion can run. | see source |
| `process_prompt_media_captures` | `bundle_media.py` | `(ir, media_dir)` | Resolve raw operator captures (images/videos) into prompt media slots.  Scans ``media/unprocessed/`` | see source |
| `organize_bundle_touchtools_media` | `bundle_media.py` | `(bundle_media_dir, source_dir)` | Split bundle media into deploy-ready ``processed/`` and raw ``unprocessed/`` copies. | see source |
| `render_bundle_media_readme` | `bundle_media.py` | `()` | see source | see source |
| `assign_step_label_media_to_final_prompts` | `bundle_media.py` | `(ir, files)` | Resolve ``stepN.ext`` captures to media slots by final visible prompt number.  The visible prompt la | see source |
| `_load_json (priv)` | `bundle_media.py` | `(path)` | see source | see source |
| `build_profiles_from_component_counts` | `connector_coverage_export.py` | `(component_names, per_component_counts)` | One coverage row per component that has connectors in this ZEIA/install. | see source |
| `build_connector_coverage_from_geometry` | `connector_coverage_export.py` | `(geometry)` | Mine connector coverage profiles from this ZEIA/install geometry only. | see source |
| `write_connector_coverage` | `connector_coverage_export.py` | `(destination, geometry)` | Write ``connector_coverage.json`` when geometry has components. Return path or None. | see source |
| `write_connector_coverage_for_context` | `connector_coverage_export.py` | `(context_root, geometry)` | Write coverage next to ``manifest.json`` / ``labware_catalog.json`` under a context root. | see source |
| `_component_guids_matching_names (priv)` | `connector_coverage_export.py` | `(component_names, patterns)` | Deprecated helper kept for callers that still filter by name substring. | see source |
| `build_connector_graph_from_geometry` | `connector_graph_export.py` | `(geometry)` | Convert ZEIA ``worktable_geometry`` connectors/sites into a fluent-connector-graph. | see source |
| `build_connector_graph_from_datastore` | `connector_graph_export.py` | `(datastore_root)` | Walk ``Connectors/*.xcon`` under a ZEIA/install DataStore , full Snap edges. | see source |
| `build_connector_graph_for_package` | `connector_graph_export.py` | `(geometry)` | Prefer the fuller of geometry-derived and DataStore Snap graphs. | see source |
| `select_richer_connector_graph` | `connector_graph_export.py` | `(left, right)` | Keep the graph with more Snap connector edges (ties → left). | see source |
| `resolve_worktable_datastore` | `worktable_datastore.py` (used by connector/labware exporters) | `(path)` | Return DataStore/worktable root when ``SystemSpecific/Worktable`` exists. | see source |
| `discover_worktable_datastore` | `worktable_datastore.py` (used by connector/labware exporters) | `(context_root)` | Find ZEIA/install worktable root under a project context or extract tree. | see source |
| `write_connector_graph` | `connector_graph_export.py` | `(destination, geometry)` | Write ``connector_graph.json`` from geometry and/or ZEIA DataStore Snap walk. | see source |
| `write_connector_graph_for_context` | `connector_graph_export.py` | `(context_root, geometry)` | Write the graph next to ``manifest.json`` under a project context root. | see source |
| `DeliveryBundleIssue` | `delivery_bundle.py` | class | class | , |
| `DeliveryBundleIssue.to_dict` | `delivery_bundle.py` | `()` | see source | see source |
| `DeliveryBundleValidationResult` | `delivery_bundle.py` | class | class | , |
| `DeliveryBundleValidationResult.errors` | `delivery_bundle.py` | `()` | see source | see source |
| `DeliveryBundleValidationResult.to_dict` | `delivery_bundle.py` | `()` | see source | see source |
| `validate_v2_delivery_bundle` | `delivery_bundle.py` | `(bundle_dir)` | Validate the complete V2 delivery-folder contract.  This checks the final human/AI delivery bundle,  | see source |
| `render_delivery_bundle_validation` | `delivery_bundle.py` | `(result)` | see source | see source |
| `delivery_bundle_failure_message` | `delivery_bundle.py` | `(result)` | see source | see source |
| `_validate_manifest_artifacts (priv)` | `delivery_bundle.py` | `(manifest)` | see source | see source |
| `_validate_external_file_deployments (priv)` | `delivery_bundle.py` | `(manifest)` | see source | see source |
| `_validate_no_unpublished_artifacts (priv)` | `delivery_bundle.py` | `(bundle_dir, protocol_name, add_issue)` | see source | see source |
| `build_driver_macros_catalog` | `driver_macros_export.py` | `()` | Mine macro_name / module_name pairs from scripts and optional DataStore objects. | see source |
| `write_driver_macros_catalog` | `driver_macros_export.py` | `(destination)` | Write ``driver_macros.json``. Empty catalog still writes (soft inventory). | see source |
| `write_driver_macros_for_context` | `driver_macros_export.py` | `(context_root, manifest)` | see source | see source |
| `load_driver_macros_catalog` | `driver_macros_export.py` | `(path)` | see source | see source |
| `_resolve_manifest_path (priv)` | `driver_macros_export.py` | `(manifest, item)` | see source | see source |
| `_ArchiveWriterUnavailable` | `exports.py` | class | Raised when the FluentControl archive writer cannot run locally. | , |
| `ExportedArtifact` | `exports.py` | class | class | , |
| `ReadyBundleStage` | `exports.py` | class | class | , |
| `ReadyBundlePublishPlan` | `exports.py` | class | class | , |
| `_ReadyBundleTransactionError` | `exports.py` | class | Raised when a staged bundle fails at a specific transaction boundary. | , |
| `export_ready_to_import` | `exports.py` | `(compiled_xscr)` | Copy artifacts into a strict `ready-to-import/<script>/` bundle. | see source |
| `_write_bundle_transaction_failure_metadata (priv)` | `exports.py` | `(metadata_path)` | see source | see source |
| `_replace_path_with_retry (priv)` | `exports.py` | `(source, destination)` | Atomically replace a path, tolerating transient Windows file locks. | see source |
| `publish_ready_to_import_bundle` | `exports.py` | `(stage)` | Deprecated staging-folder publish.  Do not use for handoff. Delivery bundles must go through ``publi | see source |
| `publish_ready_to_import_zeia` | `exports.py` | `(stage)` | Publish validated generated ZEIA archives as complete protocol delivery folders. | see source |
| `_copy_v2_source_tree (priv)` | `exports.py` | `(source_dir, destination_dir)` | Copy the accepted V2 companion tree without importable/intermediate artifacts. | see source |
| `_write_touchtools_deploy_config (priv)` | `exports.py` | `(source_dir, protocol_folder)` | see source | see source |
| `_stage_external_file_deployments (priv)` | `exports.py` | `(archive_path)` | Stage non-TouchTools ZEIA filesystem payloads with exact deployment metadata. | see source |
| `_write_delivery_manifest (priv)` | `exports.py` | `(stage)` | see source | see source |
| `attach_generation_reports_to_protocol_folders` | `exports.py` | `(artifact_paths)` | Atomically attach final generation reports to published protocol folders. | see source |
| `attach_generation_reports_to_protocol_folder` | `exports.py` | `(protocol_dir)` | Attach final generation reports to one protocol delivery folder via atomic replacement. | see source |
| `_validate_published_protocol_folder (priv)` | `exports.py` | `(protocol_dir, protocol_folder)` | see source | see source |
| `cleanup_ready_to_import_stage` | `exports.py` | `(stage)` | Discard a staged bundle that was not published. | see source |
| `attach_generation_reports_to_ready_bundles` | `exports.py` | `(artifact_paths)` | Attach finalized workflow reports to ready bundles created by this run. | see source |
| `attach_generation_reports_to_bundle` | `exports.py` | `(bundle_dir)` | Attach finalized workflow reports to a specific staged or published bundle. | see source |
| `audit_ready_bundle` | `exports.py` | `(bundle_dir)` | Audit a staged or published ready bundle before publication or handoff. | see source |
| `_minimal_harness_request_spec_yaml (priv)` | `exports.py` | `(protocol_name)` | Stub request.spec.yaml so Path A assemble can publish harness builders. | see source |
| `next_ready_bundle_name` | `exports.py` | `(root, base_name)` | Return the next versioned ready-to-import bundle name for a protocol family. | see source |
| `plan_ready_to_import_publish` | `exports.py` | `(root, base_name)` | Reserve versioned ready-to-import paths for a single publish attempt. | see source |
| `_retarget_exported_artifacts (priv)` | `exports.py` | `(artifacts)` | see source | see source |
| `_protocol_name_stems (priv)` | `exports.py` | `(ir)` | Normalized protocol stems used to find prior ready-to-import bundles. | see source |
| `_prior_ready_bundles_for_protocol (priv)` | `exports.py` | `(ir)` | Newest-first ready-to-import folders whose stem matches the protocol family. | see source |
| `_materialize_step_label_media_into_media_dir (priv)` | `exports.py` | `(ir, media_dir)` | Copy ``stepN.ext`` captures onto underscore slot filenames before placeholders. | see source |
| `_normalize_windows_key (priv)` | `exports.py` | `(value)` | see source | see source |
| `_remove_generated_media_unresolved_paths (priv)` | `exports.py` | `(filesystem_packaging, generated_media_packaging)` | see source | see source |
| `_write_project_import_archives (priv)` | `exports.py` | `(source_projects)` | see source | see source |
| `_write_generated_project_archive (priv)` | `exports.py` | `(source_project, destination)` | see source | see source |
| `_write_generated_project_archive_with_fluent_writer (priv)` | `exports.py` | `(source_project, destination)` | see source | see source |
| `_write_generated_project_archive_legacy_zip (priv)` | `exports.py` | `(source_project, destination)` | see source | see source |
| `verify_generated_project_archive` | `exports.py` | `(archive_path)` | Open a packaged ``generated_project.zeia`` and verify it is importable.  This re-reads the *written* | see source |
| `_audit_script_node_identity (priv)` | `exports.py` | `(archive_data)` | Block metadata that would make FluentControl remap a checksummed script. | see source |
| `verify_added_subroutine_metadata` | `exports.py` | `(archive_path, added_subroutines)` | Audit the datastore metadata quality of newly-ADDED subroutines.  Replaced subroutines reuse an exis | see source |
| `_merge_project_audits (priv)` | `exports.py` | `(records)` | Collapse per-archive audits into context payloads for the ready gates. | see source |
| `_write_project_import_report_artifacts (priv)` | `exports.py` | `(records)` | see source | see source |
| `_render_project_import_report (priv)` | `exports.py` | `(records)` | see source | see source |
| `_archive_writer_dependency_records (priv)` | `exports.py` | `(archive_data)` | Return direct source datastore records needed by the generated script.  The FluentControl archive wr | see source |
| `_fluent_import_unsupported_dependency (priv)` | `exports.py` | `(record)` | see source | see source |
| `_friendly_import_unsupported_type (priv)` | `exports.py` | `(record)` | see source | see source |
| `_prepare_project_script_payload (priv)` | `exports.py` | `(path)` | see source | see source |
| `_normalize_script_folder (priv)` | `exports.py` | `(value)` | see source | see source |
| `_strip_unavailable_optional_references (priv)` | `exports.py` | `(data, archive_data)` | Validate every model reference in a script against the source ZEIA base.  The generated ZEIA reuses  | see source |
| `_next_nodedescription_version (priv)` | `exports.py` | `(text)` | Return one past the highest existing per-node ``<V>`` value in a nodedescription.  Real archives use | see source |
| `_write_zip_info (priv)` | `exports.py` | `(out_zip, info, data)` | see source | see source |
| `_restore_windows_datastore_zip_names (priv)` | `exports.py` | `(archive_path)` | Undo Python 3.14's slash normalization for FluentControl datastore entries.  FluentControl ZEIA arch | see source |
| `_rewrite_zip_filename_records (priv)` | `exports.py` | `(data)` | see source | see source |
| `_fluentcontrol_userspecific_dirs (priv)` | `exports.py` | `()` | Return candidate FluentControl UserSpecific datastore directories. | see source |
| `_find_local_fluentcontrol_script_guid (priv)` | `exports.py` | `(object_name, folder)` | Return the installed GUID for the same script name+folder, if present.  FluentControl import matches | see source |
| `_unique_project_guid (priv)` | `exports.py` | `(source_project, object_name, path, existing_guids)` | Return a deterministic uuid5 GUID that does not collide with the archive.  Mirrors :func:`_stable_pr | see source |
| `_normalize_archive_entry (priv)` | `exports.py` | `(entry)` | see source | see source |
| `_script_file_references (priv)` | `exports.py` | `(path)` | Extract external-file dependencies declared by a script.  Real ``.xscr`` payloads list binary/asset  | see source |
| `_normalize_archive_writer_script_payload (priv)` | `exports.py` | `(data)` | see source | see source |
| `_postprocess_archive_writer_script_payload (priv)` | `exports.py` | `(data)` | see source | see source |
| `_script_folder_from_payload (priv)` | `exports.py` | `(data)` | see source | see source |
| `_script_type_version_from_payload (priv)` | `exports.py` | `(data)` | see source | see source |
| `_script_reference_guids_from_payload (priv)` | `exports.py` | `(data)` | see source | see source |
| `_fluent_archive_writer_available (priv)` | `exports.py` | `()` | see source | see source |
| `_run_fluent_archive_writer (priv)` | `exports.py` | `()` | see source | see source |
| `_resolved_subroutine_artifacts (priv)` | `exports.py` | `(source_manifest)` | see source | see source |
| `_find_subroutine_record (priv)` | `exports.py` | `(source_manifest, scripts, ref, parent)` | Resolve a subroutine reference to a manifest script record.  Returns ``(match, alternatives)``. ``al | see source |
| `_subroutine_match_strength (priv)` | `exports.py` | `(script, forms)` | Rank how strongly a script matches a subroutine reference (0 = no match).  Higher is a more reliable | see source |
| `_render_subroutine_manifest (priv)` | `exports.py` | `(records)` | see source | see source |
| `_resolved_hardware_artifacts (priv)` | `exports.py` | `(source_manifest)` | see source | see source |
| `_write_labware_catalog_artifact (priv)` | `exports.py` | `(source_manifest)` | Persist ZEIA-derived labware catalog into the bundle source tree.  Prefer detailed ``worktable_geome | see source |
| `_write_connector_coverage_artifact (priv)` | `exports.py` | `(source_manifest)` | Persist ZEIA-derived connector coverage (name profiles → resolved GUIDs/counts). | see source |
| `_write_connector_graph_artifact (priv)` | `exports.py` | `(source_manifest)` | Persist ZEIA-derived connector snap graph into the bundle source tree.  Prefer full ``Connectors/*.x | see source |
| `_write_liquid_classes_artifact (priv)` | `exports.py` | `(source_manifest)` | Persist ZEIA-derived liquid class catalog (``*.xlqc``) into the bundle source tree. | see source |
| `_write_driver_macros_artifact (priv)` | `exports.py` | `(source_manifest)` | Persist ZEIA-mined Legacy/Application driver macro inventory (soft-empty OK). | see source |
| `_write_script_folder_bindings_artifact (priv)` | `exports.py` | `(source_manifest)` | Persist Scripts-folder tree + script→worktable bindings from ZEIA manifest. | see source |
| `_write_hardware_artifacts (priv)` | `exports.py` | `(report)` | see source | see source |
| `_render_hardware_pins_checklist (priv)` | `exports.py` | `(report)` | see source | see source |
| `_write_method_touchtools_artifacts (priv)` | `exports.py` | `(report)` | see source | see source |
| `_parse_xml_safely (priv)` | `exports.py` | `(path)` | see source | see source |
| `_render_method_touchtools_readiness (priv)` | `exports.py` | `(report)` | see source | see source |
| `_write_protocol_ir_from_draft (priv)` | `exports.py` | `(draft_path, destination)` | see source | see source |
| `_write_unavailable_json (priv)` | `exports.py` | `(destination, reason)` | see source | see source |
| `_write_recreate_from_ir (priv)` | `exports.py` | `(protocol_ir_path, destination)` | see source | see source |
| `_write_recreate_unavailable (priv)` | `exports.py` | `(protocol_ir_path, destination)` | see source | see source |
| `_write_worktable_changes_from_ir (priv)` | `exports.py` | `(protocol_ir_path, destination)` | see source | see source |
| `_write_worktable_patch_from_ir (priv)` | `exports.py` | `(protocol_ir_path, destination)` | see source | see source |
| `_render_worktable_changes_unavailable (priv)` | `exports.py` | `(protocol_ir_path)` | see source | see source |
| `_render_worktable_patch_unavailable (priv)` | `exports.py` | `(protocol_ir_path)` | see source | see source |
| `_normalize_report_key (priv)` | `exports.py` | `(key)` | see source | see source |
| `_render_worktable_changes (priv)` | `exports.py` | `(details)` | see source | see source |
| `_render_recreate_guide (priv)` | `exports.py` | `(metadata)` | see source | see source |
| `_render_manual_recreation (priv)` | `exports.py` | `(details)` | see source | see source |
| `normalize_script_folder` | `fluentcontrol_inventory.py` | `(value)` | see source | see source |
| `inventory_key` | `fluentcontrol_inventory.py` | `(folder, object_name)` | see source | see source |
| `fluentcontrol_userspecific_dirs` | `fluentcontrol_inventory.py` | `()` | see source | see source |
| `fluentcontrol_systemspecific_dirs` | `fluentcontrol_inventory.py` | `()` | see source | see source |
| `build_scripts_inventory` | `fluentcontrol_inventory.py` | `(userspecific_dir)` | Scan UserSpecific ``.xscr`` files into a packaging inventory. | see source |
| `write_scripts_inventory` | `fluentcontrol_inventory.py` | `(path, inventory)` | see source | see source |
| `load_scripts_inventory` | `fluentcontrol_inventory.py` | `(path)` | see source | see source |
| `find_unique_guid` | `fluentcontrol_inventory.py` | `(inventory, object_name, folder)` | see source | see source |
| `collision_preflight` | `fluentcontrol_inventory.py` | `(inventory, object_name, folder)` | see source | see source |
| `find_local_script_guid` | `fluentcontrol_inventory.py` | `(object_name, folder)` | Unique local GUID for name+folder, or None if missing/ambiguous. | see source |
| `resolve_local_script_guid_for_name` | `fluentcontrol_inventory.py` | `(inventory, object_name)` | Resolve a local GUID for a script ObjectName.  Returns ``(guid, reason)`` where reason is ``unique`` | see source |
| `rewrite_script_reference_guids` | `fluentcontrol_inventory.py` | `(payload, inventory)` | Rewrite ``<Reference>`` Script GUIDs to unique local GUIDs; skip ambiguous. | see source |
| `_index_systemspecific_objects (priv)` | `fluentcontrol_inventory.py` | `(systemspecific_dir)` | Map ObjectName.casefold() -> [{guid, path, suffix}]. | see source |
| `extract_typed_references` | `fluentcontrol_inventory.py` | `(payload)` | see source | see source |
| `report_missing_system_dependencies` | `fluentcontrol_inventory.py` | `(payload)` | Report referenced workspaces/LCs/components missing from SystemSpecific + base. | see source |
| `strip_fluent_instance_suffix` | `fluent_naming.py` (re-exported from `labware_catalog_export.py`) | `(value)` | Strip Fluent instance suffixes such as ``[001]`` or ``[platecount]``. | see source |
| `build_labware_catalog_from_geometry` | `labware_catalog_export.py` | `(geometry)` | Convert ``manifest['worktable_geometry']`` into a portable catalog JSON. | see source |
| `write_labware_catalog` | `labware_catalog_export.py` | `(destination, geometry)` | Write ``labware_catalog.json`` from geometry and/or Components ``*.xcmp`` walk. | see source |
| `write_labware_catalog_for_context` | `labware_catalog_export.py` | `(context_root, geometry)` | Write the catalog next to ``manifest.json`` under a project context root. | see source |
| `build_labware_catalog_for_package` | `labware_catalog_export.py` | `(geometry)` | Prefer geometry-mined catalog; fall back to Components ``*.xcmp`` for large ZEIA. | see source |
| `build_labware_catalog_from_datastore` | `labware_catalog_export.py` | `(datastore_root)` | Walk ``Components/*.xcmp`` (+ Sites for site dims) when detailed geometry was skipped. | see source |
| `resolve_worktable_datastore` | `worktable_datastore.py` (re-exported via `labware_catalog_export.py`) | `(path)` | see source | see source |
| `discover_worktable_datastore` | `worktable_datastore.py` (re-exported via `labware_catalog_export.py`) | `(context_root)` | see source | see source |
| `alias_maps_from_labware_catalog` | `labware_catalog_export.py` | `(catalog)` | Derive labware/catalog alias maps from a ZEIA-built catalog (instance → type). | see source |
| `load_labware_catalog` | `labware_catalog_export.py` | `(path)` | see source | see source |
| `_grip_payload (priv)` | `labware_catalog_export.py` | `(component, arrangement)` | see source | see source |
| `_compatible_component_rows (priv)` | `labware_catalog_export.py` | `()` | Emit guid/name refs without inventing guid↔name pairings. | see source |
| `build_script_folder_bindings` | `script_folder_bindings_export.py` | `(manifest)` | Mine folder tree + script worktable bindings from a project manifest. | see source |
| `attach_script_folder_bindings` | `script_folder_bindings_export.py` | `(manifest)` | Attach a compact binding summary onto the in-memory manifest for init preference. | see source |
| `write_script_folder_bindings` | `script_folder_bindings_export.py` | `(destination, manifest)` | see source | see source |
| `write_script_folder_bindings_for_context` | `script_folder_bindings_export.py` | `(context_root, manifest)` | see source | see source |
| `load_script_folder_bindings` | `script_folder_bindings_export.py` | `(path)` | see source | see source |
| `zeia_worktable_bindings_from_manifest` | `script_folder_bindings_export.py` | `(manifest)` | Return exported script→worktable bindings when present on the manifest. | see source |
| `_normalize_folder (priv)` | `script_folder_bindings_export.py` | `(value)` | see source | see source |
| `build_worktable_geometry` | `worktable_geometry.py` | `(manifest)` | Return parsed worktable geometry from a project or collection manifest. | see source |
| `parse_connector` | `worktable_geometry.py` | `(path)` | see source | see source |
| `parse_site` | `worktable_geometry.py` | `(path)` | see source | see source |
| `parse_component` | `worktable_geometry.py` | `(path)` | see source | see source |
| `parse_workspace` | `worktable_geometry.py` | `(path)` | see source | see source |
| `workspace_labware_records` | `worktable_geometry.py` | `(workspace)` | Convert workspace placements into source labware records for diffs. | see source |
| `_decorate_compatible_components (priv)` | `worktable_geometry.py` | `(components)` | Fold workspace occupancy into parent compatible-component lists. | see source |
| `_parse_xml (priv)` | `worktable_geometry.py` | `(path)` | see source | see source |
| `_mesh_references (priv)` | `worktable_geometry.py` | `(payload)` | Return (mesh_guids, mesh_names) from WorktableMesh references on a component. | see source |
| `classify_site_kind` | `worktable_geometry.py` | `(location_group, type_name, object_name)` | Classify xsit sites for pin_sites / nest-cap mining.  Mines WorktablePin* **and** CapHolder / *_Cap_ | see source |
| `_looks_like_pin_name (priv)` | `worktable_geometry.py` | `(value)` | Backward-compatible alias , now includes nest/cap TypeName families. | see source |
| `_normalize_windows_path (priv)` | `zeia_filesystem.py` | `(value)` | see source | see source |
| `parse_fs_mapping_directories` | `zeia_filesystem.py` | `(data)` | Parse ``fs/mapping.xml`` into ``(key, directory_path)`` pairs. | see source |
| `archive_fs_path_to_content_entry` | `zeia_filesystem.py` | `(archive_path)` | Convert a packaged ``fs/`` zip member to a ``content.xml`` Entry value. | see source |
| `content_entry_to_archive_fs_path` | `zeia_filesystem.py` | `(content_entry)` | Convert a ``content.xml`` FilesystemEntries Entry value to a packaged ``fs/`` path. | see source |
| `_build_filesystem_entries_block (priv)` | `zeia_filesystem.py` | `(entry_names)` | see source | see source |
| `_parse_content_filesystem_entries (priv)` | `zeia_filesystem.py` | `(content_xml_bytes)` | see source | see source |
| `update_archive_content_filesystem` | `zeia_filesystem.py` | `(content_xml_bytes, filesystem_entry_names)` | Insert or replace ``<FilesystemEntries>`` in ``meta/content.xml`` and restamp checksum. | see source |
| `build_fs_mapping_xml` | `zeia_filesystem.py` | `(directories)` | Build ``fs/mapping.xml`` bytes with a valid ``DirectoryMappings`` checksum. | see source |
| `FsEmbedFile` | `zeia_filesystem.py` | class | class | , |
| `FsEmbedPlan` | `zeia_filesystem.py` | class | class | , |
| `_resolve_media_source (priv)` | `zeia_filesystem.py` | `(media_dir, basename)` | see source | see source |
| `_assign_directory_keys (priv)` | `zeia_filesystem.py` | `(requested)` | Assign stable fs keys for directory paths, reusing existing mappings when possible. | see source |
| `plan_fs_embed` | `zeia_filesystem.py` | `()` | Plan ``fs/`` payload files and directory mappings for one packaged ZEIA. | see source |
| `embed_filesystem_in_archive` | `zeia_filesystem.py` | `(archive_path, plan)` | Merge ``fs/`` files and ``fs/mapping.xml`` into an existing ZEIA zip. | see source |
| `copy_referenced_filesystem_from_archives` | `zeia_filesystem.py` | `(source_archives, destination_archive)` | Copy the file closure required by every XSCR shipped in the destination. | see source |
| `collect_archive_file_reference_paths` | `zeia_filesystem.py` | `(archive_path)` | Collect the union of file references from every XSCR shipped in a ZEIA. | see source |
| `extract_archive_filesystem_payloads` | `zeia_filesystem.py` | `(archive_path, destination_dir)` | Extract packaged ``fs/{key}/file`` payloads for the V2 delivery media folder. | see source |
| `collect_file_reference_paths` | `zeia_filesystem.py` | `(media_path_map, ir, external_report)` | Collect absolute paths that should appear as script ``FileReference`` entries. | see source |
| `ensure_script_file_references` | `zeia_filesystem.py` | `(xscr_path, paths)` | Inject missing ``<FileReference><File>`` blocks before ``<PayloadData>``. | see source |
| `strip_orphan_touchtools_media_file_references` | `zeia_filesystem.py` | `(xscr_path)` | Drop TouchTools media FileReferences not used by prompt image/sound fields.  Regeneration that repla | see source |
| `repair_archive_content_filesystem` | `zeia_filesystem.py` | `(archive_path)` | Patch ``meta/content.xml`` FilesystemEntries from existing ``fs/`` zip members. | see source |
| `audit_archive_filesystem` | `zeia_filesystem.py` | `(archive_data)` | Return blocking findings when mapped file refs lack ``fs/{key}/`` payloads. | see source |
