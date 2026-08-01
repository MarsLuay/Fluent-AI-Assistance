# Test fixtures policy

Committed fixtures here are **synthetic engine samples only**.

They must not encode a real lab’s scripts, worktables, deck layout, operator
prompts, media, or verification recipes.

## Source of lab truth

Lab-specific names and geometry come from a **user-provided full ZEIA** via
`import-project` (and derived files such as `labware_catalog.json` under the
import context). Do not treat anything under `tests/fixtures/` as a lab
template.

## Allowed in git

- Tiny `request.spec.yaml` stubs with placeholder names (`Lab`, `DemoScript`,
  `WT_Demo`, `Lab\\SUB_Example`).
- XML/JSON snippets used only to unit-test parsers and validators.
- Synthetic deck tokens only: `Demo_Worktable_A`, `Demo_Nest_Pos`,
  `Demo_Tube_Pos_1`, `Demo_Device_Pos`, and obviously fake GUIDs such as
  `aaaaaaaa-bbbb-4ccc-8ddd-111111111111`. Do not reuse real host worktable
  GUIDs or lab location names (`Falcon50_Pos_*`, Nest61mm/A200 site strings
  copied from a private export) as if they were product defaults.

## Not allowed in git

- Full operator-verification recipes mined from a real instrument.
- Real Scripts-folder prefixes, worktable names, or confidential prompts.
- Golden regeneration inputs for a private lab method.

## Private goldens (optional, local/CI only)

Heavy end-to-end golden regeneration is **opt-in** and must point at private
paths outside this repo (or gitignored local exports):

```bash
export TECAN_ENABLE_PRIVATE_GOLDENS=1
export TECAN_GOLDEN_SPEC=/path/to/private/request.spec.yaml
export TECAN_GOLDEN_CONTEXT=imported-context-name
# optional: TECAN_GOLDEN_CONTEXT_ROOT=/path/to/context
python3 -m pytest tests/test_verification_v12_golden_regression.py -q
```

Public clones run helper unit tests only; the private golden class skips.
