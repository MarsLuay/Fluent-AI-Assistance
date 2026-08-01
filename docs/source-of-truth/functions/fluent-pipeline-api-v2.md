# Functions: fluent-pipeline-api-v2

Source roots: `fluent_pipeline/` (35 files)

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `is_verification_recipe_ir` | `api_v2/add_labware_golden.py` | `(ir)` | see source | see source |
| `verification_recipe_add_labware_steps` | `api_v2/add_labware_golden.py` | `(ir)` | Return ordered ``add_labware`` IR steps for a verification_recipe protocol. | see source |
| `add_labware_payload_from_element` | `api_v2/add_labware_golden.py` | `(element)` | see source | see source |
| `add_labware_payload_from_command` | `api_v2/add_labware_golden.py` | `(command)` | see source | see source |
| `compare_add_labware_payloads` | `api_v2/add_labware_golden.py` | `(expected, actual)` | see source | see source |
| `compare_verification_recipe_add_labware_golden` | `api_v2/add_labware_golden.py` | `(ir)` | Diff each verification_recipe ``add_labware`` step against compiled XSCR XML. | see source |
| `verification_recipe_add_labware_summary` | `api_v2/add_labware_golden.py` | `(findings)` | Roll up verification_recipe AddLabware golden diff for Gate 11/12 details. | see source |
| `enrich_compiled_inventory_with_golden_compare` | `api_v2/add_labware_golden.py` | `(inventory)` | Attach fc_native_xml_compare and verification_recipe AddLabware summaries. | see source |
| `CommonErrorDialogJournal` | `api_v2/ced.py` | class | Records every ``CommonErrorDialog`` / CEDNotification decision. | , |
| `CommonErrorDialogJournal.record` | `api_v2/ced.py` | `(ced_info, result)` | see source | see source |
| `CommonErrorDialogJournal.as_dict` | `api_v2/ced.py` | `()` | see source | see source |
| `CEDNotificationHandler` | `api_v2/ced.py` | class | Python stand-in for ``Tecan.VisionX.API.V2.CEDNotification.Invoke``. | , |
| `CEDNotificationHandler.invoke` | `api_v2/ced.py` | `(ced_info, button_index)` | Handle ``CommonErrorDialog`` and write the chosen ``buttonIndex``. | see source |
| `handle_common_error_dialog` | `api_v2/ced.py` | `(ced_info)` | Select a safe button index for unattended Gate 27 checks. | see source |
| `ced_info_from_text` | `api_v2/ced.py` | `(text)` | Build ``ICedInfo`` from flattened pywinauto window text. | see source |
| `ced_info_from_window` | `api_v2/ced.py` | `(window)` | Build ``ICedInfo`` from a pywinauto ``dump_relevant_windows`` record. | see source |
| `extract_buttons_from_window_children` | `api_v2/ced.py` | `(children)` | see source | see source |
| `ced_info_from_dotnet` | `api_v2/ced.py` | `(ced_info_obj)` | Map a live ``ICedInfo`` COM/pythonnet object into the offline dataclass. | see source |
| `write_button_index` | `api_v2/ced.py` | `(button_index, value)` | Write ``ref Int32 buttonIndex`` for pythonnet/list/single-value stubs. | see source |
| `merge_ced_journal_into_report` | `api_v2/ced.py` | `(report, journal)` | see source | see source |
| `subscribe_common_error_dialog` | `api_v2/ced.py` | `(runtime_events, handler)` | Subscribe ``CommonErrorDialog`` when the runtime exposes event hooks. | see source |
| `user_prompt_summary` | `api_v2/command_summary.py` | `(text)` | Canonical prompt text for prompt_text_quality and prompt_coverage matching (UserPrompt.ToString pari | see source |
| `ir_step_for_subroutine_call` | `api_v2/command_summary.py` | `(ir, call)` | Locate the IR ``call_subroutine`` step backing a subroutine_load_review record. | see source |
| `subroutine_call_summary` | `api_v2/command_summary.py` | `(step)` | FC-native ``Subroutine.ToString()`` label for subroutine_load_review traces. | see source |
| `subroutine_call_label` | `api_v2/command_summary.py` | `(ir, call)` | Build a Subroutine call label from an IR step when available. | see source |
| `enrich_subroutine_load_review_record` | `api_v2/command_summary.py` | `(record, ir)` | Attach ``call_label`` to a subroutine_load_review finding record (api-v2-087). | see source |
| `subroutine_path_from_opaque_message` | `api_v2/command_summary.py` | `(message)` | Best-effort subroutine path extraction from simulator opaque-call text. | see source |
| `enrich_simulation_subroutine_traces` | `api_v2/command_summary.py` | `(data, ir)` | Attach ``call_label`` to opaque subroutine simulation steps/events for subroutine_load_review (api-v | see source |
| `transfer_labware_summary` | `api_v2/command_summary.py` | `(params)` | Human-readable TransferLabware summary for logs and XSCR diffs. | see source |
| `format_remove_labware_trace` | `api_v2/command_tracing.py` | `()` | api-v2-079: readable RemoveLabware label for ExecuteCommand traces. | see source |
| `format_set_location_trace` | `api_v2/command_tracing.py` | `()` | api-v2-086: readable SetLocation label for stepped runner / event-log traces. | see source |
| `format_subroutine_call_trace` | `api_v2/command_tracing.py` | `()` | api-v2-087: FC-native subroutine call label for subroutine_load_review traces. | see source |
| `trace_execution_command` | `api_v2/command_tracing.py` | `(command_type)` | Build a structured trace record for runtime-report / event-log payloads. | see source |
| `append_command_trace` | `api_v2/command_tracing.py` | `(details, trace_record)` | Append a command trace into ``details['command_traces']`` (bounded list). | see source |
| `stepped_command_trace` | `api_v2/command_tracing.py` | `(command)` | Return ``ICommand.ToString()``-style text for a stepped-runner command (api-v2-079). | see source |
| `_SteppedCommandLike` | `api_v2/command_tracing.py` | class | class | , |
| `set_location_trace_for_stepped_command` | `api_v2/command_tracing.py` | `(command)` | Return FC-native SetLocation ``ToString()`` label for deck placement steps (api-v2-086). | see source |
| `merge_set_location_traces_into_details` | `api_v2/command_tracing.py` | `(details, command_log)` | Aggregate SetLocation traces from stepped ``command_log`` (api-v2-086). | see source |
| `merge_remove_labware_traces_into_details` | `api_v2/command_tracing.py` | `(details, command_log)` | Aggregate RemoveLabware traces from stepped ``command_log`` (api-v2-079). | see source |
| `command_trace_for_stepped` | `api_v2/command_tracing.py` | `(command)` | Best-effort ``ICommand.ToString()`` label for stepped runner / event-log (079/086). | see source |
| `enrich_stepped_log_entry` | `api_v2/command_tracing.py` | `(log_entry, command)` | Attach ``trace`` / ``ir_step_id`` to a stepped-runner log row (079/086). | see source |
| `CompiledCommandRecord` | `api_v2/command_validate.py` | class | class | , |
| `CompiledCommandRecord.as_dict` | `api_v2/command_validate.py` | `()` | see source | see source |
| `CommandValidationFailure` | `api_v2/command_validate.py` | class | class | , |
| `CommandValidationFailure.as_dict` | `api_v2/command_validate.py` | `()` | see source | see source |
| `CommandValidationFailure.as_finding` | `api_v2/command_validate.py` | `()` | see source | see source |
| `CommandValidationReport` | `api_v2/command_validate.py` | class | class | , |
| `CommandValidationReport.as_dict` | `api_v2/command_validate.py` | `()` | see source | see source |
| `CommandValidationReport.fluentcontrol_findings` | `api_v2/command_validate.py` | `()` | see source | see source |
| `CommandValidateProvider` | `api_v2/command_validate.py` | class | class | , |
| `CommandValidateProvider.validate_compiled_xscr` | `api_v2/command_validate.py` | `(path)` | see source | see source |
| `OfflineCommandValidateProvider` | `api_v2/command_validate.py` | class | class | , |
| `OfflineCommandValidateProvider.validate_compiled_xscr` | `api_v2/command_validate.py` | `(path)` | see source | see source |
| `NativeApiV2CommandValidateProvider` | `api_v2/command_validate.py` | class | class | , |
| `NativeApiV2CommandValidateProvider.validate_compiled_xscr` | `api_v2/command_validate.py` | `(path)` | see source | see source |
| `default_command_validate_provider` | `api_v2/command_validate.py` | `()` | see source | see source |
| `validate_compiled_xscr_commands` | `api_v2/command_validate.py` | `(path)` | see source | see source |
| `validate_compiled_commands` | `api_v2/command_validate.py` | `(root, text)` | see source | see source |
| `validate_typed_commands` | `api_v2/command_validate.py` | `(records)` | see source | see source |
| `validate_script_level_commands` | `api_v2/command_validate.py` | `(text)` | see source | see source |
| `normalize_configure_data_labware_boolean_casing` | `api_v2/command_validate.py` | `(text)` | see source | see source |
| `validate_cross_command_heuristics` | `api_v2/command_validate.py` | `(root, commands)` | see source | see source |
| `_validate_add_labware_cross_command (priv)` | `api_v2/command_validate.py` | `(root, commands)` | Sequential duplicate label/slot checks (field rules run in validate_typed_commands). | see source |
| `ICommand` | `api_v2/commands.py` | class | class | , |
| `ICommand.to_xml` | `api_v2/commands.py` | `()` | see source | see source |
| `ICommand.validate` | `api_v2/commands.py` | `()` | see source | see source |
| `ICommand.to_string` | `api_v2/commands.py` | `()` | see source | see source |
| `_normalize_prompt_text (priv)` | `api_v2/commands.py` | `(prompt)` | see source | see source |
| `_require_non_empty (priv)` | `api_v2/commands.py` | `(label, value)` | see source | see source |
| `SetLocation` | `api_v2/commands.py` | class | api-v2-038 / api-v2-039. | , |
| `SetLocation.to_string` | `api_v2/commands.py` | `()` | see source | see source |
| `SetLocation.validate` | `api_v2/commands.py` | `()` | see source | see source |
| `SetLocation.to_xml` | `api_v2/commands.py` | `()` | see source | see source |
| `VariableMapping` | `api_v2/commands.py` | class | class | , |
| `VariableMapping.to_xml` | `api_v2/commands.py` | `()` | see source | see source |
| `Subroutine` | `api_v2/commands.py` | class | api-v2-040 / api-v2-041. | , |
| `Subroutine.to_string` | `api_v2/commands.py` | `()` | see source | see source |
| `Subroutine.validate` | `api_v2/commands.py` | `()` | see source | see source |
| `Subroutine.to_xml` | `api_v2/commands.py` | `()` | see source | see source |
| `TransferLabware` | `api_v2/commands.py` | class | api-v2-043 / api-v2-044. | , |
| `TransferLabware.to_string` | `api_v2/commands.py` | `()` | see source | see source |
| `TransferLabware.validate` | `api_v2/commands.py` | `()` | see source | see source |
| `TransferLabware.parameters_xml` | `api_v2/commands.py` | `()` | Inner TransferLabwareCommandParameters XML (single-escaped). | see source |
| `TransferLabware.to_execution_settings` | `api_v2/commands.py` | `()` | Double-escaped payload for ApplicationDriverMacro ExecutionSettings. | see source |
| `TransferLabware.to_xml` | `api_v2/commands.py` | `()` | see source | see source |
| `UserPrompt` | `api_v2/commands.py` | class | api-v2-045 (+ Validate scaffold for api-v2-046). | , |
| `UserPrompt.is_rup_standard` | `api_v2/commands.py` | `()` | see source | see source |
| `UserPrompt.to_string` | `api_v2/commands.py` | `()` | see source | see source |
| `UserPrompt.validate` | `api_v2/commands.py` | `()` | see source | see source |
| `UserPrompt.to_xml` | `api_v2/commands.py` | `()` | see source | see source |
| `GetFingers` | `api_v2/commands.py` | class | api-v2-017 ToXML / api-v2-018 Validate for RGA/CGA finger pickup. | , |
| `GetFingers.to_string` | `api_v2/commands.py` | `()` | see source | see source |
| `GetFingers.validate` | `api_v2/commands.py` | `()` | see source | see source |
| `GetFingers.to_xml` | `api_v2/commands.py` | `()` | see source | see source |
| `DropFingers` | `api_v2/commands.py` | class | api-v2-048 ToXML / api-v2-049 Validate. | , |
| `DropFingers.to_string` | `api_v2/commands.py` | `()` | see source | see source |
| `DropFingers.validate` | `api_v2/commands.py` | `()` | see source | see source |
| `DropFingers.to_xml` | `api_v2/commands.py` | `()` | see source | see source |
| `RemoveLabware` | `api_v2/commands.py` | class | api-v2-059 RemoveLabware.ToXML(). | , |
| `RemoveLabware.to_string` | `api_v2/commands.py` | `()` | see source | see source |
| `RemoveLabware.validate` | `api_v2/commands.py` | `()` | see source | see source |
| `RemoveLabware.to_xml` | `api_v2/commands.py` | `()` | see source | see source |
| `drop_fingers_from_xscr_object` | `api_v2/commands.py` | `(element)` | see source | see source |
| `remove_labware_from_xscr_object` | `api_v2/commands.py` | `(element)` | see source | see source |
| `get_fingers_from_xscr_element` | `api_v2/commands.py` | `(element)` | Parse ``CgaGetFingersScriptCommandDataV1`` XML into a typed GetFingers command. | see source |
| `set_location_from_ir_step` | `api_v2/commands.py` | `(step)` | see source | see source |
| `subroutine_from_ir_step` | `api_v2/commands.py` | `(step)` | see source | see source |
| `transfer_labware_from_ir_step` | `api_v2/commands.py` | `(step)` | see source | see source |
| `get_fingers_from_ir_step` | `api_v2/commands.py` | `(step)` | see source | see source |
| `add_labware_from_ir_step` | `api_v2/commands.py` | `(step)` | see source | see source |
| `user_prompt_from_ir_step` | `api_v2/commands.py` | `(step)` | see source | see source |
| `validate_command` | `api_v2/commands.py` | `(command)` | see source | see source |
| `_rup_timeout_from_xscr (priv)` | `api_v2/commands.py` | `(node)` | Derive UserPrompt timeout from RUP auto-close flags, not the default RUPTimeOut. | see source |
| `_parse_transfer_setting (priv)` | `api_v2/commands.py` | `(settings, tag, default)` | see source | see source |
| `AddLabware` | `api_v2/commands.py` | class | api-v2-007 ToXML + api-v2-008 ``AddLabware.Validate()``. | , |
| `AddLabware.to_string` | `api_v2/commands.py` | `()` | see source | see source |
| `AddLabware.validate` | `api_v2/commands.py` | `()` | see source | see source |
| `AddLabware.to_xml` | `api_v2/commands.py` | `()` | see source | see source |
| `_format_set_variable_xml_value (priv)` | `api_v2/commands.py` | `(value)` | Format ``<Value>`` the way FluentControl serializes SetVariableStatement. | see source |
| `SetVariable` | `api_v2/commands.py` | class | FluentControl ``SetVariableStatement`` (api-v2 variable parity). | , |
| `SetVariable.to_string` | `api_v2/commands.py` | `()` | see source | see source |
| `SetVariable.validate` | `api_v2/commands.py` | `()` | see source | see source |
| `SetVariable.to_xml` | `api_v2/commands.py` | `()` | see source | see source |
| `QueryVariable` | `api_v2/commands.py` | class | FluentControl ``QueryVariableStatement`` (api-v2 variable parity). | , |
| `QueryVariable.to_string` | `api_v2/commands.py` | `()` | see source | see source |
| `QueryVariable.validate` | `api_v2/commands.py` | `()` | see source | see source |
| `QueryVariable.to_xml` | `api_v2/commands.py` | `()` | see source | see source |
| `RUPVariable` | `api_v2/commands.py` | class | FluentControl ``RUPVariableStatement`` TouchTools runtime prompt form. | , |
| `RUPVariable.to_string` | `api_v2/commands.py` | `()` | see source | see source |
| `RUPVariable.validate` | `api_v2/commands.py` | `()` | see source | see source |
| `RUPVariable.to_params` | `api_v2/commands.py` | `()` | see source | see source |
| `RUPVariable.to_xml` | `api_v2/commands.py` | `()` | see source | see source |
| `set_variable_from_ir_step` | `api_v2/commands.py` | `(step)` | see source | see source |
| `query_variable_from_ir_step` | `api_v2/commands.py` | `(step)` | see source | see source |
| `rup_variable_from_ir_step` | `api_v2/commands.py` | `(step)` | see source | see source |
| `_parse_rup_variable_items (priv)` | `api_v2/commands.py` | `(node)` | see source | see source |
| `_parse_query_limit_value (priv)` | `api_v2/commands.py` | `(node, tag)` | see source | see source |
| `GenericCommand` | `api_v2/commands.py` | class | api-v2-004 passthrough wrapper for unmodeled compiled statements. | , |
| `GenericCommand.to_string` | `api_v2/commands.py` | `()` | see source | see source |
| `GenericCommand.validate` | `api_v2/commands.py` | `()` | see source | see source |
| `GenericCommand.to_xml` | `api_v2/commands.py` | `()` | see source | see source |
| `command_to_xml` | `api_v2/commands.py` | `(command)` | Single entry point for ``ICommand.ToXML()`` (offline serializer). | see source |
| `command_from_ir_step` | `api_v2/commands.py` | `(step)` | Map a canonical IR step to the best-known typed API V2 command. | see source |
| `command_from_xscr_object` | `api_v2/commands.py` | `(element)` | Parse a compiled XSCR ``Object`` node into a typed API V2 command. | see source |
| `enrich_fluent_context_details` | `api_v2/context_enrichment.py` | `(details)` | Attach low-priority API V2 observability blocks to runtime-report details. | see source |
| `merge_api_v2_context_into_report` | `api_v2/context_enrichment.py` | `(report)` | Merge api-v2-081..087 observability blocks and apply preflight errors. | see source |
| `render_api_v2_context_markdown_lines` | `api_v2/context_enrichment.py` | `(details)` | Human-readable api_v2 block for runtime-report markdown (081..085). | see source |
| `emit_ir_deck_step_events` | `api_v2/context_enrichment.py` | `(ir, emit)` | Emit SetLocation/add_labware trace events during generate --event-log (api-v2-086). | see source |
| `log_remove_labware_trace` | `api_v2/context_enrichment.py` | `(details)` | api-v2-079: record RemoveLabware ToString-style trace in runtime-report details. | see source |
| `log_set_location_trace` | `api_v2/context_enrichment.py` | `(details)` | api-v2-086: record SetLocation ToString-style trace in runtime-report details. | see source |
| `subroutine_trace_for_call` | `api_v2/context_enrichment.py` | `(call)` | api-v2-087: build Subroutine ToString-style label from an IR/manifest call record. | see source |
| `ExecutionChannelTracker` | `api_v2/events.py` | class | Track ``ChannelOpens`` / ``ChannelCloses`` (api-v2-068/069). | , |
| `ExecutionChannelTracker.on_channel_opens` | `api_v2/events.py` | `(channel)` | see source | see source |
| `ExecutionChannelTracker.on_channel_closes` | `api_v2/events.py` | `(channel)` | see source | see source |
| `ExecutionChannelTracker.has_open_channel` | `api_v2/events.py` | `()` | see source | see source |
| `ExecutionChannelTracker.wait_until_closed` | `api_v2/events.py` | `()` | see source | see source |
| `ReadyModeWaiter` | `api_v2/events.py` | class | Event-driven ready signal via ``EnterReadyMode`` (api-v2-070). | , |
| `ReadyModeWaiter.on_enter_ready_mode` | `api_v2/events.py` | `()` | see source | see source |
| `ReadyModeWaiter.wait` | `api_v2/events.py` | `()` | see source | see source |
| `DeckCheckAsyncPolicy` | `api_v2/events.py` | class | Default headless policy for deck-check delegates (api-v2-074). | , |
| `wait_deck_check_end_invoke` | `api_v2/events.py` | `(end_invoke, async_result)` | Bounded ``EndInvoke`` when BeginInvoke was used (api-v2-075). | see source |
| `ExpressionCheck` | `api_v2/expressions.py` | class | class | , |
| `ExpressionCheckResult` | `api_v2/expressions.py` | class | class | , |
| `run_expression_checks` | `api_v2/expressions.py` | `(runtime, checks)` | Evaluate IR-declared expressions via ``ResolveExpression`` after prepare/run. | see source |
| `GenericCommandValidationFailure` | `api_v2/generic_command_validate.py` | class | class | , |
| `GenericCommandValidationFailure.as_dict` | `api_v2/generic_command_validate.py` | `()` | see source | see source |
| `GenericCommandValidationFailure.as_finding` | `api_v2/generic_command_validate.py` | `()` | see source | see source |
| `GenericCommandValidationReport` | `api_v2/generic_command_validate.py` | class | class | , |
| `GenericCommandValidationReport.as_dict` | `api_v2/generic_command_validate.py` | `()` | see source | see source |
| `GenericCommandValidationReport.fluentcontrol_findings` | `api_v2/generic_command_validate.py` | `()` | see source | see source |
| `GenericCommandValidateProvider` | `api_v2/generic_command_validate.py` | class | class | , |
| `GenericCommandValidateProvider.validate_passthrough_commands` | `api_v2/generic_command_validate.py` | `(path)` | see source | see source |
| `OfflineGenericCommandValidateProvider` | `api_v2/generic_command_validate.py` | class | class | , |
| `OfflineGenericCommandValidateProvider.validate_passthrough_commands` | `api_v2/generic_command_validate.py` | `(path)` | see source | see source |
| `NativeGenericCommandValidateProvider` | `api_v2/generic_command_validate.py` | class | Reserved native VisionX API V2 GenericCommand.Validate() provider. | , |
| `NativeGenericCommandValidateProvider.validate_passthrough_commands` | `api_v2/generic_command_validate.py` | `(path)` | see source | see source |
| `native_generic_validate_available` | `api_v2/generic_command_validate.py` | `()` | see source | see source |
| `default_generic_command_validate_provider` | `api_v2/generic_command_validate.py` | `()` | see source | see source |
| `validate_passthrough_commands_from_xscr` | `api_v2/generic_command_validate.py` | `(path)` | see source | see source |
| `_resolved_passthrough_command_id (priv)` | `api_v2/generic_command_validate.py` | `(element)` | see source | see source |
| `extract_passthrough_generic_commands` | `api_v2/generic_command_validate.py` | `(root)` | see source | see source |
| `validate_generic_command_payload` | `api_v2/generic_command_validate.py` | `(command)` | Offline ``GenericCommand.Validate()`` , raises ``ApiV2ValidationError`` on failure. | see source |
| `validate_generic_command_before_execute` | `api_v2/generic_command_validate.py` | `()` | Pre-ExecuteCommand check for stepped runner; returns error text or None. | see source |
| `stepped_command_from_xscr` | `api_v2/generic_passthrough.py` | `(command_object)` | Return ``(api_v2_type, execute_xml, operation)`` for one XSCR statement. | see source |
| `uses_generic_command_passthrough` | `api_v2/generic_passthrough.py` | `()` | True when Gate 27 should exercise this step via ``GenericCommand`` raw XML. | see source |
| `generic_command_from_stepped` | `api_v2/generic_passthrough.py` | `()` | Rebuild a ``GenericCommand`` from a stepped-runner ``ICommand`` payload. | see source |
| `validate_generic_passthrough_execute_xml` | `api_v2/generic_passthrough.py` | `()` | Return an error message when GenericCommand passthrough XML is invalid. | see source |
| `_object_type_from_payload (priv)` | `api_v2/generic_passthrough.py` | `(payload_xml)` | see source | see source |
| `_parse_line_number (priv)` | `api_v2/generic_passthrough.py` | `(line_number)` | see source | see source |
| `compare_xscr_commands_to_native_xml` | `api_v2/golden_compare.py` | `(xscr_path)` | Compare each compiled command Object against re-serialized ``ICommand.ToXML()``. | see source |
| `_command_summary (priv)` | `api_v2/golden_compare.py` | `(command)` | Human-readable ``ICommand.ToString()`` for Gate 11 mismatch logs (api-v2-072). | see source |
| `golden_compare_summary` | `api_v2/golden_compare.py` | `(findings)` | Roll up golden-compare findings for validation gate details. | see source |
| `resolve_legacy_service_endpoint` | `api_v2/legacy_sila.py` | `(host, port)` | Resolve the legacy SiLA service endpoint for observability only.  ``HelperAPI.GenerateServiceEndpoin | see source |
| `native_to_xml_available` | `api_v2/native_provider.py` | `()` | see source | see source |
| `NativeToXmlProvider` | `api_v2/native_provider.py` | class | class | , |
| `NativeToXmlProvider.available` | `api_v2/native_provider.py` | `()` | see source | see source |
| `NativeToXmlProvider.to_xml` | `api_v2/native_provider.py` | `(command)` | see source | see source |
| `ProgressSyncPolicy` | `api_v2/progress_policy.py` | class | Workflow tooling must not scatter ``Progress.BeginInvoke`` / ``EndInvoke``. | , |
| `progress_wait_guidance` | `api_v2/progress_policy.py` | `()` | Human-readable guidance recorded in runtime-report details. | see source |
| `RunControlOptions` | `api_v2/run_control.py` | class | Opt-in live run control flags. | , |
| `pause_run_if_enabled` | `api_v2/run_control.py` | `(runtime)` | Call ``PauseRun`` only when explicitly enabled (api-v2-064).  Default Gate 27 stays prepare-only; ha | see source |
| `OperatorAckSource` | `api_v2/run_control.py` | class | External operator acknowledgement for semi-automated ``ResumeRun`` (api-v2-085). | , |
| `OperatorAckSource.wait_for_ack` | `api_v2/run_control.py` | `(timeout_seconds)` | see source | see source |
| `ResumeRunResult` | `api_v2/run_control.py` | class | class | , |
| `ResumeRunResult.as_dict` | `api_v2/run_control.py` | `()` | see source | see source |
| `CallableOperatorAckSource` | `api_v2/run_control.py` | class | Test/double ack source backed by a predicate. | , |
| `CallableOperatorAckSource.wait_for_ack` | `api_v2/run_control.py` | `(timeout_seconds)` | see source | see source |
| `FileOperatorAckSource` | `api_v2/run_control.py` | class | Wait for an operator to touch/create an ack file (``TECAN_OPERATOR_ACK_FILE``). | , |
| `FileOperatorAckSource.wait_for_ack` | `api_v2/run_control.py` | `(timeout_seconds)` | see source | see source |
| `is_user_prompt_type_name` | `api_v2/run_control.py` | `(type_name)` | True when a FluentControl type name is an operator UserPrompt command. | see source |
| `run_control_options_from_env` | `api_v2/run_control.py` | `()` | Build run-control flags from optional ``TECAN_*`` environment variables. | see source |
| `operator_ack_source_from_options` | `api_v2/run_control.py` | `(options)` | see source | see source |
| `call_resume_run` | `api_v2/run_control.py` | `(runtime)` | see source | see source |
| `resume_run_after_ack` | `api_v2/run_control.py` | `(runtime)` | Call ``ResumeRun`` only after an external operator-ack signal (api-v2-085).  Manual resume remains t | see source |
| `SemiAutomatedResumeMonitor` | `api_v2/run_control.py` | class | Listen for UserPrompt/pause events; resume only after external ack (api-v2-085). | , |
| `SemiAutomatedResumeMonitor.on_mode_changed` | `api_v2/run_control.py` | `(old, new)` | see source | see source |
| `SemiAutomatedResumeMonitor.after_user_prompt_command` | `api_v2/run_control.py` | `()` | see source | see source |
| `CloseMethodResult` | `api_v2/run_control.py` | class | Recorded ``IRuntimeController.CloseMethod()`` teardown (api-v2-022). | , |
| `CloseMethodResult.as_dict` | `api_v2/run_control.py` | `()` | see source | see source |
| `close_method_guarded` | `api_v2/run_control.py` | `(runtime)` | Call ``CloseMethod`` only when ``IsReady``/``GetFluentStatus`` allow it (api-v2-022). | see source |
| `MethodTeardown` | `api_v2/run_control.py` | class | Always ``StopMethod`` before guarded ``CloseMethod`` in provider finally blocks. | , |
| `MethodTeardown.run` | `api_v2/run_control.py` | `()` | see source | see source |
| `IRuntimeController` | `api_v2/runtime.py` | class | class | , |
| `IRuntimeController.login_user` | `api_v2/runtime.py` | `(username, password)` | see source | see source |
| `IRuntimeController.prepare_method` | `api_v2/runtime.py` | `(method)` | see source | see source |
| `IRuntimeController.run_method` | `api_v2/runtime.py` | `()` | see source | see source |
| `IRuntimeController.set_variable_value` | `api_v2/runtime.py` | `(name, value)` | see source | see source |
| `IRuntimeController.close_query_at_startup_dialog` | `api_v2/runtime.py` | `(accept_values)` | see source | see source |
| `IRuntimeController.get_fluent_status` | `api_v2/runtime.py` | `()` | see source | see source |
| `IRuntimeController.is_ready` | `api_v2/runtime.py` | `()` | see source | see source |
| `IRuntimeController.close_method` | `api_v2/runtime.py` | `()` | see source | see source |
| `IRuntimeController.stop_method` | `api_v2/runtime.py` | `()` | see source | see source |
| `MockRuntimeController` | `api_v2/runtime.py` | class | Deterministic runtime for unit tests and offline scaffolding. | , |
| `MockRuntimeController.validate_user` | `api_v2/runtime.py` | `(username, password)` | see source | see source |
| `MockRuntimeController.ValidateUser` | `api_v2/runtime.py` | `(username, password)` | see source | see source |
| `MockRuntimeController.get_progress` | `api_v2/runtime.py` | `()` | see source | see source |
| `MockRuntimeController.GetProgress` | `api_v2/runtime.py` | `()` | see source | see source |
| `MockRuntimeController.get_progress_initialization` | `api_v2/runtime.py` | `()` | see source | see source |
| `MockRuntimeController.GetProgressInitialization` | `api_v2/runtime.py` | `()` | see source | see source |
| `MockRuntimeController.resolve_expression` | `api_v2/runtime.py` | `(expression)` | see source | see source |
| `MockRuntimeController.ResolveExpression` | `api_v2/runtime.py` | `(expression)` | see source | see source |
| `MockRuntimeController.login_user` | `api_v2/runtime.py` | `(username, password)` | see source | see source |
| `MockRuntimeController.prepare_method` | `api_v2/runtime.py` | `(method)` | see source | see source |
| `MockRuntimeController.run_method` | `api_v2/runtime.py` | `()` | see source | see source |
| `MockRuntimeController.set_variable_value` | `api_v2/runtime.py` | `(name, value)` | see source | see source |
| `MockRuntimeController.get_variable_value` | `api_v2/runtime.py` | `(name)` | see source | see source |
| `MockRuntimeController.close_query_at_startup_dialog` | `api_v2/runtime.py` | `(accept_values)` | see source | see source |
| `MockRuntimeController.get_fluent_status` | `api_v2/runtime.py` | `()` | see source | see source |
| `MockRuntimeController.is_ready` | `api_v2/runtime.py` | `()` | see source | see source |
| `MockRuntimeController.close_method` | `api_v2/runtime.py` | `()` | see source | see source |
| `MockRuntimeController.stop_method` | `api_v2/runtime.py` | `()` | see source | see source |
| `MockRuntimeController.get_all_runnable_methods` | `api_v2/runtime.py` | `()` | see source | see source |
| `seed_simulation_values` | `api_v2/runtime.py` | `(runtime, seeds)` | Apply api-v2-034 SetVariableValue seeds before PrepareMethod. | see source |
| `wait_for_state` | `api_v2/runtime.py` | `(runtime, target)` | see source | see source |
| `login_user_or_fail` | `api_v2/runtime.py` | `(runtime, username, password)` | see source | see source |
| `prepare_method_checked` | `api_v2/runtime.py` | `(runtime, method)` | see source | see source |
| `run_method_checked` | `api_v2/runtime.py` | `(runtime)` | see source | see source |
| `try_import_native_runtime` | `api_v2/runtime.py` | `()` | Optional pythonnet bridge; returns (controller, error_message). | see source |
| `RuntimeEventCollector` | `api_v2/runtime_events.py` | class | Captures Error, ModeChanged, and CommonErrorDialog during prepare/run. | , |
| `RuntimeEventCollector.on_error` | `api_v2/runtime_events.py` | `(message)` | see source | see source |
| `RuntimeEventCollector.on_mode_changed` | `api_v2/runtime_events.py` | `(old, current)` | see source | see source |
| `RuntimeEventCollector.on_common_error_dialog` | `api_v2/runtime_events.py` | `(ced_info)` | ``CEDNotification.Invoke`` / ``RuntimeControllerEvents.CommonErrorDialog`` handler. | see source |
| `RuntimeEventCollector.on_progress_changed` | `api_v2/runtime_events.py` | `(value)` | see source | see source |
| `RuntimeEventCollector.on_notify` | `api_v2/runtime_events.py` | `(message)` | ``FluentControlEvents.Notify(message)`` (api-v2-055). | see source |
| `RuntimeEventCollector.on_notification` | `api_v2/runtime_events.py` | `(message)` | ``Notification.Invoke(message)`` (api-v2-058). | see source |
| `RuntimeEventCollector.on_deck_check_discrepancy` | `api_v2/runtime_events.py` | `(description, camera_results)` | ``DeckCheckDiscrepancyDetected.Invoke`` (api-v2-047). | see source |
| `RuntimeEventCollector.on_enter_ready_mode` | `api_v2/runtime_events.py` | `()` | see source | see source |
| `RuntimeEventCollector.on_channel_opens` | `api_v2/runtime_events.py` | `(channel)` | see source | see source |
| `RuntimeEventCollector.on_channel_closes` | `api_v2/runtime_events.py` | `(channel)` | see source | see source |
| `dispose_concrete_runtime_controller` | `api_v2/runtime_session.py` | `(controller)` | api-v2-080: dispose only when holding a concrete ``RuntimeController``.  Normal providers receive `` | see source |
| `partition_method_inventory` | `api_v2/runtime_session.py` | `(runnable_methods, maintenance_methods)` | api-v2-081: separate assay runnable names from maintenance-only names. | see source |
| `record_session_identity` | `api_v2/runtime_session.py` | `()` | api-v2-082: session identity block for runtime-report JSON. | see source |
| `resume_run_policy` | `api_v2/runtime_session.py` | `()` | api-v2-085: opt-in semi-automated ResumeRun after operator prompts. | see source |
| `validate_method_in_inventory` | `api_v2/runtime_session.py` | `(method)` | Return errors when *method* is absent from runnable + maintenance inventories (api-v2-081). | see source |
| `session_identity_errors` | `api_v2/runtime_session.py` | `(session)` | Fail when TECAN_FLUENT_USERNAME is set but GetCurrentUserName does not match (api-v2-082). | see source |
| `format_state_machine_state` | `api_v2/state.py` | `(state)` | Render a typed state as the stable human-readable label used in reports. | see source |
| `parse_state_machine_states` | `api_v2/state.py` | `(value)` | Parse arbitrary runtime state text into ``StateMachineStates``.  Mirrors ``HelperAPI.ParseStateMachi | see source |
| `format_state` | `api_v2/state.py` | `(value)` | Normalize runtime state for runtime reports (pairs with api-v2-063). | see source |
| `try_native_parse_state_machine_states` | `api_v2/state.py` | `(text)` | Optional pythonnet bridge to ``HelperAPI.ParseStateMachineStates``. | see source |
| `enrich_context_check_state` | `api_v2/state.py` | `(payload)` | Add typed ``state_machine_states`` metadata to a runtime-report payload. | see source |
| `compare_subroutine_step_to_compiled` | `api_v2/subroutine_identity.py` | `(step, compiled_object)` | Diff IR-derived ``Subroutine.ToXML()`` against one compiled statement. | see source |
| `audit_subroutine_identity` | `api_v2/subroutine_identity.py` | `(ir, compiled_xscr, source_manifest)` | Run subroutine statement + Script ``Reference`` identity checks. | see source |
| `subroutine_identity_summary` | `api_v2/subroutine_identity.py` | `(audit)` | Compact rollup suitable for subroutine_load_review ``details``. | see source |
| `StateMachineStates` | `api_v2/types.py` | class | Subset of FluentControl StateMachineStates used by Gate 27 waits. | , |
| `CedButton` | `api_v2/types.py` | class | class | , |
| `ICedInfo` | `api_v2/types.py` | class | Structured Common Error Dialog payload (api-v2-035/042). | , |
| `CedHandlerResult` | `api_v2/types.py` | class | class | , |
| `VariableSeed` | `api_v2/types.py` | class | class | , |
| `SteppedCommand` | `api_v2/types.py` | class | Minimal stepped-runner command shape for offline tracing and validation tests. | , |
| `variable_seed_fields` | `api_v2/types.py` | `(item)` | Return ``(name, value)`` from a seed object or mapping.  Duck-typed so duplicate ``VariableSeed`` cl | see source |
| `PrepareMethodResult` | `api_v2/types.py` | class | class | , |
| `RunMethodResult` | `api_v2/types.py` | class | class | , |
| `ApiV2ValidationError` | `api_v2/types.py` | class | Raised when an offline Validate() check fails. | , |
| `InteriorLightOptions` | `api_v2/verification_helpers.py` | class | Opt-in live interior-light toggle before operator prompts (api-v2-083). | , |
| `interior_light_verification_action` | `api_v2/verification_helpers.py` | `()` | api-v2-083: observe-only InteriorLight helper for teaching/verification runs. | see source |
| `interior_light_options_from_env` | `api_v2/verification_helpers.py` | `()` | Build interior-light flags from optional ``TECAN_INTERIOR_LIGHT_BEFORE_PROMPTS``. | see source |
| `interior_light_policy` | `api_v2/verification_helpers.py` | `()` | Policy block for runtime-report JSON (api-v2-083). | see source |
| `call_interior_light` | `api_v2/verification_helpers.py` | `(runtime)` | Invoke ``RuntimeController.InteriorLight(onOff)`` when available. | see source |
| `toggle_interior_light_before_prompt` | `api_v2/verification_helpers.py` | `(runtime)` | Call ``InteriorLight`` before operator prompts when explicitly enabled (api-v2-083).  Default Gate 2 | see source |
| `environmental_pre_run_template` | `api_v2/verification_helpers.py` | `()` | api-v2-084: template payload for ReportEnvironmentalData pre-run hooks. | see source |
| `CommandXmlCompareResult` | `api_v2/xml_compare.py` | class | class | , |
| `CommandXmlCompareResult.as_dict` | `api_v2/xml_compare.py` | `()` | see source | see source |
| `normalize_command_xml` | `api_v2/xml_compare.py` | `(xml_text)` | Normalize command XML for golden compare (ignore line numbers and whitespace). | see source |
| `compare_command_xml` | `api_v2/xml_compare.py` | `(expected, actual)` | see source | see source |
| `extract_command_objects_from_xscr` | `api_v2/xml_compare.py` | `(xscr_text)` | Return compiled command ``Object`` nodes as ``{command_id, xml}`` records. | see source |
| `AddLabwareFields` | `api_v2_add_labware_validate.py` | class | class | , |
| `AddLabwareFields.slot_key` | `api_v2_add_labware_validate.py` | `()` | see source | see source |
| `AddLabwareFields.label_key` | `api_v2_add_labware_validate.py` | `()` | see source | see source |
| `AddLabwareFields.as_dict` | `api_v2_add_labware_validate.py` | `()` | see source | see source |
| `AddLabwareValidateResult` | `api_v2_add_labware_validate.py` | class | class | , |
| `AddLabwareValidateResult.as_dict` | `api_v2_add_labware_validate.py` | `()` | see source | see source |
| `AddLabwareCommandLike` | `api_v2_add_labware_validate.py` | class | class | , |
| `is_add_labware_command` | `api_v2_add_labware_validate.py` | `(command)` | see source | see source |
| `extract_add_labware_fields` | `api_v2_add_labware_validate.py` | `(command)` | see source | see source |
| `add_labware_fields_from_ir_step` | `api_v2_add_labware_validate.py` | `(step)` | see source | see source |
| `validate_add_labware_fields` | `api_v2_add_labware_validate.py` | `(fields)` | Offline ``AddLabware.Validate()`` aligned with typed ``AddLabware.validate()``. | see source |
| `validate_add_labware_offline` | `api_v2_add_labware_validate.py` | `(command)` | see source | see source |
| `validate_add_labware_before_execute` | `api_v2_add_labware_validate.py` | `(command)` | see source | see source |
| `validate_add_labware_ir_steps` | `api_v2_add_labware_validate.py` | `(ir)` | Pre-compile validation of all ``add_labware`` IR steps (api-v2-008). | see source |
| `record_successful_add_labware` | `api_v2_add_labware_validate.py` | `(fields)` | see source | see source |
| `runtime_error_for_validate_failure` | `api_v2/validate_runtime.py` (+ typed wrappers in `api_v2_*_validate.py`) | `(result, command, *, kind)` | Prefer validator message; else step-scoped fallback. | see source |
| `failures_to_dicts` | `api_v2_add_labware_validate.py` | `(failures)` | see source | see source |
| `_validate_fields_core (priv)` | `api_v2_add_labware_validate.py` | `(fields)` | see source | see source |
| `_validate_fc_variable_name (priv)` | `api_v2_add_labware_validate.py` | `(field, name)` | see source | see source |
| `ExecutionChannelLike` | `api_v2_execution.py` | class | class | , |
| `ExecutionChannelLike.AbortExecution` | `api_v2_execution.py` | `()` | see source | see source |
| `ExecutionChannelLike.FinishExecution` | `api_v2_execution.py` | `()` | see source | see source |
| `ExecutionChannelLike.Dispose` | `api_v2_execution.py` | `()` | see source | see source |
| `RuntimeControllerLike` | `api_v2_execution.py` | class | class | , |
| `RuntimeControllerLike.StopMethod` | `api_v2_execution.py` | `()` | see source | see source |
| `RuntimeControllerLike.CloseMethod` | `api_v2_execution.py` | `()` | see source | see source |
| `ExecutionAbortContext` | `api_v2_execution.py` | class | class | , |
| `ExecutionAbortContext.as_dict` | `api_v2_execution.py` | `()` | see source | see source |
| `ExecutionAbortContext.from_mapping` | `api_v2_execution.py` | `(cls, value)` | see source | see source |
| `SteppedExecutionTracker` | `api_v2_execution.py` | class | Tracks the active stepped-runner command for abort metadata (api-v2-001). | , |
| `SteppedExecutionTracker.begin_command` | `api_v2_execution.py` | `(index, command_type)` | see source | see source |
| `SteppedExecutionTracker.abort_context` | `api_v2_execution.py` | `(reason, message)` | see source | see source |
| `execution_abort_from_timeout` | `api_v2_execution.py` | `(message)` | see source | see source |
| `execution_abort_from_external_timeout` | `api_v2_execution.py` | `(message)` | see source | see source |
| `execution_abort_from_blocked_user_prompt` | `api_v2_execution.py` | `(message)` | see source | see source |
| `execution_abort_from_common_error_dialog` | `api_v2_execution.py` | `(message)` | see source | see source |
| `execution_abort_from_runtime_error` | `api_v2_execution.py` | `(message)` | see source | see source |
| `execution_abort_from_blocked_dialog` | `api_v2_execution.py` | `(message)` | see source | see source |
| `abort_execution_channel` | `api_v2_execution.py` | `(channel)` | Invoke ``IExecutionChannel.AbortExecution`` when a channel is available. | see source |
| `perform_runtime_teardown` | `api_v2_execution.py` | `()` | AbortExecution, then StopMethod, then CloseMethod (api-v2-009 ordering). | see source |
| `merge_execution_abort_into_report` | `api_v2_execution.py` | `(report, abort_context)` | see source | see source |
| `execution_abort_from_report` | `api_v2_execution.py` | `(report)` | see source | see source |
| `render_execution_abort_markdown` | `api_v2_execution.py` | `(abort)` | see source | see source |
| `preflight_command_validation` | `api_v2_preflight.py` | `(xscr_path)` | Offline ``ICommand.Validate()`` preflight before compile/package checks. | see source |
| `VariableValueReader` | `api_v2_startup_variables.py` | class | Minimal runtime surface for ``GetVariableValue``. | , |
| `VariableValueReader.get_variable_value` | `api_v2_startup_variables.py` | `(name)` | see source | see source |
| `StartupVariableSnapshot` | `api_v2_startup_variables.py` | class | Comparison result for one query-at-startup variable. | , |
| `StartupVariableSnapshotReport` | `api_v2_startup_variables.py` | class | class | , |
| `StartupVariableSnapshotReport.as_dict` | `api_v2_startup_variables.py` | `()` | see source | see source |
| `query_at_startup_expectations` | `api_v2_startup_variables.py` | `(ir)` | Collect expected defaults for variables flagged query-at-startup in IR/spec. | see source |
| `snapshot_startup_variable_values` | `api_v2_startup_variables.py` | `(reader, names)` | Call ``GetVariableValue`` for each name and return normalized snapshots. | see source |
| `compare_startup_variable_snapshots` | `api_v2_startup_variables.py` | `(expected, actual)` | Diff expected IR/spec defaults against post-prepare runtime values. | see source |
| `run_startup_variable_value_check` | `api_v2_startup_variables.py` | `(reader, expectations)` | Snapshot and compare query-at-startup variables via ``GetVariableValue``. | see source |
| `normalize_variable_value` | `api_v2_startup_variables.py` | `(value)` | see source | see source |
| `variable_values_match` | `api_v2_startup_variables.py` | `(expected, actual)` | see source | see source |
| `expectations_as_tuple` | `api_v2_startup_variables.py` | `(expectations)` | Serialize IR/spec expectations for ``FluentContextCheckConfig``. | see source |
| `expectations_from_tuple` | `api_v2_startup_variables.py` | `(items)` | see source | see source |
| `live_startup_variable_snapshot_from_report` | `api_v2_startup_variables.py` | `(report)` | see source | see source |
| `ICommand` | `api_v2_stepped_inventory.py` | class | Minimal ``ICommand`` stand-in for ``ExecutionChannel.ExecuteCommand``. | , |
| `ICommand.command_type` | `api_v2_stepped_inventory.py` | `()` | see source | see source |
| `ICommand.as_dict` | `api_v2_stepped_inventory.py` | `()` | see source | see source |
| `ExecutionResult` | `api_v2_stepped_inventory.py` | class | class | , |
| `SteppedRunResult` | `api_v2_stepped_inventory.py` | class | class | , |
| `SteppedRunResult.as_details` | `api_v2_stepped_inventory.py` | `()` | see source | see source |
| `ExecutionChannel` | `api_v2_stepped_inventory.py` | class | FluentControl ``ExecutionChannel`` surface used by the stepped runner. | , |
| `ExecutionChannel.prepare_method` | `api_v2_stepped_inventory.py` | `(method)` | see source | see source |
| `ExecutionChannel.execute_command` | `api_v2_stepped_inventory.py` | `(command)` | see source | see source |
| `ExecutionChannel.finish_execution` | `api_v2_stepped_inventory.py` | `()` | see source | see source |
| `ExecutionChannel.wait_for_channel_close` | `api_v2_stepped_inventory.py` | `(timeout_seconds)` | see source | see source |
| `ExecutionChannel.close_method` | `api_v2_stepped_inventory.py` | `()` | see source | see source |
| `ExecutionChannel.abort_execution` | `api_v2_stepped_inventory.py` | `()` | see source | see source |
| `ChannelEventSink` | `api_v2_stepped_inventory.py` | class | Records RuntimeControllerEvents.Error and ChannelCloses callbacks (api-v2-002). | , |
| `ChannelEventSink.on_error` | `api_v2_stepped_inventory.py` | `(message)` | see source | see source |
| `ChannelEventSink.on_channel_closes` | `api_v2_stepped_inventory.py` | `()` | see source | see source |
| `extract_commands_from_xscr` | `api_v2_stepped_inventory.py` | `(path)` | Walk compiled XSCR XML and emit one ``ICommand`` per statement ``Object``. | see source |
| `map_ir_steps_to_commands` | `api_v2_stepped_inventory.py` | `(ir)` | Map reviewed protocol IR steps to ``ICommand`` instances. | see source |
| `resolve_commands` | `api_v2_stepped_inventory.py` | `()` | Prefer compiled XSCR commands; fall back to IR when XSCR is absent. | see source |
| `_append_execution_step (priv)` | `api_v2_stepped_inventory.py` | `(result, command)` | Record per-step pass/fail for runtime-report JSON (api-v2-002). | see source |
| `SteppedRunner` | `api_v2_stepped_inventory.py` | class | Execute mapped ``ICommand`` steps one at a time via ``ExecutionChannel``. | , |
| `SteppedRunner.run` | `api_v2_stepped_inventory.py` | `()` | see source | see source |
| `SteppedRunner._abort_and_teardown (priv)` | `api_v2_stepped_inventory.py` | `(result)` | AbortExecution, then StopMethod/CloseMethod; record metadata (api-v2-009). | see source |
| `SteppedRunner._teardown_method (priv)` | `api_v2_stepped_inventory.py` | `(close_method)` | StopMethod before CloseMethod so hung runs do not block the next check (api-v2-066). | see source |
| `RecordingExecutionChannel` | `api_v2_stepped_inventory.py` | class | Test double that records ``ExecuteCommand`` calls without FluentControl. | , |
| `RecordingExecutionChannel.prepare_method` | `api_v2_stepped_inventory.py` | `(method)` | see source | see source |
| `RecordingExecutionChannel.execute_command` | `api_v2_stepped_inventory.py` | `(command)` | see source | see source |
| `RecordingExecutionChannel.finish_execution` | `api_v2_stepped_inventory.py` | `()` | see source | see source |
| `RecordingExecutionChannel.wait_for_channel_close` | `api_v2_stepped_inventory.py` | `(timeout_seconds)` | see source | see source |
| `RecordingExecutionChannel.stop_method` | `api_v2_stepped_inventory.py` | `()` | see source | see source |
| `RecordingExecutionChannel.abort_execution` | `api_v2_stepped_inventory.py` | `()` | see source | see source |
| `RecordingExecutionChannel.close_method` | `api_v2_stepped_inventory.py` | `()` | see source | see source |
| `RegistryValidationExecutionChannel` | `api_v2_stepped_inventory.py` | class | Offline channel that validates command types against the command registry. | , |
| `RegistryValidationExecutionChannel.prepare_method` | `api_v2_stepped_inventory.py` | `(method)` | see source | see source |
| `RegistryValidationExecutionChannel.execute_command` | `api_v2_stepped_inventory.py` | `(command)` | see source | see source |
| `RegistryValidationExecutionChannel.finish_execution` | `api_v2_stepped_inventory.py` | `()` | see source | see source |
| `RegistryValidationExecutionChannel.wait_for_channel_close` | `api_v2_stepped_inventory.py` | `(timeout_seconds)` | see source | see source |
| `RegistryValidationExecutionChannel.close_method` | `api_v2_stepped_inventory.py` | `()` | see source | see source |
| `SubroutineValidateResult` | `api_v2_subroutine_validate.py` | class | class | , |
| `SubroutineValidateResult.as_dict` | `api_v2_subroutine_validate.py` | `()` | see source | see source |
| `SubroutineInventoryReport` | `api_v2_subroutine_validate.py` | class | Batch resolution of all subroutine calls against a runtime worktable inventory. | , |
| `SubroutineInventoryReport.as_dict` | `api_v2_subroutine_validate.py` | `()` | see source | see source |
| `SubroutineCommandLike` | `api_v2_subroutine_validate.py` | class | class | , |
| `is_subroutine_command` | `api_v2_subroutine_validate.py` | `(command)` | see source | see source |
| `extract_subroutine_path` | `api_v2_subroutine_validate.py` | `(command)` | see source | see source |
| `subroutine_model_from_command` | `api_v2_subroutine_validate.py` | `(command)` | see source | see source |
| `validate_subroutine_fields` | `api_v2_subroutine_validate.py` | `(path, execution_mode)` | see source | see source |
| `load_runtime_script_inventory` | `api_v2_subroutine_validate.py` | `(path)` | Load target worktable script inventory from env or an explicit JSON file. | see source |
| `resolve_subroutine_against_inventory` | `api_v2_subroutine_validate.py` | `(ref, inventory)` | Resolve one subroutine path against a live worktable script inventory. | see source |
| `validate_subroutine_offline` | `api_v2_subroutine_validate.py` | `(command)` | see source | see source |
| `validate_subroutine_before_execute` | `api_v2_subroutine_validate.py` | `(command)` | Validate a Subroutine command before ``ExecuteCommand`` (native first, then offline). | see source |
| `validate_subroutines_after_load` | `api_v2_subroutine_validate.py` | `()` | Batch live resolution after ZEIA/XSCR import (Gate 27 post-load hook for subroutine_load_review). | see source |
| `runtime_error_for_validate_failure` | `api_v2_subroutine_validate.py` | `(result, command)` | Thin wrapper → `api_v2.validate_runtime` (`kind="Subroutine"`). | see source |
| `TransferLabwareFields` | `api_v2_transfer_labware_validate.py` | class | class | , |
| `TransferLabwareFields.as_dict` | `api_v2_transfer_labware_validate.py` | `()` | see source | see source |
| `TransferLabwareFields.destination_slot` | `api_v2_transfer_labware_validate.py` | `()` | see source | see source |
| `TransferLabwareValidateResult` | `api_v2_transfer_labware_validate.py` | class | class | , |
| `TransferLabwareValidateResult.as_dict` | `api_v2_transfer_labware_validate.py` | `()` | see source | see source |
| `TransferLabwareCommandLike` | `api_v2_transfer_labware_validate.py` | class | class | , |
| `is_transfer_labware_command` | `api_v2_transfer_labware_validate.py` | `(command)` | see source | see source |
| `extract_transfer_labware_fields` | `api_v2_transfer_labware_validate.py` | `(command)` | see source | see source |
| `transfer_labware_fields_from_ir_step` | `api_v2_transfer_labware_validate.py` | `(step)` | see source | see source |
| `validate_transfer_labware_fields` | `api_v2_transfer_labware_validate.py` | `(fields)` | Offline ``TransferLabware.Validate()`` with strict deck simulation. | see source |
| `validate_transfer_labware_offline` | `api_v2_transfer_labware_validate.py` | `(command)` | see source | see source |
| `validate_transfer_labware_before_execute` | `api_v2_transfer_labware_validate.py` | `(command)` | see source | see source |
| `record_successful_transfer` | `api_v2_transfer_labware_validate.py` | `(fields)` | Update simulated deck state after a validated transfer. | see source |
| `runtime_error_for_validate_failure` | `api_v2_transfer_labware_validate.py` | `(result, command)` | Thin wrapper → `api_v2.validate_runtime` (`kind="TransferLabware"`). | see source |
| `_validate_fields_core (priv)` | `api_v2_transfer_labware_validate.py` | `(fields)` | see source | see source |
| `_parse_transfer_setting (priv)` | `api_v2_transfer_labware_validate.py` | `(settings, tag, default)` | see source | see source |
| `_normalize_base_token (priv)` | `api_v2_transfer_labware_validate.py` | `(location)` | see source | see source |
| `UserPromptValidateResult` | `api_v2_user_prompt_validate.py` | class | class | , |
| `UserPromptValidateResult.as_dict` | `api_v2_user_prompt_validate.py` | `()` | see source | see source |
| `UserPromptCommandLike` | `api_v2_user_prompt_validate.py` | class | class | , |
| `is_user_prompt_command` | `api_v2_user_prompt_validate.py` | `(command)` | see source | see source |
| `extract_prompt_message` | `api_v2_user_prompt_validate.py` | `(command)` | Read operator prompt text from a compiled XSCR command payload. | see source |
| `validate_user_prompt_offline` | `api_v2_user_prompt_validate.py` | `(command)` | Offline prompt_text_quality preflight when native ``UserPrompt.Validate()`` is unavailable. | see source |
| `validate_user_prompt_before_execute` | `api_v2_user_prompt_validate.py` | `(command)` | Validate a UserPrompt command before ``ExecuteCommand`` (native first, then offline). | see source |
| `runtime_error_for_validate_failure` | `api_v2_user_prompt_validate.py` | `(result, command)` | Thin wrapper → `api_v2.validate_runtime` (`kind="UserPrompt"`). | see source |
| `FluentInitializeContext` | `api_v2_workflow_helpers.py` | class | class | , |
| `FluentContextCheckConfig` | `api_v2_workflow_helpers.py` | class | Configuration shape retained for initialization planning and tests. | , |
| `build_initialize_steps` | `api_v2_workflow_helpers.py` | `(config)` | Build ordered initialization worktable steps before method preparation. | see source |
| `execution_steps_from_report` | `api_v2_workflow_helpers.py` | `(report)` | see source | see source |
