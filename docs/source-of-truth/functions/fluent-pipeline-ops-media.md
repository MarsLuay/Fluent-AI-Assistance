# Functions: fluent-pipeline-ops-media

Source roots: `fluent_pipeline/` (13 files)

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `xml_root_name` | `checksum.py` | `(data)` | Return the local name of the first XML element in ``data`` (no prefix). | see source |
| `_blank_checksum (priv)` | `checksum.py` | `(data)` | Return ``data`` with the first ``<Checksum>`` element emptied. | see source |
| `_payload_message (priv)` | `checksum.py` | `(data)` | Return the canonical bytes hashed for ``data``'s checksum, or ``None``.  SHA-256 metadata roots hash | see source |
| `compute_checksum` | `checksum.py` | `(data)` | Compute the FluentControl ``<Checksum>`` value for one entry's bytes.  ``data`` is the full datastor | see source |
| `fluentcontrol_md5` | `checksum.py` | `(message)` | Return the FluentControl-required MD5 digest in non-security mode. | see source |
| `stamp_checksum` | `checksum.py` | `(data)` | Return ``data`` with its ``<Checksum>`` element set to the computed value.  Returns ``None`` when th | see source |
| `verify_self` | `checksum.py` | `()` | Return ``True`` when every embedded known-good fixture reproduces exactly.  This is the trust gate f | see source |
| `_ChecksumBackend` | `checksums.py` | class | class | , |
| `_bridge (priv)` | `checksums.py` | `()` | Return a verified checksum backend, or ``None`` when absent. | see source |
| `_backend_from_vendored_pure_python (priv)` | `checksums.py` | `()` | Vendored pure-Python checksum backend (offline default).  Uses :mod:`fluent_pipeline.checksum`, whic | see source |
| `_backend_from_tecan_install (priv)` | `checksums.py` | `()` | Use FluentControl's installed XML checksum handler when available. | see source |
| `_backend_from_clr_env (priv)` | `checksums.py` | `()` | Best-effort pythonnet hook for site-local CLR adapters.  The real FluentControl assembly surface is  | see source |
| `_rewrite_with_tempfile (priv)` | `checksums.py` | `(data, rewrite_in_place)` | see source | see source |
| `_rewrite_with_core (priv)` | `checksums.py` | `(data, rewrite_checksum)` | see source | see source |
| `checksum_bridge_available` | `checksums.py` | `()` | True when a checksum recompute backend (real or vendored) is available. | see source |
| `checksum_backend_name` | `checksums.py` | `()` | Return the active checksum backend's name, or ``None`` when absent. | see source |
| `checksum_backend_is_vendored` | `checksums.py` | `()` | True when the active backend is the vendored pure-Python implementation. | see source |
| `entry_checksum_state` | `checksums.py` | `(data)` | Classify the checksum element of a datastore entry.  Returns ``"valid"`` (checksum present and, for  | see source |
| `recompute_checksum_bytes` | `checksums.py` | `(data)` | Recompute the checksum for one entry's bytes using a verified backend.  Returns the rewritten bytes  | see source |
| `audit_archive_checksums` | `checksums.py` | `(archive_data)` | Summarize checksum state across archive entries.  When ``mutated_entries`` is provided, the audit fo | see source |
| `DiagnosticBundle` | `diagnostics.py` | class | class | , |
| `diagnose_input` | `diagnostics.py` | `(input_path)` | Diagnose a ZEIA archive or a single script/worklist input. | see source |
| `render_diagnostic_markdown` | `diagnostics.py` | `(report)` | see source | see source |
| `_write_diagnostic_artifacts (priv)` | `diagnostics.py` | `(report, out_dir)` | see source | see source |
| `_resolve_input_path (priv)` | `diagnostics.py` | `(ctx, source)` | see source | see source |
| `scan_fluent_dump_errors` | `dump_error_scan.py` | `(dump_root)` | Find known Script Editor error strings without copying multi-GB dump files. | see source |
| `main` | `dump_error_scan.py` | `(argv)` | see source | see source |
| `FluentLogRecord` | `fluent_log_parser.py` | class | class | , |
| `FluentLogRecord.as_dict` | `fluent_log_parser.py` | `()` | see source | see source |
| `FluentLogDiagnostic` | `fluent_log_parser.py` | class | class | , |
| `FluentLogDiagnostic.as_dict` | `fluent_log_parser.py` | `()` | see source | see source |
| `DiagnosticRule` | `fluent_log_parser.py` | class | class | , |
| `DiagnosticRule.matches` | `fluent_log_parser.py` | `(text, error_ids)` | see source | see source |
| `parse_fluent_log_file` | `fluent_log_parser.py` | `(path)` | Parse a FluentControl/VisionX log file into structured records. | see source |
| `parse_fluent_log_text` | `fluent_log_parser.py` | `(text)` | Parse line-oriented FluentControl logs while preserving nearby detail lines. | see source |
| `_records_with_audit_import_context (priv)` | `fluent_log_parser.py` | `(records, audit_events)` | Name otherwise-unattributed errors only when a recent audit import is unique. | see source |
| `_records_with_thread_scope (priv)` | `fluent_log_parser.py` | `(records)` | Carry Scope stack script names onto later errors on the same ThreadId. | see source |
| `_records_with_script_command_lines (priv)` | `fluent_log_parser.py` | `(records, command_index)` | Pin script_line when an error's command hint uniquely matches XSCR LineNumber metadata. | see source |
| `build_script_command_index` | `fluent_log_parser.py` | `(xscr_paths)` | Index XSCR command ids to FluentControl script LineNumber metadata. | see source |
| `diagnose_fluent_log_text` | `fluent_log_parser.py` | `(text)` | see source | see source |
| `diagnose_fluent_messages` | `fluent_log_parser.py` | `(messages)` | see source | see source |
| `diagnose_fluent_log_records` | `fluent_log_parser.py` | `(records)` | Map parsed log records to known protocol-builder workflow defects. | see source |
| `_enrich_move_axis_suggested_fix (priv)` | `fluent_log_parser.py` | `(base_fix)` | Append MoveAxis-related script names mined from log scope / imported XSCRs. | see source |
| `build_fluent_log_report` | `fluent_log_parser.py` | `(path)` | see source | see source |
| `discover_fluent_log_files` | `fluent_log_parser.py` | `()` | Return recent FluentControl/VisionX logs from the common install paths. | see source |
| `build_latest_fluent_log_report` | `fluent_log_parser.py` | `()` | Build diagnostics from the newest common FluentControl/VisionX log files. | see source |
| `render_fluent_log_report_markdown` | `fluent_log_parser.py` | `(report)` | see source | see source |
| `diagnostics_to_findings` | `fluent_log_parser.py` | `(diagnostics)` | see source | see source |
| `_parse_record_header (priv)` | `fluent_log_parser.py` | `(line)` | see source | see source |
| `_parse_scope_stack (priv)` | `fluent_log_parser.py` | `(text)` | see source | see source |
| `_record_with_nearby_script (priv)` | `fluent_log_parser.py` | `(records, index, record, script_candidates, script_candidate_timestamps)` | Attach a nearby named script to an error emitted by a child command. | see source |
| `_logical_log_lines (priv)` | `fluent_log_parser.py` | `(text)` | Split ULF/XML records even when the logging server wrote one huge line. | see source |
| `report_to_json` | `fluent_log_parser.py` | `(report)` | see source | see source |
| `write_placeholder_video_slot` | `media_convert.py` | `(dest)` | Write a plain centered-text GIF placeholder for a video media slot. | see source |
| `resolve_ffmpeg` | `media_convert.py` | `()` | see source | see source |
| `is_placeholder_video_gif` | `media_convert.py` | `(path)` | Return True when ``path`` is missing, empty, or still the bundled placeholder GIF. | see source |
| `find_dropped_video_source` | `media_convert.py` | `(media_dir, slot)` | Return a non-empty dropped video file for a slot, e.g. ``step_009_video.mp4``. | see source |
| `is_placeholder_image_slot` | `media_convert.py` | `(path)` | Return True when ``path`` is missing, empty, or still the bundled placeholder PNG. | see source |
| `worktable_fallback_video_slots` | `media_convert.py` | `(specs, media_dir)` | Video slots that will feed Worktable detail media.  Worktable prompts prefer the still-image slot. W | see source |
| `is_worktable_safe_gif` | `media_convert.py` | `(path)` | Heuristic check for instrument-stable Worktable GIF encoding. | see source |
| `convert_dropped_video_slots` | `media_convert.py` | `(media_dir, specs)` | Convert dropped video files into GIF slot files when the GIF is still a placeholder. | see source |
| `normalize_worktable_detail_gifs` | `media_convert.py` | `(media_dir, specs)` | Rewrite Worktable-bound GIF slots that are not already instrument-safe. | see source |
| `convert_image_to_png` | `media_convert.py` | `(source, dest)` | Convert a still capture into a PNG prompt media slot. | see source |
| `convert_video_to_gif` | `media_convert.py` | `(source, dest)` | Convert a video file to an animated GIF for a prompt media slot. | see source |
| `convert_video_to_worktable_gif` | `media_convert.py` | `(source, dest)` | Convert video into the instrument-validated Worktable GIF format.  Pipeline: ffmpeg portrait clip -> | see source |
| `normalize_worktable_gif` | `media_convert.py` | `(source, dest)` | Rewrite a GIF into the conservative format used for RUP Worktable prompts.  RUP Standard handles ani | see source |
| `ProgressEvent` | `progress.py` | class | class | , |
| `ProgressStage` | `progress.py` | class | class | , |
| `ProgressEmitter` | `progress.py` | class | Small stateful helper that keeps timing out of workflow call sites. | , |
| `ProgressEmitter.started` | `progress.py` | `(stage_id, message)` | see source | see source |
| `ProgressEmitter.running` | `progress.py` | `(stage_id, message)` | see source | see source |
| `ProgressEmitter.completed` | `progress.py` | `(stage_id, message)` | see source | see source |
| `ProgressEmitter.skipped` | `progress.py` | `(stage_id, message)` | see source | see source |
| `ProgressEmitter.warning` | `progress.py` | `(stage_id, message)` | see source | see source |
| `ProgressEmitter.failed` | `progress.py` | `(stage_id, message)` | see source | see source |
| `ProgressEmitter.failed_current` | `progress.py` | `(message)` | see source | see source |
| `ProgressEmitter.report` | `progress.py` | `(stage_id, status)` | Emit one event, including optional item-level progress. | see source |
| `ProgressEmitter.heartbeat` | `progress.py` | `(stage_id)` | Emit elapsed-time heartbeats while an uncountable operation runs. | see source |
| `render_plain_progress_event` | `progress.py` | `(event)` | see source | see source |
| `prompt_media_step_records` | `prompt_media.py` | `(ir)` | Map each visual/audio media file to its media and operator step labels. | see source |
| `ensure_compiled_prompt_media_references` | `prompt_media.py` | `(xscr_path, ir)` | Wire IR audio slots into matching compiled RUP Standard prompts. | see source |
| `_rup_standard_object_span_for_prompt (priv)` | `prompt_media.py` | `(text, prompt_xml)` | Return the exact RUP Standard ``Object`` containing ``prompt_xml``. | see source |
| `QueryVariableAudit` | `query_variable_audit.py` | class | class | , |
| `QueryVariableAudit.as_dict` | `query_variable_audit.py` | `()` | see source | see source |
| `normalize_query_variable_names` | `query_variable_audit.py` | `(raw)` | Coerce ``GetQueryVariableNames()`` output to a deduped sorted name tuple. | see source |
| `expected_query_names_from_ir` | `query_variable_audit.py` | `(protocol_ir)` | Collect startup-query variable names modeled in protocol IR. | see source |
| `expected_query_names_from_spec` | `query_variable_audit.py` | `(request_spec)` | Collect startup-query names seeded from request.spec simulation values. | see source |
| `build_query_variable_audit` | `query_variable_audit.py` | `()` | Diff live ``GetQueryVariableNames()`` output against IR and request spec. | see source |
| `live_query_names_from_fluent_report` | `query_variable_audit.py` | `(report)` | Extract ``GetQueryVariableNames()`` results from a Fluent runtime report. | see source |
| `audit_query_variables_for_workflow` | `query_variable_audit.py` | `()` | see source | see source |
| `render_query_variable_audit_markdown` | `query_variable_audit.py` | `(audit)` | see source | see source |
| `validation_diff_check_for_query_audit` | `query_variable_audit.py` | `(audit)` | see source | see source |
| `compact_simulation` | `reports.py` | `(data)` | see source | see source |
| `render_simulation_markdown` | `reports.py` | `(protocol, data, result)` | see source | see source |
| `render_roundtrip_markdown` | `reports.py` | `(source, stages)` | see source | see source |
| `render_compile_markdown` | `reports.py` | `(protocol, output, result)` | see source | see source |
| `render_doctor_markdown` | `reports.py` | `(checks)` | see source | see source |
| `FluentContextCheckConfig` | `runtime_bridge.py` | class | class | , |
| `run_fluent_context_check` | `runtime_bridge.py` | `(config)` | Return a deterministic offline report when no live provider is wired. | see source |
| `render_fluent_context_check_markdown` | `runtime_bridge.py` | `(report)` | Render a compact Markdown summary for the compatibility report. | see source |
| `RuntimeVariableAudit` | `runtime_variable_audit.py` | class | class | , |
| `RuntimeVariableAudit.as_dict` | `runtime_variable_audit.py` | `()` | see source | see source |
| `normalize_variable_names` | `runtime_variable_audit.py` | `(raw)` | Coerce ``GetVariableNames()`` output to a deduped sorted name tuple. | see source |
| `expected_variable_names_from_xscr` | `runtime_variable_audit.py` | `(xscr_path)` | Collect variable names declared in a compiled XSCR. | see source |
| `expected_variable_names_from_ir` | `runtime_variable_audit.py` | `(protocol_ir)` | Collect startup/method variable names modeled in protocol IR. | see source |
| `build_runtime_variable_audit` | `runtime_variable_audit.py` | `()` | Diff live ``GetVariableNames()`` output against offline XSCR/IR inventory. | see source |
| `live_variable_names_from_fluent_report` | `runtime_variable_audit.py` | `(report)` | Extract ``GetVariableNames()`` results from a Fluent runtime report. | see source |
| `live_query_names_from_fluent_report` | `runtime_variable_audit.py` | `(report)` | see source | see source |
| `audit_runtime_variables_for_workflow` | `runtime_variable_audit.py` | `()` | see source | see source |
| `render_runtime_variable_audit_markdown` | `runtime_variable_audit.py` | `(audit)` | Markdown section for ``validation_diff.md`` runtime variable audit output. | see source |
| `render_fluent_variables_cli_output` | `runtime_variable_audit.py` | `(audit)` | Human-readable runtime variable inventory report (legacy CLI helper). | see source |
| `validation_diff_check_for_runtime_audit` | `runtime_variable_audit.py` | `(audit)` | see source | see source |
| `SceneArtifactSpec` | `simulator_scene.py` | class | class | , |
| `write_simulator_handoff` | `simulator_scene.py` | `(out_dir)` | Write ``sim_scene.json`` and ``simulator-project.json`` into ``out_dir``. | see source |
| `_build_generation_block (priv)` | `simulator_scene.py` | `()` | see source | see source |
| `_normalize_gate (priv)` | `simulator_scene.py` | `(gate)` | see source | see source |
| `_build_sim_scene_payload (priv)` | `simulator_scene.py` | `()` | see source | see source |
| `_build_simulator_project_payload (priv)` | `simulator_scene.py` | `()` | see source | see source |
| `_embedded_normalization_roots (priv)` | `simulator_scene.py` | `(path)` | Absolute path roots to collapse inside embedded artifact text. | see source |
