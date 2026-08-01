# Ready To Import

This is the user handoff folder for generated Tecan artifacts.

Files here are grouped by script name. Each script folder is meant to be
manually reviewed and imported by the user. The protocol builder does not
deploy anything into FluentControl.

## Layout

```text
ready-to-import/
  _shared/temp_files/
    build/                         # shared tooling + setuptools scratch
      tecan_project_index.sqlite
      setuptools/<package>/        # setuptools build-base (not under source/*)
      api_v2/                      # generated API V2 mining outputs
    logs/                          # workflow event JSONL, doctor/tool logs
    cache/
  <context>/temp_files/            # per-context protocol workflow scratch
                                   # (error_logs_*, tecan_method_source, setup logs)
  <protocol>_vN/                   # published import bundles
```

Published script bundles:

```text
ready-to-import/
  script-name/
    protocol.ir.json
    protocol_draft.py
    generated_script.xscr
    generated_worklist.gwl
    original_sources/
    reports/
      validation_report.md
      simulation_report.md
      repair_plan.md
      compile_report.md
    worktable_changes.md
    metadata.json
    RECREATE_SCRIPT.md
```

The folder name should be the generated script name. All information for that
script should live in that one folder.

`RECREATE_SCRIPT.md` explains how to rebuild or manually recreate the script
instead of importing the compiled `.xscr`.

Treat every generated `.xscr` as a draft until it has been reviewed, simulated,
and validated inside FluentControl.

Tooling notes:

- Project-reader indexes/reports and worklist examples default under
  `_shared/temp_files/build/`.
- Workflow event logs default under `_shared/temp_files/logs/`.
- Setuptools `build/` staging is redirected via each package `setup.cfg`
  `build-base` into `_shared/temp_files/build/setuptools/<package>/`.
- Wheelhouse installs stage in OS temp (see protocol-builder bootstrap) so
  package trees stay clean.
- `python -m build` still writes final wheels to a local `dist/` unless you
  pass `--outdir ready-to-import/_shared/temp_files/build/dist/<package>`.
- The retired `Inspiration/` tree is not used; optional manuals/connector
  checkouts may live under `_shared/temp_files/manuals/` and
  `_shared/temp_files/connector-repos/`. Pass explicit `--repo` to connector
  tooling when mining SiLA sources.
