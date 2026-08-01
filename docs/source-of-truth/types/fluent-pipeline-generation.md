# Types: fluent-pipeline-generation

| Symbol | File | Notes |
| --- | --- | --- |
| `ApprovalSet` | `generation_workflow.py` | class |
| `BundleVerificationRequest` | `application_services.py` | class |
| `BundleVerificationResult` | `application_services.py` | class |
| `CommandRecord` | `minimal_edit.py` | class |
| `GenerationOptions` | `generation_options.py` | class |
| `GenerationRequest` | `generation_workflow.py` | class |
| `GenerationResult` | `application_services.py` | class |
| `GenerationStage` | `workflows/generation/runner.py` | One ordered, synchronous generation workflow stage. |
| `GenerationStageRunner` | `workflows/generation/runner.py` | Run stages in declaration order against exactly one shared state object. |
| `GenerationState` | `workflows/generation/state.py` | Mutable state passed through one ordered generation-stage sequence. |
| `LoadContextStage` | `workflows/generation/stages.py` | Load the requested project context with the legacy progress contract. |
| `LogAnalysisRequest` | `application_services.py` | class |
| `LogAnalysisResult` | `application_services.py` | class |
| `ProjectImportRequest` | `application_services.py` | class |
| `ProjectImportResult` | `application_services.py` | class |
| `ProjectInspectionRequest` | `application_services.py` | class |
| `ProjectInspectionResult` | `application_services.py` | class |
| `PythonSourceIndex` | `repair.py` | class |
| `RepairAction` | `repair.py` | class |
| `RepairApplicationError` | `repair.py` | Raised when a structured repair no longer matches the recorded span. |
| `RepairApplyRequest` | `application_services.py` | class |
| `RepairApplyResult` | `application_services.py` | class |
| `RepairEdit` | `repair.py` | class |
| `RepairPlan` | `repair.py` | class |
| `RepairPlanRequest` | `application_services.py` | class |
| `RepairPlanResult` | `application_services.py` | class |
| `RequestSpecCandidate` | `request_spec_resolver.py` | class |
| `RequestSpecCreateRequest` | `application_services.py` | class |
| `RequestSpecCreateResult` | `application_services.py` | class |
| `RequestSpecValidationRequest` | `application_services.py` | class |
| `RequestSpecValidationResult` | `application_services.py` | class |
