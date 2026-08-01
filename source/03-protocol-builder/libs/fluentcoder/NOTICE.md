# Notice

fluentcoder is part of **Fluent AI-Assistance**, released under the
[PolyForm Noncommercial License 1.0.0](../../../../LICENSE) at the repository
root (noncommercial / hobby / education free; commercial use requires contacting
[marwanluay2005@gmail.com](mailto:marwanluay2005@gmail.com)).

It is **not a production or instrument-qualified release**. Generated scripts
require normal FluentControl validation and lab safety review before hardware
use.

## What is in better shape today

Recent work has improved the **offline** authoring, simulation, and compile
paths. The following are exercised by tests and examples (with documented
limits):

- Python authoring API: worktables, labware, reagents, grouping, loops,
  conditionals
- Pipetting heads: MCA-96 (`wt.mca96`), MCA-384 (`wt.mca384`), LiHa (`wt.liha`),
  FCA (`wt.fca`)
- FluentControl variable tokens via `wt.declare_fc_variable(...)` for
  `catalog=` / `labware_type` in IR steps
- Gripper moves, magnet stacking semantics, and physical invariant checks in
  the simulator
- Simulator snapshot modes: full, `snapshot_mode="delta"` (per-step diffs),
  and `record_snapshots=False` / `snapshot_mode="final_only"`
- Subroutine calls with optional `SubroutineRegistry` simulation inlining
- `.xscr` compile and decompile for supported FluentControl XML
- Catalog indexing when a local FluentControl install is available

See [README.md](README.md) **What Works Today** and
[docs/RELEASE_READINESS.md](docs/RELEASE_READINESS.md) for the full readiness
checklist.

## What is still cautious

- **Hardware / robot use:** generated `.xscr` files and simulated protocols must
  pass normal FluentControl validation and lab safety review before any
  instrument run. fluentcoder does not replace method validation or
  qualification. The simulator models liquid and deck state approximately — not
  device timing, fluidics edge cases, or site-specific interlocks.
- **FluentControl dependency:** install-backed catalog, workspace loading,
  checksum embed, deploy, and shell-validation paths require an appropriately
  licensed FluentControl installation on the target machine.
- **Site-specific configuration:** bundled `generation.yaml` ships neutral
  placeholders (serial `0000000000`, generic catalog names, `780_Empty`
  workspace). Copy `generation.yaml.example` and replace values from your
  instrument before treating compile output as portable.
- **LM authoring** (`fluentcoder author` / `chat` / `deploy`) is optional,
  requires extra dependencies and API keys, and is **disabled in the
  protocol-builder pipeline** by design.

## Affiliation

This repository is **not** affiliated with, endorsed by, or licensed by Tecan.
"Tecan", "FluentControl", and related marks belong to their respective owners.
Users are responsible for complying with Tecan license terms and site policies
when using install-backed features.

## Not legal advice

This notice describes project posture for reviewers. It is not legal advice.
For distribution, compliance, or licensing decisions, consult qualified counsel
and your organization's policies.
