# fluentcoder Packaging Notes

fluentcoder is a first-party package developed alongside this project and lives
at `source/03-protocol-builder/libs/fluentcoder`. The protocol-builder pipeline
imports it directly.

Packaging details:

- The distribution metadata is named `fluentcoder`.
- Normal dependencies are limited to `pydantic` and `PyYAML`.
- `lxml` and authoring/model packages are optional.
- `fluentcoder.__init__` does not import the authoring API at top level, so
  compile/simulate/decompile can run without API-key-oriented dependencies.

The Python import package is `fluentcoder`, matching the DSL and examples.

## Recent capability summary

These features are implemented and documented in `README.md` and `docs/`:

| Area | Capability |
|---|---|
| Heads | `MCA96Head`, `MCA384Head`, `LiHa`, `FCAHead`; MCA partial pickup; LiHa `tip_channels` |
| Subroutines | `call_subroutine`, `SubroutineRegistry`, variable mappings, CLI `--subroutine-dir` |
| Simulator | `record_snapshots=False`, author-side isolation, `CannotAspirateError` (magnet + cap) |
| Catalog | Multi-install keyed DBs, `category_overrides.toml`, site-referenced `.xcon` indexing, `synthetic_catalog` fixture |
| Compile | Required workspace binding, `deterministic=True` for stable `WorkspaceDelta` GUID |
| Decompile | `RUPWorktableStatement` / `RUPStandardStatement` image prompts, subroutine variable mappings |
| Labware | `TubeRack.fill_tube()`, per-tube `cap_closed` |

## Workspace binding at compile time

The renderer writes a `<Reference TypeId="WorktableWorkspace">` into every
`.xscr`. Binding is explicit on the `Protocol` IR:

- `Worktable.from_workspace(...)` sets `workspace_guid` / `workspace_name`,
  copied to `Protocol.worktable_guid` / `worktable_name` by `to_protocol()`.
- `Worktable.compile()` requires a bound workspace before rendering.
- Direct `render_protocol(protocol)` without those fields falls back to the
  legacy `worktable:` block in `fluentcoder/_assets/config/generation.yaml`
  and emits a `UserWarning` (default). Set
  `validation.strict_workspace_binding: true` in that file, or pass
  `strict_workspace_binding=True` to `Renderer` / `render_protocol`, to fail
  instead.

See [docs/compile-path.md](docs/compile-path.md) for the full compile path.

## Image prompts

`wt.user_prompt(..., image_path=..., rup_kind="standard")` compiles to
`RUPStandardStatement` with `SelectedImagePath`. Without `rup_kind="standard"`,
`image_path` routes to `RUPWorktableStatement` / `CustomDetailImageFilePath`.
The decompiler round-trips both worktable and standard RUP image prompts back to
`user_prompt(...)` / `user_prompt_worktable(...)`.

## Offline / CI catalog

Set `FLUENTCODER_TEST_CATALOG_DB` to
`tests/fixtures/synthetic_catalog/install_index.db` (built by
`python tests/fixtures/synthetic_catalog/bootstrap.py`) for tests that must
run without a real FluentControl install.
