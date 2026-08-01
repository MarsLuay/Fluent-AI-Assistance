# Authoring API

This is the surface authors interact with: `Worktable`, the labware classes,
the pipetting heads, the gripper, and `Reagent`. Each method is small and
emits exactly one IR step. State changes are reconstructed by the simulator
when you call `wt.simulate()`, not at authoring time.

## `Reagent`

`fluentcoder/reagent.py:14`

```python
@dataclass(frozen=True, eq=False)
class Reagent:
    name: str
    pinned_when_magnetized: bool = False
    metadata: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)
```

- `eq=False` keeps Python identity (`is`-comparison) as the meaning of
  reagent equality. Two `Reagent("Ethanol 70%")` calls produce **different**
  reagents — keep one canonical instance and reference it everywhere.
- `pinned_when_magnetized=True` marks a reagent (paramagnetic beads, MyOne,
  AMPure) as immobile when its containing labware is magnetized.
  `Simulator._aspirate_one` skips such layers when the plate is on a magnet
  rack.

## `Worktable`

`fluentcoder/worktable.py:21`

The conductor: collects IR steps into groups, exposes the heads + gripper,
manages the slot map, and provides `compile()` / `simulate()`.

### Construction

```python
wt = Worktable(name="Simple transfer", comment="Move liquid")
```

Or, from a real FluentControl workspace:

```python
wt = Worktable.from_workspace(
    "780_Empty",
    auto_place=True,        # default — auto-instantiate occupants
    protocol_name="My run",
    comment="",
)
```

`from_workspace` is fully covered in [docs/catalog.md](catalog.md).

### Methods

| Method | What it does |
|---|---|
| `wt.group(name)` | Open a new IR group ("Setup", "Transfer", …). Subsequent emits land in this group. |
| `wt.place(labware, location, position)` | Add labware to a slot. Emits `AddLabwareStep`. Refuses occupied slots; refuses slots outside `valid_slots` if a workspace whitelist is set. Returns the labware (capture for chaining). |
| `wt.remove(labware)` | Pop labware from the worktable. Emits `RemoveLabwareStep`. |
| `wt.declare_variable(name, default)` | Declare a FluentControl protocol-level variable (rendered into the .xscr's `VariableDeclarations`). |
| `wt.set_sim_value(name, value)` | Provide a concrete sim-time value the simulator should substitute for a runtime variable in loops/conditionals/imports. |
| `with wt.loop(times=N or 'var', name=...)` | Context manager. Steps emitted inside the `with` block become a `LoopStep.steps` body. `times` is either a literal int or a runtime-variable name (resolved via `set_sim_value`). |
| `with wt.conditional(left='var', op='>=', right=7, name=...)` | Context manager. Steps emitted inside become a `ConditionalStep.then_steps` body. `op` is one of `==`/`!=`/`<`/`<=`/`>`/`>=`. else-branch authoring is not part of v1.1 — populate `cond.else_steps` directly if needed. |
| `wt.call_subroutine(path, *, execution_mode=...)` | Call a FluentControl subroutine by script path. Emits `SubRoutineStep`. |
| `wt.to_protocol()` | Build a frozen `Protocol` IR object from the collected steps. |
| `wt.simulate(*, subroutine_registry=...)` | Run the simulator. Populates `wt.snapshots`. Pass `SubroutineRegistry` to inline registered subroutine `.xscr` bodies. |
| `wt.compile(out_path, *, deterministic=False)` | Render IR to `.xscr` at `out_path`. `deterministic=True` fixes the `WorkspaceDelta` GUID. |
| `wt.labware_by_label(label)` | Find a placed labware by its author-given label. |

### Attributes

| Attribute | Meaning |
|---|---|
| `wt.name` / `wt.comment` | Protocol name and comment (rendered into `.xscr`). |
| `wt.slot_map: dict[(loc,pos)] -> list[Labware]` | Authoring-time slot state, bottom→top stacks. |
| `wt.valid_slots: set[(loc,pos)] \| None` | Workspace whitelist. `None` if no `from_workspace`. |
| `wt.workspace_name` | Set by `from_workspace`; `None` otherwise. |
| `wt.snapshots: list[Snapshot]` | Filled by `simulate()`. Indexed by step. |
| `wt.mca96: MCA96Head` | The MCA-96 pipetting head (always present). |
| `wt.mca384: MCA384Head` | The MCA-384 pipetting head (always present). |
| `wt.liha: LiHa` | The LiHa eight-channel pipetting head (always present). |
| `wt.gripper: Gripper` | The gripper (always present). |
| `wt.protocol_variables: dict[str, value]` | Author-declared FC variables. |
| `wt.sim_values: dict[str, value]` | Author-provided sim-time substitutions. |

## Labware classes

Ten behavioral families — one Python class per category. `Plate96`,
`MCA100Box`, etc. are thin convenience subclasses.

| Class | File | Category | Notes |
|---|---|---|---|
| `Plate`, `Plate96`, `Plate96Deep`, `Plate384` | `labware/plates.py` | `plate` | Auto-parallel pipettable. `Plate96` fixes 8×12; `Plate384` fixes 16×24. |
| `Trough`, `Trough25mL`, `Trough100mL`, `Waste` | `labware/troughs.py` | `trough` | Single-pool reservoir; `pool` attribute returns the lone Well. |
| `TipBox`, `MCA100Box`, `MCA200Box`, `FCA50Box`, `FCA200Box`, `FCA1000Box` | `labware/tipboxes.py` | `tip_box` | Carries `capacity_ul` and an `is_full: bool`. |
| `Adapter`, `EvaAdapter` | `labware/adapters.py` | `adapter` | Head accessory; not placed on a slot. |
| `MagnetRack` | `labware/magnet.py` | `magnet_rack` | Stacking destination; bestows `is_magnetized` on labware above. |
| `TubeRack` | `labware/tuberack.py` | `tube_rack` | Discrete tube positions exposed as wells. Per-tube state is v1.2. |
| `WashStation`, `WasteChute`, `Hotel`, `FixedDeck` | `labware/deckitems.py` | various | Deck items; carry geometry but no per-well reagent state. |

Construction signature (shared by all):

```python
Plate96(
    label: str,                                # author-given identifier
    *,
    catalog: str | None = None,                # FluentControl catalog name
    max_well_volume_ul: float | None = None,   # override parsed/family default
)
```

When the catalog index is built (the normal case) `catalog=` is **required**
unless you register a class default (see below). When the index is empty
(offline / CI) `catalog=` is optional; the constructor synthesises a generic
well grid from the class's taxonomic shape.

Register defaults once per project so common classes omit `catalog=`:

```python
from fluentcoder import Plate96, Worktable
from fluentcoder.defaults import set_catalog_defaults

set_catalog_defaults({Plate96: "96 Well Flat"})
wt = Worktable(name="My run")
wt.place(Plate96("Source"), "Nest", 1)  # resolves "96 Well Flat" from default
```

The same registry is reachable as `wt.set_default_catalog(Plate96, "96 Well Flat")`.
Explicit `catalog=` always wins; without a default, omitting `catalog=` still raises.

### Common attributes

| Attribute | Type | Meaning |
|---|---|---|
| `lw.label` | `str` | Author-given identifier. |
| `lw.catalog_name` | `str` | FC catalog name (or `<offline:Class>` in offline mode). |
| `lw.slot` | `(str, int) \| None` | Worktable slot or `None` if not placed. |
| `lw.stack_below` | `list[Labware]` | Labware below this one in the slot's stack. |
| `lw.is_magnetized` | `bool` (property) | True iff a `MagnetRack` is below in the stack. |
| `lw.dim_mm` | `(x, y, z) \| None` | mm dimensions parsed from .xcmp. |
| `lw.site_offsets_mm` | `tuple[(x,y,z), ...]` | Per-site mm offsets if applicable. |
| `lw.wells` | `dict[str, Well]` | Wells keyed by address (`"A1"`, `"H12"`, …). |

### Common methods

| Method | Returns |
|---|---|
| `lw.well("A1")` | `Well` at the given address. |
| `lw.column(idx)` | List of wells in column `idx` (1-based). |
| `lw.row("A")` | List of wells in row "A". |
| `lw.all_wells()` | All wells. |
| `lw.fill_all(reagent, volume_ul)` | Set every well's contents to a single `(reagent, volume)` layer. **Twin-only** — does not emit IR. |

### `Well`

`fluentcoder/labware/base.py:42`

```python
@dataclass
class Well:
    address: str                              # 'A1', 'B12', …
    max_volume_ul: float
    layers: list[Layer]                       # bottom → top
    position_mm: tuple[float,float,float] | None   # parsed; offline = None
```

Attributes: `volume_ul` (sum of layers), `is_empty`. `add_layer(reagent, vol)`
implements the merge-or-push rule: if the existing top layer has the same
reagent (object identity), volume is merged; else a new layer is pushed.

### `Layer`

`fluentcoder/labware/base.py:34`

```python
@dataclass
class Layer:
    reagent: Reagent
    volume_ul: float
```

The simulator's job is to keep these layered through aspirate/dispense.

## `MCA96Head`

`fluentcoder/heads/mca96.py:42`

The MCA-96 head, accessed via `wt.mca96`. Methods emit IR; the simulator
reconstructs head + tip state.

| Method | IR emitted | Notes |
|---|---|---|
| `head.mount_adapter(adapter=None)` | `GetHeadAdapterStep` | Defaults to EVA via the catalog name `EVA[001]`. Pass an explicit `EvaAdapter` to override. |
| `head.drop_adapter(adapter=None)` | `DropHeadAdapterStep` | |
| `head.pick_up(tip_box)` | `PickUpTipsStep` | Simulator instantiates 96 fresh `Tip` objects with the box's `capacity_ul`. |
| `head.return_tips(tip_box=None)` | `SetTipsBackStep` | If `tip_box` is `None`, FluentControl returns to the source. |
| `head.aspirate(target, volume_ul, *, liquid_class)` | `AspirateStep` | `target` is a Labware (auto-parallel over wells). `liquid_class` is the **exact** FC liquid-class name, required. |
| `head.dispense(target, volume_ul, *, liquid_class)` | `DispenseStep` | |

### `Tip`

`fluentcoder/heads/mca96.py:20`

```python
@dataclass
class Tip:
    capacity_ul: float
    layers: list[Layer]
```

Tips dispense **FIFO** (the bottom of the tip's layer stack comes out first
— what was aspirated first dispenses first). This matters for tracking
contamination across multi-source tip-loads.

## `MCA384Head`

`fluentcoder/heads/mca384.py:27`

The MCA-384 head, accessed via `wt.mca384`. Emits MCA384-specific IR for
tip/arm commands; structured `AspirateStep` / `DispenseStep` reuse the same
simulator liquid math as MCA96 (384-tip array on `_mca_tips`).

| Method | IR emitted | Notes |
|---|---|---|
| `head.get_tips(tip_box=None)` | `Mca384GetTipsStep` | |
| `head.drop_tips(tip_box=None)` | `Mca384DropTipsStep` | |
| `head.move_arm(movement_type=..., labware=None)` | `Mca384MoveArmStep` | |
| `head.aspirate(target, volume_ul, *, liquid_class)` | `AspirateStep` | Auto-parallel over wells. MCA96 adapter must be mounted for simulator pre-checks. |
| `head.dispense(target, volume_ul, *, liquid_class)` | `DispenseStep` | |
| `head.mix(target, volume_ul, *, cycles=10, liquid_class)` | `Mca384MixStep` | |
| `head.empty_tips(target, volume_ul, *, liquid_class="Empty Tip")` | `Mca384EmptyTipsStep` | |

```python
wt.mca96.mount_adapter()
head = wt.mca384
head.get_tips(tips384)
head.aspirate(src384, 5.0, liquid_class="Water Free Single")
head.dispense(dst384, 5.0, liquid_class="Water Free Single")
head.drop_tips()
```

## Subroutine calls

```python
from fluentcoder.subroutines import SubroutineRegistry

registry = SubroutineRegistry()
registry.register_directory("path/to/subroutines")

wt.group("Run")
wt.call_subroutine(r"<ScriptsFolder>\<SubroutineName>", execution_mode="JoinSubroutine")

wt.simulate(subroutine_registry=registry)   # inlines registered .xscr bodies
```

Without `subroutine_registry`, `SubRoutineStep` is an opaque snapshot-only
pass-through during simulation.

## `Gripper`

`fluentcoder/gripper.py:14`

Accessed via `wt.gripper`. Moves labware between worktable slots, supports
stacking onto another labware.

```python
wt.gripper.move(labware, *, to=(loc, pos))    # plain slot move
wt.gripper.move(labware, *, onto=mag_rack)    # stack onto another labware
```

Exactly one of `to=` and `onto=` must be passed; the other raises `TypeError`.

`onto=` resolves to the target labware's *current* slot — so you can write
high-level intent ("put this on the magnet") without tracking which slot
the magnet rack lives on. The simulator updates the worktable's slot map
and re-derives `is_magnetized` for the moved labware.

The gripper emits three IR steps in sequence per move:

1. `CgaGetFingersStep`
2. `RgaTransferLabwareStep` (target slot)
3. `CgaDropFingersStep`

This matches FluentControl's convention.

## Magnet semantics

Magnetisation isn't a separate concept — it's a property of stacking. A
`MagnetRack` placed on the worktable becomes a stack target; gripper-moving
a plate `onto` the rack makes the plate's `is_magnetized` flip True
(derived from `stack_below` containing a `MagnetRack`). Aspirate semantics
respect it: when a magnetized well's targeted layer has a reagent with
`pinned_when_magnetized=True`, the simulator skips that layer top-down. If
all remaining volume is pinned, `CannotAspirateError` is raised; partial
shortfalls raise `InsufficientVolumeError`.

```python
beads = Reagent("AMPure beads", pinned_when_magnetized=True)
plate = wt.place(Plate96("P", catalog="96 Well Flat"), "Nest", 1)
rack  = wt.place(MagnetRack("M", catalog="24 Magnet Plate"), "Nest", 7)
plate.fill_all(beads, 100.0)

wt.gripper.move(plate, onto=rack)        # plate.is_magnetized → True

# Author tries to aspirate from beads-only well on magnetized plate.
head.aspirate(plate, 20.0, liquid_class="Water Free Single")
wt.simulate()                            # raises CannotAspirateError
```

## Loops and conditionals

```python
wt.declare_variable("cycles", 3)
wt.declare_variable("ph", 7)
wt.set_sim_value("cycles", 3)
wt.set_sim_value("ph", 7)

with wt.loop(times="cycles", name="Wash cycles"):
    head.aspirate(src, 10.0, liquid_class="Water Free Single")
    head.dispense(waste, 10.0, liquid_class="Water Free Single")
    with wt.conditional(left="ph", op=">=", right=7, name="Extra rinse"):
        head.aspirate(src, 5.0, liquid_class="Water Free Single")
        head.dispense(waste, 5.0, liquid_class="Water Free Single")
```

The renderer emits `<LoopGroup>` / `<ConditionalGroup>` blocks; the
simulator dispatches the body N times for loops and follows the
then-branch when the predicate is true. Sim-time values resolve
runtime-variable iteration counts and predicate operands. See
`examples/loop_conditional.py`.

## Compile and simulate

`wt.compile(path)` writes a `.xscr` file at `path` and returns a `Path`.
Pass `deterministic=True` for a stable `WorkspaceDelta` GUID across renders.

`wt.simulate()` walks the IR top-to-bottom and populates `wt.snapshots` —
one Snapshot per IR step, deep-copied so later mutations don't leak.
Author-side labware and `wt.slot_map` are not mutated; only snapshots and
the twin carry simulated state.

```python
wt.simulate()
# Inspect anywhere along the run:
wt.snapshots[5].labware("DestPlate").well("A1").layers
# Last snapshot reflects final state:
wt.snapshots[-1].mca_tips[0].volume_ul
```

See [docs/simulator.md](simulator.md) for what gets recorded and how to
query it.

## Authoring loop (optional)

Hand authoring (this document) uses the core package only. The separate **LM
authoring loop** (`fluentcoder author`, `chat`, `deploy`) requires
`pip install -e ".[authoring]"` and is disabled in the Fluent AI-Assistance
protocol-builder pipeline by design. See [CONTRIBUTING.md](../CONTRIBUTING.md)
§ Authoring loop and [RELEASE_READINESS.md](RELEASE_READINESS.md) §5.

## Chat-driven authoring (LangGraph)

`PromptAuthoringSession` and `PromptAuthoringService.author()` run the LM
authoring loop on a LangGraph state machine defined in
`fluentcoder/authoring/graph.py`. Nodes: `intent_nudge → model_call →
{dispatch_tools | extract_python} → validate_locally → {success | clarify |
fail}`. Repair-lock and grounding-gate live as conditional edges so the
existing test contract in `tests/test_authoring_session.py` is preserved.

### LangSmith tracing (opt-in)

Tracing is off by default. Set these env vars before running the chat or
calling `service.author()` to stream every node call to LangSmith:

```powershell
$env:LANGCHAIN_TRACING_V2 = 'true'
$env:LANGCHAIN_API_KEY = '<your LangSmith key>'
$env:LANGCHAIN_PROJECT = 'fluentcoder-authoring'   # optional project name
```

Each session/run produces a hierarchical trace: the top-level invocation
contains one span per graph node, and `model_call` spans contain the raw
ChatOpenAI request/response. Useful for debugging "why did the model do X" —
you can see the exact tool calls, repair-lock blocks, and grounding nudges in
sequence.

When the env vars are unset, LangChain emits no telemetry and the local
authoring path is unchanged.
