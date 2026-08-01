# Types: fluent-pipeline-api-v2

| Symbol | File | Notes |
| --- | --- | --- |
| `_SteppedCommandLike` | `api_v2/command_tracing.py` | class |
| `AddLabware` | `api_v2/commands.py` | api-v2-007 ToXML + api-v2-008 ``AddLabware.Validate()``. |
| `AddLabwareCommandLike` | `api_v2_add_labware_validate.py` | class |
| `AddLabwareFields` | `api_v2_add_labware_validate.py` | class |
| `AddLabwareValidateResult` | `api_v2_add_labware_validate.py` | class |
| `ApiV2ValidationError` | `api_v2/types.py` | Raised when an offline Validate() check fails. |
| `CallableOperatorAckSource` | `api_v2/run_control.py` | Test/double ack source backed by a predicate. |
| `CedButton` | `api_v2/types.py` | class |
| `CedHandlerResult` | `api_v2/types.py` | class |
| `CEDNotificationHandler` | `api_v2/ced.py` | Python stand-in for ``Tecan.VisionX.API.V2.CEDNotification.Invoke``. |
| `ChannelEventSink` | `api_v2_stepped_inventory.py` | Records RuntimeControllerEvents.Error and ChannelCloses callbacks (api-v2-002). |
| `CloseMethodResult` | `api_v2/run_control.py` | Recorded ``IRuntimeController.CloseMethod()`` teardown (api-v2-022). |
| `CommandValidateProvider` | `api_v2/command_validate.py` | class |
| `CommandValidationFailure` | `api_v2/command_validate.py` | class |
| `CommandValidationReport` | `api_v2/command_validate.py` | class |
| `CommandXmlCompareResult` | `api_v2/xml_compare.py` | class |
| `CommonErrorDialogJournal` | `api_v2/ced.py` | Records every ``CommonErrorDialog`` / CEDNotification decision. |
| `CompiledCommandRecord` | `api_v2/command_validate.py` | class |
| `DeckCheckAsyncPolicy` | `api_v2/events.py` | Default headless policy for deck-check delegates (api-v2-074). |
| `DropFingers` | `api_v2/commands.py` | api-v2-048 ToXML / api-v2-049 Validate. |
| `ExecutionAbortContext` | `api_v2_execution.py` | class |
| `ExecutionChannel` | `api_v2_stepped_inventory.py` | FluentControl ``ExecutionChannel`` surface used by the stepped runner. |
| `ExecutionChannelLike` | `api_v2_execution.py` | class |
| `ExecutionChannelTracker` | `api_v2/events.py` | Track ``ChannelOpens`` / ``ChannelCloses`` (api-v2-068/069). |
| `ExecutionResult` | `api_v2_stepped_inventory.py` | class |
| `ExpressionCheck` | `api_v2/expressions.py` | class |
| `ExpressionCheckResult` | `api_v2/expressions.py` | class |
| `FileOperatorAckSource` | `api_v2/run_control.py` | Wait for an operator to touch/create an ack file (``TECAN_OPERATOR_ACK_FILE``). |
| `FluentContextCheckConfig` | `api_v2_workflow_helpers.py` | Configuration shape retained for initialization planning and tests. |
| `FluentInitializeContext` | `api_v2_workflow_helpers.py` | class |
| `GenericCommand` | `api_v2/commands.py` | api-v2-004 passthrough wrapper for unmodeled compiled statements. |
| `GenericCommandValidateProvider` | `api_v2/generic_command_validate.py` | class |
| `GenericCommandValidationFailure` | `api_v2/generic_command_validate.py` | class |
| `GenericCommandValidationReport` | `api_v2/generic_command_validate.py` | class |
| `GetFingers` | `api_v2/commands.py` | api-v2-017 ToXML / api-v2-018 Validate for RGA/CGA finger pickup. |
| `ICedInfo` | `api_v2/types.py` | Structured Common Error Dialog payload (api-v2-035/042). |
| `ICommand` | `api_v2/commands.py` | class |
| `ICommand` | `api_v2_stepped_inventory.py` | Minimal ``ICommand`` stand-in for ``ExecutionChannel.ExecuteCommand``. |
| `InteriorLightOptions` | `api_v2/verification_helpers.py` | Opt-in live interior-light toggle before operator prompts (api-v2-083). |
| `IRuntimeController` | `api_v2/runtime.py` | class |
| `MethodTeardown` | `api_v2/run_control.py` | Always ``StopMethod`` before guarded ``CloseMethod`` in provider finally blocks. |
| `MockRuntimeController` | `api_v2/runtime.py` | Deterministic runtime for unit tests and offline scaffolding. |
| `NativeApiV2CommandValidateProvider` | `api_v2/command_validate.py` | class |
| `NativeGenericCommandValidateProvider` | `api_v2/generic_command_validate.py` | Reserved native VisionX API V2 GenericCommand.Validate() provider. |
| `NativeToXmlProvider` | `api_v2/native_provider.py` | class |
| `OfflineCommandValidateProvider` | `api_v2/command_validate.py` | class |
| `OfflineGenericCommandValidateProvider` | `api_v2/generic_command_validate.py` | class |
| `OperatorAckSource` | `api_v2/run_control.py` | External operator acknowledgement for semi-automated ``ResumeRun`` (api-v2-085). |
| `PrepareMethodResult` | `api_v2/types.py` | class |
| `ProgressSyncPolicy` | `api_v2/progress_policy.py` | Workflow tooling must not scatter ``Progress.BeginInvoke`` / ``EndInvoke``. |
| `QueryVariable` | `api_v2/commands.py` | FluentControl ``QueryVariableStatement`` (api-v2 variable parity). |
| `ReadyModeWaiter` | `api_v2/events.py` | Event-driven ready signal via ``EnterReadyMode`` (api-v2-070). |
| `RecordingExecutionChannel` | `api_v2_stepped_inventory.py` | Test double that records ``ExecuteCommand`` calls without FluentControl. |
| `RegistryValidationExecutionChannel` | `api_v2_stepped_inventory.py` | Offline channel that validates command types against the command registry. |
| `RemoveLabware` | `api_v2/commands.py` | api-v2-059 RemoveLabware.ToXML(). |
| `ResumeRunResult` | `api_v2/run_control.py` | class |
| `RunControlOptions` | `api_v2/run_control.py` | Opt-in live run control flags. |
| `RunMethodResult` | `api_v2/types.py` | class |
| `RuntimeControllerLike` | `api_v2_execution.py` | class |
| `RuntimeEventCollector` | `api_v2/runtime_events.py` | Captures Error, ModeChanged, and CommonErrorDialog during prepare/run. |
| `RUPVariable` | `api_v2/commands.py` | FluentControl ``RUPVariableStatement`` TouchTools runtime prompt form. |
| `SemiAutomatedResumeMonitor` | `api_v2/run_control.py` | Listen for UserPrompt/pause events; resume only after external ack (api-v2-085). |
| `SetLocation` | `api_v2/commands.py` | api-v2-038 / api-v2-039. |
| `SetVariable` | `api_v2/commands.py` | FluentControl ``SetVariableStatement`` (api-v2 variable parity). |
| `StartupVariableSnapshot` | `api_v2_startup_variables.py` | Comparison result for one query-at-startup variable. |
| `StartupVariableSnapshotReport` | `api_v2_startup_variables.py` | class |
| `StateMachineStates` | `api_v2/types.py` | Subset of FluentControl StateMachineStates used by Gate 27 waits. |
| `SteppedCommand` | `api_v2/types.py` | Minimal stepped-runner command shape for offline tracing and validation tests. |
| `SteppedExecutionTracker` | `api_v2_execution.py` | Tracks the active stepped-runner command for abort metadata (api-v2-001). |
| `SteppedRunner` | `api_v2_stepped_inventory.py` | Execute mapped ``ICommand`` steps one at a time via ``ExecutionChannel``. |
| `SteppedRunResult` | `api_v2_stepped_inventory.py` | class |
| `Subroutine` | `api_v2/commands.py` | api-v2-040 / api-v2-041. |
| `SubroutineCommandLike` | `api_v2_subroutine_validate.py` | class |
| `SubroutineInventoryReport` | `api_v2_subroutine_validate.py` | Batch resolution of all subroutine calls against a runtime worktable inventory. |
| `SubroutineValidateResult` | `api_v2_subroutine_validate.py` | class |
| `TransferLabware` | `api_v2/commands.py` | api-v2-043 / api-v2-044. |
| `TransferLabwareCommandLike` | `api_v2_transfer_labware_validate.py` | class |
| `TransferLabwareFields` | `api_v2_transfer_labware_validate.py` | class |
| `TransferLabwareValidateResult` | `api_v2_transfer_labware_validate.py` | class |
| `UserPrompt` | `api_v2/commands.py` | api-v2-045 (+ Validate scaffold for api-v2-046). |
| `UserPromptCommandLike` | `api_v2_user_prompt_validate.py` | class |
| `UserPromptValidateResult` | `api_v2_user_prompt_validate.py` | class |
| `VariableMapping` | `api_v2/commands.py` | class |
| `VariableSeed` | `api_v2/types.py` | class |
| `VariableValueReader` | `api_v2_startup_variables.py` | Minimal runtime surface for ``GetVariableValue``. |
