# Release Readiness Checklist

This document is a **documentation-only** readiness guide for reviewers and
maintainers. It is not legal advice. Use it to decide whether fluentcoder is
fit for a given workflow — especially before importing compiled scripts into
FluentControl or running anything on hardware.

## Status summary

| Area | Current state |
|---|---|
| License | **PolyForm Noncommercial 1.0.0** — free for noncommercial use; companies contact [marwanluay2005@gmail.com](mailto:marwanluay2005@gmail.com) — see [LICENSE](../../../../LICENSE) and [NOTICE.md](../NOTICE.md) |
| Core authoring / simulate / compile | **Usable offline** with documented limits |
| Install-backed catalog / workspaces | Requires a **licensed local FluentControl** install |
| LM authoring loop (`author` / `chat` / `deploy`) | **Optional extra** — needs `pip install -e ".[authoring]"` and API keys; disabled in protocol-builder by design |
| Instrument / robot use | **Not release-ready** without normal lab validation |

## 1. License and distribution

- [x] PolyForm Noncommercial 1.0.0 recorded at the Fluent AI-Assistance repository root (`LICENSE`).
- [ ] Third-party and Tecan/FluentControl-derived asset provenance is documented
  (see §4 below and [REVIEW_NOTES.md](../REVIEW_NOTES.md)).
- [x] README and NOTICE match the PolyForm Noncommercial license posture.

## 2. Safety boundaries: simulate vs hardware

fluentcoder separates **offline reasoning** from **instrument motion**:

| Path | What it proves | What it does *not* prove |
|---|---|---|
| `wt.simulate()` / `fluentcoder simulate` | Liquid math, deck/slot consistency, tip/adapter pre-checks, subroutine inlining (with registry), physical invariant errors | Real tip geometry, liquid-class behavior on hardware, timing, device faults |
| `wt.compile()` / `fluentcoder compile` | IR → `.xscr` XML shape, workspace reference wiring, checksum when a backend is available | FluentControl editor context-check, runnable method on your deck |
| Drop-in / `fluentcoder deploy` | Datastore loader accepts the file | Script is semantically valid for your worktable, liquids, and FC build |
| FluentControl Script Editor open | Context-check and load errors visible to the operator | Safe or qualified for production runs |
| Hardware run | Only path that moves the robot | — |

**Default expectation:** treat simulate + compile as **design-time checks**.
Do not run generated `.xscr` files on an instrument without:

1. FluentControl closed-loop review (Script Editor / simulation mode on the
   target system, per your site policy).
2. Operator review of prompts, deck layout, volumes, and subroutine
   dependencies.
3. Site-specific method validation and safety procedures.

See [docs/simulator.md](simulator.md), [docs/deployment.md](deployment.md), and
[docs/reviewer-guide.md](reviewer-guide.md).

## 3. FluentControl dependency

| Feature | Needs FC install? | Override |
|---|---|---|
| Import `fluentcoder`, author offline examples | No | — |
| Catalog index build / `Worktable.from_workspace` | Yes | `FLUENTCODER_FC_INSTALL` or CLI catalog flags |
| Checksum embed at compile time | Optional | `fluentcontrol_core` bridge when present; protocol-builder also ships a vendored checksum path |
| Deploy / shell validation | Yes | Licensed FC + `UserSpecific` datastore access |

Default install probe:

```text
C:\ProgramData\Tecan\VisionX\Database
```

Without a reachable install, catalog-backed tests skip or use offline
synthesis — the package should still import.

**Affiliation:** fluentcoder is not affiliated with, endorsed by, or licensed
by Tecan. Users must hold appropriate FluentControl licenses for install-backed
features.

## 4. Bundled reference data provenance

Shipped under `fluentcoder/_assets/`:

| Asset | Role | Provenance note |
|---|---|---|
| `templates/*.xml` | Compile-time XML skeletons | Project-authored templates aligned to FC script XML shape |
| `reference/commands.yaml` | Command metadata for renderer/registry | Derived from FluentControl command surface observation; not a Tecan publication |
| `reference/labware.yaml`, `locations.txt` | Offline / fallback hints | Curated reference; prefer install-backed catalog when available |
| `config/generation.yaml` | Default workspace GUID, device aliases, liquid class | **Neutral placeholders** (`780_Empty`, serial `0000000000`, generic catalog names) — customize via `generation.yaml.example` before instrument use |

Before a broad public release:

- [x] Replace site-specific `generation.yaml` defaults with neutral public-release
  placeholders; ship `generation.yaml.example` for per-site customization.
- [ ] Confirm bundled YAML/XML does not redistribute restricted Tecan content
  beyond what your distribution license allows.
- [ ] Document which assets are install-sourced vs hand-maintained.

## 5. Authoring / LLM optional dependencies

Core install (`pip install -e .`):

- `pydantic`, `PyYAML` only.

Optional extras ([pyproject.toml](../pyproject.toml)):

| Extra | Packages | Enables |
|---|---|---|
| `xml` | `lxml` | Faster/stricter XML paths where used |
| `authoring` | `langchain-core`, `langchain-openai`, `langgraph` | `fluentcoder author`, `fluentcoder chat`, LM tooling under `fluentcoder/authoring/` |
| `dev` | `pytest`, `build` | Test and packaging tooling |

The top-level package **does not** import authoring on `import fluentcoder`, so
compile/simulate/decompile work without LangChain or API keys.

### Authoring loop (protocol-builder policy)

The Fluent AI-Assistance **protocol-builder** pipeline consumes fluentcoder for
IR, compile, and simulate only. It **does not** run `fluentcoder author`,
`fluentcoder chat`, or `fluentcoder deploy` — by design in
`source/03-protocol-builder/AGENTS.md` (offline, no-key workflow).

To evaluate the LM authoring loop locally:

```bash
pip install -e ".[authoring,dev]"
```

Then see [docs/authoring.md](authoring.md) § Chat-driven authoring and
[CONTRIBUTING.md](../CONTRIBUTING.md) § Authoring loop.

## 6. Gates before robot use

Use this ordered checklist before any protocol touches hardware.

### fluentcoder-local gates

- [ ] `python -m pytest tests/ -q` passes in your environment (install
  `.[authoring,dev]` if running the full suite including LM/authoring tests).
- [ ] `fluentcoder simulate <protocol.py>` completes without
  `SimulationError` (use `--strict --fail-on-opaque` for decompiled scripts).
- [ ] If the protocol calls subroutines, `SubroutineRegistry` registers the
  real `.xscr` bodies and simulate inlines them successfully.
- [ ] `fluentcoder compile <protocol.py> -o staging.xscr` produces a file whose
  checksum inspects valid when `fluentcontrol_core` (or your site's checksum
  tool) is available.

### FluentControl gates (target system)

- [ ] Workspace referenced in the compiled script exists in the FC datastore.
- [ ] Liquid classes, labware types, and device aliases match the target FC
  build (version-sensitive `*DataVn` types).
- [ ] Script opens in Script Editor without blocking context-check errors.
- [ ] FC simulation mode (if used at your site) passes operator review.
- [ ] Deck layout, tips, reagents, and subroutine scripts on disk match what the
  method assumes.

### protocol-builder gates (when using the pipeline)

If the script was produced through `fluent_pipeline` generation, also inspect:

- `ready_validation.md`, `validation_diff.md`, `generation_manifest.json`
- Ready gates (compile, volume bounds, liquid state, checksum, packaged ZEIA,
  etc.) — see `source/03-protocol-builder/AGENTS.md`

`ready_to_import` means **offline packaging/import health**, not
hardware-run-ready.

### Human gates (always)

- [ ] Method owner sign-off.
- [ ] Site EHS / biosafety / contamination controls observed.
- [ ] First run uses reduced scale or dry deck policy per lab SOP.

## 7. Recent capability improvements (review context)

The following areas have progressed since early "not working" notes; they
improve **offline** fidelity but do not remove hardware caution:

- MCA-384 head authoring and simulation (`wt.mca384`)
- LiHa- and FCA-style liquid handling (`wt.liha`, `wt.fca`)
- Device initialization and application-driver macros (`wt.initialize_device`,
  `wt.application_driver_macro`) — parse, compile, decompile codegen, simulate
- FluentControl variable tokens (`wt.declare_fc_variable`, `FCVariableToken`) for
  dynamic catalog references in IR and decompiled protocols
- Subroutine call IR, compile, decompile codegen (`wt.call_subroutine`), and
  simulate inlining (`SubroutineRegistry`)
- Structured LiHa step decompile codegen (`wt.liha.*`; legacy pick-up XML may
  remain `wt.raw_xml_step`)
- Loops, conditionals, and protocol variables
- Decompiler round-trip and strict simulate gates for recovered scripts
- Deterministic `compile(..., deterministic=True)` for stable workspace deltas
- Delta snapshot mode (`record_snapshots="delta"`, CLI `--delta-snapshots`) for
  lower-memory per-step simulation history
- Default catalog per labware class (`Worktable.set_default_catalog`) so common
  `catalog=` strings can be omitted when registered

Remaining gaps are tracked in [REVIEW_NOTES.md](../REVIEW_NOTES.md).

## Related documents

- [NOTICE.md](../NOTICE.md) — affiliation, license, hardware caution
- [REVIEW_NOTES.md](../REVIEW_NOTES.md) — audit resolved vs open
- [CONTRIBUTING.md](../CONTRIBUTING.md) — feedback scope and authoring loop
- [PACKAGING_NOTES.md](../PACKAGING_NOTES.md) — optional deps and import boundaries
- [README.md](../README.md) — review status and quickstart
