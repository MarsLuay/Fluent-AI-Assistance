# Target-aware deployment planning

Deployment planning is an offline, reviewable boundary. It compares an explicit
source profile with explicit target evidence and produces a
`tecan.deployment_plan.v1` plan. It does not open FluentControl, write a target,
or claim physical readiness.

## One planning surface

Use the shared application service through the CLI adapter:

```powershell
protocol-builder plan-deployment `
  --source-profile source-target.json `
  --target-profile captured-target.json `
  --mode cross_target_import `
  --report plan.md `
  --json-out plan.json
```

`source-target.json` and `captured-target.json` are explicit
`tecan.target_datastore.v1` profiles. A profile may be captured evidence or
built from explicitly supplied inventory roots and software identity. The
planner never substitutes the machine running the command for an omitted
target.

The same service is available to non-CLI adapters as
`fluent_pipeline.application_services.plan_deployment`. CLI, MCP, and direct
Python callers therefore use one plan builder rather than maintaining separate
promotion rules.

## Target modes

### Same-target drop-in

`same_target_dropin` is appropriate only when source and target fingerprints
match. The plan can report exact GUID/content reuse. A different fingerprint
blocks this mode; it is not silently treated as equivalent.

### Cross-target import

`cross_target_import` compares object GUID, content, semantic role, type, and
path evidence. Missing objects become import dependencies. Same-role objects
with different identity or content become review conflicts. External-file
relocations are recorded as actions, not performed by the planner.

Cross-target output is a prerequisite/review plan for supported artifact-level
import or promotion workflows. It is not an instruction to replace an entire
store or to rewrite target metadata.

### Unknown target

Use `--no-target` when no target evidence is available:

```powershell
protocol-builder plan-deployment `
  --source-profile source-target.json `
  --no-target `
  --json
```

The result is blocked with `target_profile_required`. This is intentional:
unknown target identity cannot establish compatibility or safe drop-in
readiness.

## Evidence and drift

The plan records source and target fingerprints, target profile ID, software
family evidence, object actions, conflicts, findings, mode, and final status.
Pass `--current-target-profile` when a fresh capture is available. If it does
not match the fingerprint used to create the plan, the service adds
`target_profile_drift` and invalidates the plan as blocked.

Target software-family mismatches are blocked. Missing family/build evidence is
reviewable or blocked according to the profile and plan mode; it is never
invented from a filename or host default.

## Safety boundary

- Planning is read-only and side-effect free.
- `destructive_target_mutation` is always `false` in the plan.
- Recovery/runtime state is not portable deployment source.
- Internal repository/database metadata (`DataBase.svn`, `SVNRoot`, and
  internal UUID/version records) is provenance or diagnostic evidence only,
  never a plan action.
- Do not replace or copy a whole FluentControl database, `SVNRoot`, or
  runtime/recovery store. Keep source control at the artifact/profile level.
- The plan does not prove instrument motion, calibration, alignment, clearance,
  collision safety, or FluentControl UI acceptance. Those remain separate
  compatibility and physical-verification boundaries.

## Status and exit codes

- `ready_for_same_target_dropin`: exact same-target evidence; exit `0`.
- `ready_for_import`: cross-target plan has no blocking finding; exit `0`.
- `needs_review`: evidence or object conflicts require review; exit `2`.
- `blocked`: target, compatibility, mode, or drift guard failed; exit `1`.

The Markdown report is for human review. The JSON output is the canonical
machine-readable plan and should be retained with the source artifact and
explicit profile evidence.

## Related references

- [compile-path.md](compile-path.md) — source compilation before planning.
- [cli.md](cli.md) — general command surface.
- `fluent_pipeline/target_datastore.py` — explicit profile schema and
  deterministic fingerprinting.
- `fluent_pipeline/deployment_plan.py` — shared plan logic and rendering.
