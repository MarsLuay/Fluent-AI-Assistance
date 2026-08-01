# Decompiler — `.xscr` → `.py`

`fluentcoder/decompiler/`

The decompiler is the inverse of the renderer. It parses a FluentControl
`.xscr` into the Pydantic `Protocol` IR, then emits a self-contained
fluentcoder Python protocol that — when executed — re-renders the same
`.xscr`.

```
[ .xscr (FluentControl XML) ]
       │  parse_xscr(path)
       ▼
[ Protocol IR — Pydantic model ]
       │  emit_python(protocol)
       ▼
[ Python source string ]
       │  write to disk
       ▼
[ <name>.py with build_worktable() factory ]
       │  python <name>.py  →  build_worktable().compile(...)
       ▼
[ <name>_recompiled.xscr ]
```

Round-trip parity: `<name>.xscr` and `<name>_recompiled.xscr` are
byte-equal modulo:

- the per-render random `<Identifier>` GUID inside `<WorkspaceDelta>`
- the FC-rewritten `<Checksum>` payload (deterministic given content)

## CLI

```
fluentcoder decompile <input.xscr> [-o <output.py>] [--strict]
```

- `-o, --output` — output `.py` path (defaults to `<input>.py`).
- `--strict` — exit code 1 if any step decodes as `GenericStep` (an
  unrecognised IR step type). Default behaviour: warn-only.

After decompiling, validate the generated protocol with:

```
fluentcoder simulate <output.py> --strict --fail-on-opaque --json
```

That path preserves the machine-readable simulator result, including
`status` (`passed`, `passed_with_opaque`, `failed`) and the structured
`failure` object when strict validation fails.

```
fluentcoder decompile examples/simple_transfer.xscr
# → examples/simple_transfer.py written
```

## Architecture

Two modules:

| File | Job |
|---|---|
| `xscr_parser.py` | XML → `Protocol` IR. Inverts the renderer's command-id mapping. |
| `codegen.py`     | `Protocol` IR → Python source string. Per-step emit table; class dispatch via the SQL catalog. |

`__init__.py` re-exports `parse_xscr` and `emit_python` for direct
programmatic use.

```python
from fluentcoder.decompiler import parse_xscr, emit_python

protocol = parse_xscr("lab/run_42.xscr")
src = emit_python(protocol, source_xscr="run_42.xscr")
open("lab/run_42.py", "w", encoding="utf-8").write(src)
```

## Per-step emit table

| IR step                   | Generated Python                                                        |
|---------------------------|--------------------------------------------------------------------------|
| `AddLabwareStep`          | `<var> = wt.place(<Class>(<label>, catalog=<name>), <loc>, <pos>)` + `fill_all` |
| `AddLabwareStep` for `EVA[*]` | *skipped* — `head.mount_adapter()` handles it implicitly             |
| `RemoveLabwareStep`       | `wt.remove(<var>)`                                                       |
| `GetHeadAdapterStep`      | `head.mount_adapter()`                                                   |
| `DropHeadAdapterStep`     | `head.drop_adapter()`                                                    |
| `PickUpTipsStep`          | `head.pick_up(<var>)`                                                    |
| `SetTipsBackStep`         | `head.return_tips(<var>)`                                                |
| `AspirateStep`            | `head.aspirate(<var>, <vol>, liquid_class=<name>)`                       |
| `DispenseStep`            | `head.dispense(<var>, <vol>, liquid_class=<name>)`                       |
| Cga/Rga/Cga triplet       | collapsed → `wt.gripper.move(<var>, onto=<var>)` or `, to=(<loc>, <pos>))` |
| stray `Cga*FingersStep`   | dropped (`gripper.move` regenerates them on re-render)                   |
| `LoopStep`                | `with wt.loop(times=N or 'var', name=...):` block                        |
| `ConditionalStep`         | `with wt.conditional(left=, op=, right=, name=...) as _cond:` + optional `with wt.else_branch(_cond):` when an `<AlternateGroup>` sibling was parsed |
| `SubRoutineStep`          | `wt.call_subroutine(...)` with optional `execution_mode`, `variable_mappings_start`/`end` |
| `LihaGetTipsStep`         | `liha = wt.liha` + `liha.get_tips(<var>)` (structured V3+ XML; legacy `LihaPickUp` → `wt.raw_xml_step`) |
| `LihaDropTipsStep`        | `liha.drop_tips(<var>)`                                                  |
| `LihaAspirateStep`        | `liha.aspirate(<var>, <vol>, liquid_class=..., well_offset=...)`        |
| `LihaDispenseStep`        | `liha.dispense(...)`                                                     |
| `LihaMixStep`             | `liha.mix(..., cycles=...)`                                              |
| `LihaEmptyTipsStep`       | `liha.empty_tips(<var>, <vol>, liquid_class=...)`                        |
| `InitializeDeviceStep`    | `wt.initialize_device(device_alias=..., available_id=..., init_type=...)` |
| `ApplicationDriverMacroStep` | `wt.application_driver_macro(...)`                                    |
| Group boundary            | `wt.group(<name>)`                                                       |
| `GenericStep` (unknown)   | `wt.raw_xml_step(...)` or `# [decompiler] unsupported step: <name>`      |

## Stand-in reagent contract

Reagent identity, initial well fills, and `pinned_when_magnetized`
flags live entirely in the Python author-side state. They are never
serialised into the `.xscr`. Decompiled `.py` therefore cannot
recover them.

To keep the decompiled `.py` simulate-able out of the box, codegen
injects:

```python
default_reagent = Reagent("liquid")
…
sourceplate = wt.place(Plate96('SourcePlate', catalog='96 Well Flat'), 'Nest', 1)
sourceplate.fill_all(default_reagent, 200.0)
…
```

The fill volumes are heuristic per family:

| Family   | Stand-in fill µL |
|----------|------------------|
| plate    | 200              |
| trough   | 20 000           |
| others   | not filled       |

To model real reagent identity (e.g. AMPure beads with
`pinned_when_magnetized=True`), replace the stand-in:

```python
beads = Reagent("AMPure beads", pinned_when_magnetized=True)
sample = Reagent("Sample DNA")

# Replace the stand-in fill in the placement block:
sourceplate.fill_all(sample, 100.0)
for w in sourceplate.wells.values():
    w.layers.append(Layer(reagent=beads, volume_ul=20.0))
```

## Class dispatch

For each `AddLabwareStep`, the codegen looks up the catalog row by
name to pick a Python class:

1. `resolve_by_name(catalog_name) -> CatalogEntry` (SQL hit).
2. Map `entry.category` to the family base class via
   `CATEGORY_TO_CLASS` (e.g. `'plate' → Plate`).
3. For plates, refine by grid size:
   - `(8, 12)` → `Plate96`
   - `(16, 24)` → `Plate384`
   - otherwise → base `Plate`.
4. Fall back to `FixedDeck` if no catalog row matches.

Specific tip-box / trough subclasses (`MCA100Box` vs `FCA50Box`,
`Trough25mL` vs `Trough100mL`) are not auto-disambiguated in v1.1 —
the base class is emitted with the correct catalog string, which is
sufficient for round-trip parity.

## Variable handling

`<VariableDeclarations>` in the `.xscr` are read by the parser into
`Protocol.variables` and `Protocol.variable_defaults`. Codegen emits
both a `wt.declare_variable(...)` and a matching `wt.set_sim_value(...)`
seeded with the default. The user can override the sim-time value
before calling `wt.simulate()`.

## Round-trip parity test

`tests/test_xscr_roundtrip.py` parametrises over every example .py and
asserts byte-equal round-trip:

```
1. examples/<name>.py → wt.compile() → orig.xscr
2. parse_xscr(orig.xscr) → Protocol
3. emit_python(Protocol) → decompiled.py
4. exec decompiled.py → wt.compile() → recompiled.xscr
5. assert normalize(orig) == normalize(recompiled)
```

`normalize` strips the `<WorkspaceDelta>` GUID (random unless you
`compile(..., deterministic=True)`) and the `<Checksum>` payload.

## Known limits

- **Specific tip-box / trough subclass dispatch** — base classes only;
  user can manually swap to `MCA100Box`/`Trough25mL`/etc. if desired.
- **`GenericStep` for unrecognised types** — emitted as `wt.raw_xml_step(...)`
  when raw XML is preserved, otherwise as an unsupported-step comment.
  Round-trip parity won't hold for protocols that use steps outside the
  recognised set.
- **Subroutine simulate inlining** — codegen emits `wt.call_subroutine(...)`,
  but simulator descent still requires a separate `SubroutineRegistry` of
  `.xscr` files; subroutine bodies are not inlined into the parent `.py`.
- **Legacy LiHa get-tips XML** — structured `LihaGetTipsScriptCommandDataV3+`
  codegen to `liha.get_tips(...)`. Older `LihaPickUpScriptCommandDataV1` and
  complex tip-selection payloads still decode to `GenericStep` and emit
  `wt.raw_xml_step('LihaGetTips', ...)`.

## Programmatic example

```python
from pathlib import Path
from fluentcoder.decompiler import parse_xscr, emit_python

orig = parse_xscr("lab/screen_001.xscr")
print(f"{len(orig.groups)} groups, "
      f"{sum(len(g.steps) for g in orig.groups)} steps")

src = emit_python(orig, source_xscr="screen_001.xscr")
Path("lab/screen_001.py").write_text(src, encoding="utf-8")
```

## Corpus Harness

`fluentcoder.decompiler.run_decompiled_corpus(...)` provides a deterministic
decompile/build/simulate harness for a fixed `.xscr` corpus. It emits one
compact result per protocol with:

- `status`
- `classification`
- `modeled_coverage`
- `unsupported_command_ids`
- `failure`
