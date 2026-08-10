# Functions: fluent-pipeline-project

Source roots: `fluent_pipeline/` (12 files)

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `archive_reference_fingerprint` | `archive_cache.py` | `(source_project)` | Return a stable ``(size + content)`` fingerprint for a source ZEIA.  Returns ``None`` if the file ca | see source |
| `load_records` | `archive_cache.py` | `(fingerprint, kind)` | Return cached records of ``kind`` for ``fingerprint``, or ``None``. | see source |
| `store_records` | `archive_cache.py` | `(fingerprint, kind, records)` | Persist records of ``kind`` for ``fingerprint``. Best effort; never raises. | see source |
| `normalize_agent_brief_mode` | `agent_brief.py` | `(mode)` | Normalize one requested brief mode to the supported mode vocabulary. | Pure. |
| `resolve_agent_brief_mode` | `agent_brief.py` | `(intent, *, default="status")` | Select an agent-brief mode from plain-language intent. | Pure; returns confidence and rationale. |
| `render_agent_brief` | `agent_brief.py` | `(mode)` | Render the compact checklist for one normalized workflow mode. | Raises on unsupported modes. |
| `AuditImportEvent` | `audit_import_context.py` | class | class | , |
| `read_audit_import_events` | `audit_import_context.py` | `(paths)` | Return imports with one unambiguous likely main-script name per event. | see source |
| `import_for_error` | `audit_import_context.py` | `(timestamp, events)` | Return the most recent audit import preceding an error within the causal window. | see source |
| `WorkspacePackage` | `bootstrap.py` | class | One local package in the canonical install graph. | , |
| `WorkspacePackage.editable_requirement` | `bootstrap.py` | `()` | see source | see source |
| `WorkspacePackage.wheel_requirement` | `bootstrap.py` | `()` | see source | see source |
| `workspace_packages` | `bootstrap.py` | `()` | Return the canonical local install graph. | see source |
| `bootstrap_workspace` | `bootstrap.py` | `(python)` | Bootstrap the shared repo venv using the canonical install graph. | see source |
| `upgrade_pip` | `bootstrap.py` | `(python)` | see source | see source |
| `install_editable_workspace` | `bootstrap.py` | `(python)` | see source | see source |
| `build_workspace_wheelhouse` | `bootstrap.py` | `(python, wheelhouse)` | Build workspace wheels without leaving setuptools ``build/`` trees in sources. | see source |
| `_stage_wheel_source (priv)` | `bootstrap.py` | `(source, stage_root)` | Copy one local package to disposable OS temp space for PEP 517 builds. | see source |
| `install_workspace_from_wheelhouse` | `bootstrap.py` | `(python, wheelhouse)` | see source | see source |
| `install_desktop_automation_dependencies` | `bootstrap.py` | `(python)` | see source | see source |
| `run_pip_check` | `bootstrap.py` | `(python)` | see source | see source |
| `ensure_desktop_automation_manifests` | `bootstrap.py` | `()` | see source | see source |
| `main` | `bootstrap.py` | `(argv)` | see source | see source |
| `next_bootstrap_step` | `bootstrap_status.py` | `(*, doctor_ok, projects, inspected=False)` | Compute the fail-closed next bootstrap action and tool allow/block lists. | Pure. |
| `build_bootstrap_status` | `bootstrap_status.py` | `(*, install_missing=False, confirm_install=False, write_report=True, inspected=False)` | Build the stable bootstrap status payload shared by CLI and MCP. | Runs doctor/project inventory and may write the bootstrap report. |
| `_requirements_text (priv)` | `bootstrap.py` | `()` | see source | see source |
| `_validate_workspace_package_order (priv)` | `bootstrap.py` | `(packages)` | see source | see source |
| `_write_text_if_changed (priv)` | `bootstrap.py` | `(path, text)` | see source | see source |
| `_requirement_spec (priv)` | `bootstrap.py` | `(base, extras)` | see source | see source |
| `fluentcoder_root` | `config.py` | `()` | Return the fluentcoder repo root, honoring FLUENTCODER_ROOT if present. | see source |
| `fluentcoder_python` | `config.py` | `(root)` | Return the shared repo-level Python executable for fluentcoder commands. | see source |
| `ensure_logs_dir` | `config.py` | `()` | Create the workspace log directory when tooling needs to write a log file. | see source |
| `workspace_log_path` | `config.py` | `(name)` | Return a log path under ``ready-to-import/_shared/temp_files/logs/``. | see source |
| `workflow_event_log_path` | `config.py` | `(label)` | Default JSONL event-log path for a generation run. | see source |
| `resolve_user_path` | `config.py` | `(value)` | Resolve a CLI path relative to the caller's current working directory. | see source |
| `obsidian_vault_root` | `config.py` | `()` | Best-effort Obsidian vault root (directory containing ``.obsidian`` or ``Home.md``). | see source |
| `discover_vault_root_zeia` | `config.py` | `()` | Return the newest ``*.zeia`` in the Obsidian vault root, if any. | see source |
| `build_import_options` | `import_identity.py` | `(snapshot_archives)` | Return the import options that should influence cache reuse. | see source |
| `sha256_json` | `import_identity.py` | `(payload)` | Return the SHA-256 digest of a canonical JSON serialization. | see source |
| `build_source_import_identity` | `import_identity.py` | `(source_archive, snapshot_archives)` | Return the composite cache identity for an imported ZEIA project. | see source |
| `inspect_zeia_archive` | `project_archive_inspection.py` | `(archive)` | Inspect a ZEIA archive and return a project-style manifest.  The returned manifest is intended for r | see source |
| `project_datastore_dir` | `project_catalog.py` | `(context)` | Return the normalized DataStore root for a project context, if present. | see source |
| `project_catalog_db_path` | `project_catalog.py` | `(context)` | see source | see source |
| `ensure_project_catalog` | `project_catalog.py` | `(context)` | Build and return a project-local fluentcoder catalog DB when possible.  The build is content-address | see source |
| `_catalog_source_fingerprint (priv)` | `project_catalog.py` | `(datastore)` | Return a cheap stat-only fingerprint for catalog source files.  Re-importing the same ZEIA resets mt | see source |
| `_load_catalog_sidecar (priv)` | `project_catalog.py` | `(db_dir)` | see source | see source |
| `_write_catalog_sidecar (priv)` | `project_catalog.py` | `(db_dir)` | Persist the last resolved catalog hash + cheap source fingerprint. | see source |
| `_resolve_catalog_hash (priv)` | `project_catalog.py` | `(datastore, db_dir)` | Resolve the catalog content hash, skipping full reads when the sidecar matches. | see source |
| `_catalog_content_hash (priv)` | `project_catalog.py` | `(datastore)` | Hash the catalog inputs by content so identical inputs share a cache key.  Uses relative path + size | see source |
| `_store_in_shared_cache (priv)` | `project_catalog.py` | `(db_path, cached_db)` | Copy a freshly built DB into the shared cache atomically. Best effort. | see source |
| `_mark_fresh (priv)` | `project_catalog.py` | `(db_path)` | Bump the DB mtime above the (freshly extracted) source mtimes.  A cached DB copied in via copy2 keep | see source |
| `ProjectContext` | `project_context.py` | class | class | , |
| `ProjectContext.extracted_dir` | `project_context.py` | `()` | see source | see source |
| `ProjectContext.artifacts_root` | `project_context.py` | `()` | Return the complete workspace for this imported source context. | see source |
| `ProjectContext.drafts_dir` | `project_context.py` | `()` | see source | see source |
| `ProjectContext.build_dir` | `project_context.py` | `()` | see source | see source |
| `ProjectContext.reports_dir` | `project_context.py` | `()` | see source | see source |
| `ProjectContext.roundtrips_dir` | `project_context.py` | `()` | see source | see source |
| `ProjectCollection` | `project_context.py` | class | class | , |
| `ProjectCollection.extracted_dir` | `project_context.py` | `()` | see source | see source |
| `ProjectCollection.artifacts_root` | `project_context.py` | `()` | Return the complete workspace for this source collection. | see source |
| `ProjectCollection.drafts_dir` | `project_context.py` | `()` | see source | see source |
| `ProjectCollection.build_dir` | `project_context.py` | `()` | see source | see source |
| `ProjectCollection.reports_dir` | `project_context.py` | `()` | see source | see source |
| `ProjectCollection.roundtrips_dir` | `project_context.py` | `()` | see source | see source |
| `sanitize_project_name` | `project_context.py` | `(raw, fallback)` | see source | see source |
| `project_dir` | `project_context.py` | `(name)` | see source | see source |
| `collection_dir` | `project_context.py` | `(name)` | see source | see source |
| `manifest_path` | `project_context.py` | `(name)` | see source | see source |
| `collection_manifest_path` | `project_context.py` | `(name)` | see source | see source |
| `_load_existing_project_manifest (priv)` | `project_context.py` | `(root)` | see source | see source |
| `import_project` | `project_context.py` | `(archive)` | see source | see source |
| `load_project` | `project_context.py` | `(name)` | see source | see source |
| `create_project_collection` | `project_context.py` | `(name, project_names)` | Create a persistent generation collection from imported project contexts. | see source |
| `load_project_collection` | `project_context.py` | `(name)` | see source | see source |
| `list_project_collections` | `project_context.py` | `()` | see source | see source |
| `list_projects` | `project_context.py` | `()` | see source | see source |
| `set_active_project` | `project_context.py` | `(name)` | see source | see source |
| `clear_active_project` | `project_context.py` | `()` | see source | see source |
| `active_project_name` | `project_context.py` | `()` | see source | see source |
| `resolve_context_path` | `project_context.py` | `(ctx, value)` | see source | see source |
| `resolve_context_script` | `project_context.py` | `(ctx, value)` | see source | see source |
| `find_in_project` | `project_context.py` | `(ctx, pattern)` | see source | see source |
| `build_manifest` | `project_context.py` | `()` | see source | see source |
| `build_collection_manifest` | `project_context.py` | `()` | see source | see source |
| `_merge_context_worktable_geometry (priv)` | `project_context.py` | `(contexts)` | Reuse imported per-context geometry instead of reparsing every collection XML file. | see source |
| `_skipped_large_export_geometry (priv)` | `project_context.py` | `(workspaces)` | see source | see source |
| `assess_full_zeia_export` | `project_context.py` | `(manifest)` | Conservatively assess whether an imported project looks like a full ZEIA export. | see source |
| `_ref_resolves (priv)` | `project_context.py` | `(name, guid, object_names, object_guids)` | see source | see source |
| `_has_dependency_rich_full_export_evidence (priv)` | `project_context.py` | `(manifest)` | see source | see source |
| `_append_full_zeia_export_report (priv)` | `project_context.py` | `(lines, assessment)` | see source | see source |
| `render_project_report` | `project_context.py` | `(manifest)` | see source | see source |
| `render_project_collection_report` | `project_context.py` | `(manifest)` | see source | see source |
| `_validate_collection_manifest (priv)` | `project_context.py` | `(manifest)` | Validate collection counts and identities with O(n) indexed lookups. | see source |
| `_script_resolved_path (priv)` | `project_context.py` | `(ctx, script)` | see source | see source |
| `resolve_recorded_script_path` | `project_context.py` | `(record)` | Resolve a manifest/IR script record to an on-disk `.xscr` when possible. | see source |
| `filter_generation_source_script_records` | `project_context.py` | `(records, protocol_ir)` | Prefer request-selected source scripts over same-name regeneration baselines. | see source |
| `subroutine_simulate_cli_args` | `project_context.py` | `(ctx)` | Return fluentcoder simulate CLI args that register project subroutines. | see source |
| `_write_manifest (priv)` | `project_context.py` | `(root, manifest)` | see source | see source |
| `is_context_archive` | `project_context.py` | `(path)` | Return true if a ZIP-like file looks useful as a project/snapshot context. | see source |
| `_archive_has_importable_context (priv)` | `project_context.py` | `(entries)` | see source | see source |
| `safe_extract_archive` | `project_context.py` | `(zf, destination)` | Extract a validated ZIP archive while rejecting path traversal entries. | see source |
| `_safe_extract (priv)` | `project_context.py` | `(zf, destination)` | Compatibility wrapper for older internal import call sites. | see source |
| `_import_snapshot_archives (priv)` | `project_context.py` | `(snapshots)` | see source | see source |
| `_parse_xml (priv)` | `project_context.py` | `(path)` | see source | see source |
| `_custom_part_summary_payload (priv)` | `project_context.py` | `(counters, pin_refs, asset_refs)` | see source | see source |
| `_normalize_field_name (priv)` | `project_context.py` | `(value)` | see source | see source |
| `ProjectStore` | `project_store.py` | class | Persist manifests, reports, and active-context selection atomically. | , |
| `ProjectStore.write_json` | `project_store.py` | `(path, payload)` | see source | see source |
| `ProjectStore.read_json` | `project_store.py` | `(path)` | see source | see source |
| `ProjectStore.write_text` | `project_store.py` | `(path, value)` | see source | see source |
| `ProjectStore.set_active_context` | `project_store.py` | `(name)` | see source | see source |
| `ProjectStore.clear_active_context` | `project_store.py` | `()` | see source | see source |
| `ProjectStore.active_context_name` | `project_store.py` | `()` | see source | see source |
| `_exclusive_lock (priv)` | `project_store.py` | `(path)` | Serialize writes to one project artifact across local processes. | see source |
| `PipelineError` | `runner.py` | class | Raised when the local fluentcoder environment cannot be used. | , |
| `CommandResult` | `runner.py` | class | class | , |
| `CommandResult.ok` | `runner.py` | `()` | see source | see source |
| `CommandResult.command_line` | `runner.py` | `()` | see source | see source |
| `LogWatchResult` | `runner.py` | class | class | , |
| `ensure_parent` | `runner.py` | `(path)` | see source | see source |
| `_validate_environment (priv)` | `runner.py` | `()` | see source | see source |
| `run_python` | `runner.py` | `(arguments)` | see source | see source |
| `run_fluentcoder` | `runner.py` | `(arguments)` | see source | see source |
| `run_fluentcoder_with_log_watch` | `runner.py` | `(arguments)` | Run fluentcoder while capturing lines appended to a FluentControl/log file. | see source |
| `parse_json_stdout` | `runner.py` | `(result)` | see source | see source |
| `write_json` | `runner.py` | `(path, data)` | see source | see source |
