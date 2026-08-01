# Review Notes

This repository is being polished for **community technical feedback**, not
shipped as a production-ready or instrument-qualified package.

See [docs/RELEASE_READINESS.md](docs/RELEASE_READINESS.md) for the full
pre-release checklist and [NOTICE.md](NOTICE.md) for affiliation and hardware
caution.

## Current reviewer expectations

- Evaluate whether the domain model fits real FluentControl workflows.
- Report simulator gaps, API ergonomics issues, and documentation inaccuracies.
- Do not run generated protocols on hardware without normal lab controls.

## Resolved audit items

The following were open gaps in earlier review passes and are now **implemented
with tests/docs** (offline scope — hardware caution unchanged):

| Item | Resolution |
|---|---|
| MCA-384 liquid handling | `MCA384Head` (`wt.mca384`): tips, arm moves, aspirate/dispense, mix, empty-tips; documented in [docs/authoring.md](docs/authoring.md) |
| LiHa / FCA-style pipetting | `LiHa` (`wt.liha`) and `FCAHead` (`wt.fca`) authoring facades; decompiler coverage for structured LiHa steps |
| Subroutine calls | `wt.call_subroutine`, compile to `SubRoutineStep`, `SubroutineRegistry` + simulate inlining with cycle/depth limits |
| Loops and conditionals | `wt.loop` / `wt.conditional` context managers; renderer emits loop/conditional groups |
| Simulator physical checks | Occupied slots, tips/adapters, overdraws, pinned magnet layers, insufficient volume |
| Decompiler recovery | `.xscr` → Python → recompile path with strict simulate gates for corpus fixtures |
| Device init / app-driver macros | `wt.initialize_device(...)`, `wt.application_driver_macro(...)` — parse, codegen, compile, and simulate |
| FC variable tokens | `wt.declare_fc_variable(...)` / `FCVariableToken` for dynamic catalog references in IR and decompiled `.py` |
| Decompiler LiHa / subroutine codegen | Structured `LiHa*Step` and `SubRoutineStep` emit `wt.liha.*` / `wt.call_subroutine(...)` (legacy LiHa pick-up XML may still use `wt.raw_xml_step`) |
| Default catalog per class | `Worktable.set_default_catalog(...)` / `fluentcoder.defaults.set_catalog_defaults` — omit `catalog=` when a class default is registered |
| Delta snapshot mode | `record_snapshots="delta"` / CLI `--delta-snapshots` for lightweight per-step history |
| Optional import boundary | `fluentcoder.__init__` avoids authoring imports; core deps are `pydantic` + `PyYAML` only ([PACKAGING_NOTES.md](PACKAGING_NOTES.md)) |
| Protocol-builder integration | First-party package under `libs/fluentcoder`; pipeline uses compile/simulate, not LM authoring loop |
| Workspace binding at compile | `Protocol.worktable_guid`/`worktable_name` preferred; `generation.yaml` fallback warns; `strict_workspace_binding` opt-in; tests in `tests/test_workspace_binding.py` |

## Remaining blockers (release / trust)

These should be cleared or explicitly accepted before calling fluentcoder
"broadly release-ready" or safe for unattended robot use:

1. **No open-source license** — source-visible review only until a license is
   recorded ([NOTICE.md](NOTICE.md)).
2. **`generation.yaml` machine-specific defaults** — workspace binding is now
   explicit on the `Protocol` IR with a documented legacy fallback (see
   [docs/compile-path.md](docs/compile-path.md)); device `available_id` strings
   and grounding layout remain developer-machine defaults. Decide whether those
   fields need neutral placeholders or required user config before public
   distribution.
3. **Bundled reference provenance** — confirm `fluentcoder/_assets/reference/*`
   and templates are acceptable to ship under the chosen license; document what
   is observational vs install-sourced.
4. **Install-backed test fixtures** — several tests intentionally reference
   real workspace names (`SAT_Fluent_780_Rev3`, `780_Empty`) to exercise catalog
   lookup. Keep these out of marketing claims; consider more synthetic fixtures
   for CI-only public branches.
5. **Hardware qualification boundary** — simulate + compile do not substitute
   for FluentControl context-check, FC simulation mode, or site method
   validation. [docs/deployment.md](docs/deployment.md) documents loader vs
   semantic validation limits.
6. **Authoring loop packaging** — `author` / `chat` / `deploy` require
   `.[authoring]` and operator/API setup; protocol-builder **disables** these
   commands by design. Documented in [CONTRIBUTING.md](CONTRIBUTING.md) and
   [docs/RELEASE_READINESS.md](docs/RELEASE_READINESS.md).

## Feedback welcome on

- Command families and workflow patterns still missing from the IR/renderer.
- Whether decompile-to-Python is trustworthy enough for legacy method review.
- Which `generation.yaml` fields should become user-required configuration.
