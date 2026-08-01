# Compile path

The compile path turns a Python-authored protocol into a `.xscr` XML file
that FluentControl can load. It's intentionally narrow:

```
[ Worktable.protocol_ir (list of Step objects) ]
       │  wt.to_protocol()
       ▼
[ Protocol IR — Pydantic model ]
       │  render_protocol(protocol)
       ▼
[ XML string ]
       │  rewrite_checksum_in_place(path)
       ▼
[ .xscr file ready for FluentControl ]
```

## IR — `fluentcoder/ir/schema.py`

The IR is a tree of Pydantic models that descends from the earlier
project-owned fluentdsl implementation. The outer shape:

```python
class Protocol(BaseModel):
    name: str
    comment: str
    variables: list[str]
    variable_defaults: dict[str, float|int|str]
    groups: list[Group]
    worktable_guid: str | None
    worktable_name: str | None
    liquid_class: str | None
    device_alias: str | None

    def total_steps() -> int                    # walk loops/conditionals
    def assign_line_numbers() -> None           # depth-first numbering

class Group(BaseModel):
    name: str                                   # 'Setup' / 'Pipetting' / …
    steps: list[Step]
    line_number: int | None

# Step is a discriminated union (Pydantic 2):
Step = AddLabwareStep | RemoveLabwareStep
     | GetHeadAdapterStep | DropHeadAdapterStep
     | PickUpTipsStep | SetTipsBackStep
     | AspirateStep | DispenseStep
     | RgaTransferLabwareStep | CgaGetFingersStep | CgaDropFingersStep
     | Mca384MixStep | Mca384EmptyTipsStep
     | LihaAspirateStep | LihaDispenseStep | LihaMixStep
     | LihaGetTipsStep | LihaDropTipsStep | LihaEmptyTipsStep
     | WaitStep | SetVariableStep | CalculateVariableStep
     | CommentStep | UserPromptStep | StartTimerStep | WaitForTimerStep
     | ExportVariableStep | ImportVariableStep | QueryVariableStep
     | ExecuteApplicationStep | DelayStep | SetLocationStep | SubRoutineStep
     | LoopStep | ConditionalStep | GenericStep
```

The `StepType` enum on each step (`AddLabwareStep.step_type` etc.) drives
both the Pydantic discriminator and the renderer's command dispatch.

## `Worktable.to_protocol()`

`fluentcoder/worktable.py`

```python
def to_protocol(self) -> Protocol:
    protocol = Protocol(
        name=self.name,
        comment=self.comment,
        variables=list(self.protocol_variables.keys()),
        variable_defaults=dict(self.protocol_variables),
        groups=[Group(name=g.name, steps=list(g.steps)) for g in self._groups],
        worktable_guid=self.workspace_guid,
        worktable_name=self.workspace_name,
    )
    protocol.assign_line_numbers()
    return protocol
```

Notes:

- When the worktable was built with `Worktable.from_workspace(...)`, the
  workspace GUID and name are copied onto the `Protocol` IR automatically.
- The IR is a **fresh snapshot**. Mutating the worktable after `to_protocol()`
  doesn't affect the returned `Protocol`.
- Group steps are shallow-copied. Step mutation is rare; in practice they
  are append-only Pydantic instances.
- `assign_line_numbers()` walks groups + steps depth-first and assigns
  `line_number` 1, 2, 3… The renderer uses these for `<LineNumber>`
  elements.

## Workspace binding

The script wrapper's `<Reference TypeId="WorktableWorkspace">` must name a
real FluentControl workspace. Binding is explicit on the `Protocol` IR:

| Source | Fields |
|---|---|
| `Worktable.from_workspace(...)` | `wt.workspace_guid` / `wt.workspace_name` → `Protocol.worktable_guid` / `worktable_name` via `to_protocol()` |
| Decompiler round-trip | Parsed from the source `.xscr` reference |
| Hand-built IR | Set `Protocol.worktable_guid` and `Protocol.worktable_name` directly |

`Worktable.compile()` always requires a bound workspace
(`Worktable.from_workspace(...)` or manually set `workspace_guid` /
`workspace_name`) before rendering.

For direct `render_protocol(protocol)` calls on IR without workspace fields,
the renderer falls back to the legacy `worktable:` block in
`generation.yaml` and emits a `UserWarning` (default). Set
`validation.strict_workspace_binding: true` in that file, or pass
`strict_workspace_binding=True` to `Renderer` / `render_protocol`, to fail
instead of using the dev-machine default.

## Renderer — `fluentcoder/compiler/renderer.py`

The package-local renderer descends from the earlier project-owned fluentdsl
implementation. Asset paths point inside the package
(`renderer.py:134-138`, `renderer.py:177`).

### Entry point

```python
from fluentcoder.compiler import render_protocol
xml = render_protocol(protocol)               # str
```

Or the lower-level class API:

```python
from fluentcoder.compiler import Renderer
r = Renderer()                                # picks up _assets/{config,reference,templates}
xml = r.render(protocol)
```

`Renderer.__init__` accepts `config_path`, `reference_path`, `templates_path`
overrides if you need to point at custom assets.

### What the renderer reads at startup

`Renderer.__init__` (`renderer.py:120-146`):

| Asset | Purpose |
|---|---|
| `_assets/config/generation.yaml` | Generation config: device aliases, liquid-class default, legacy worktable fallback, EVA adapter config. |
| `_assets/reference/commands.yaml` | Per-step XML command templates and parameter mappings. Indexed by command ID (`STEP_TO_COMMAND_ID` in `ir/schema.py:563`). |
| `_assets/reference/labware.yaml` | Per-labware metadata used at render time (well counts, category, functional group). |
| `_assets/templates/script_wrapper.xml` etc. | XML wrapping templates (script wrapper, group wrapper, loop, conditional, alternate). |

These are loaded once per `Renderer` instance.

### Render pipeline

`Renderer.render(protocol)` (`renderer.py:201`):

1. **Reset state** for this render (clear adapter config, labware-types
   map, placement map).
2. **Magnet cover normalization** — `_normalize_for_magnet_cover_site` —
   uses magnet `(location, site)` from IR `AddLabware` placements (ZEIA/xsit)
   and may rewrite known-bad plate types transferred onto those sites. It does
   **not** invent or relocate magnets to a hardcoded nest/site (e.g. Nest61mm
   site 3). No-op when the protocol has no magnet placements.
3. **Labware name normalization** — `_normalize_labware_names` — fixes
   labware-type names that are close-but-not-exact matches against the
   labware reference.
4. **Variable scan** — collect `set_variable` step values into a map for
   resolving variable-named labware types (e.g. when `add()` was called
   with a variable reference).
5. **Assign line numbers** — `protocol.assign_line_numbers()`.
6. **Build the XML** — wrap the protocol in `script_wrapper.xml`; render
   each group via `script_group.xml`; render each step via the appropriate
   command template from `commands.yaml`.
7. **Special groups** — loops use `loop_group.xml`, conditionals use
   `conditional_group.xml` (with optional `alternate_group.xml` for the
   `else` branch).

The final XML is a single string. It includes a placeholder `<Checksum>`
element which the install-bundle bridge fills in (next step).

## Checksum rewrite

`fluentcoder/catalog/fc_install.py:46`

```python
def rewrite_checksum_in_place(path) -> bool:
    core = shared_core()
    if core is None:
        return False
    payload = core.rewrite_checksum(path, in_place=True)
    return bool(payload.get("is_valid"))
```

`Worktable.compile()` calls this after writing the XML. It bridges to the
upstream `fluentcontrol_core` library which knows how FluentControl computes
its file checksum and rewrites the `<Checksum>` element in place. If
`fluentcontrol_core` isn't importable, the call is a silent no-op — the
file is still valid XML, just without a final checksum value (FluentControl
will recompute on first load).

## `Worktable.compile()`

`fluentcoder/worktable.py`

```python
def compile(self, out_path: str | Path, *, deterministic: bool = False) -> Path:
    from .compiler import render_protocol
    from .catalog import rewrite_checksum_in_place

    self._require_bound_workspace()
    protocol = self.to_protocol()
    xml = render_protocol(protocol, deterministic=deterministic)
    path = Path(out_path)
    path.write_text(xml, encoding="utf-8")
    rewrite_checksum_in_place(path)
    return path
```

End-to-end, given a built worktable:

```python
out = wt.compile("simple_transfer.xscr")
# out = wt.compile("simple_transfer.xscr", deterministic=True)
print(f"Wrote {out}")
```

The output is a `.xscr` file FluentControl can open directly.

## Parity guarantees

The rendering pipeline is deterministic when `deterministic=True` is passed
to `wt.compile(...)` or `render_protocol(...)`. In that mode the renderer
emits a stable `uuid.uuid5` for the `WorkspaceDelta` `<Identifier>` derived
from the protocol name instead of a fresh `uuid.uuid4()` on every call
(`renderer.py:_workspace_delta_guid`).

By default (`deterministic=False`), two runs of the same input differ on
that one GUID. The parity test (`tests/test_simple_transfer_parity.py`)
normalizes that GUID (or uses `deterministic=True` in
`test_deterministic_compile_produces_identical_xscr`) before byte-comparing
outputs.

## Generated XML — annotated outline

```xml
<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:sd="..." dataStoreVersion="4">
  <Payload>
    <ObjectName>Simple transfer</ObjectName>
    <Comment>...</Comment>
    <Reference><TypeId>WorktableWorkspace</TypeId>            ← GUID + name from Protocol IR (or warned config fallback)
              <Guid>...</Guid><ObjectName>...</ObjectName></Reference>
    <Reference><TypeId>LiquidClass</TypeId>...</Reference>    ← default liquid class
    <PayloadData>
      <Script>
        <Properties>
          <VxWorkspaceData>
            <BaseWorkspaceName>...</BaseWorkspaceName>
            <CameraView>...</CameraView>                       ← static
            <WorkspaceDeltas>                                  ← per-render random GUID
              <d2p1:string>&lt;VxWorkspaceDelta...
                &lt;Identifier&gt;<RANDOM>&lt;/Identifier&gt;
              </d2p1:string>
            </WorkspaceDeltas>
          </VxWorkspaceData>
          <VariableDeclarations>...</VariableDeclarations>     ← from protocol_variables
        </Properties>
        <ScriptModule>
          <Statements>                                          ← protocol groups + steps
            <Object Type="...">
              <AddLabwareDataV1>
                <LabwareType>96 Well Flat</LabwareType>
                <LabwareLable>SourcePlate</LabwareLable>
                <Location>Nest</Location>
                <Position>1</Position>
                ...
              </AddLabwareDataV1>
            </Object>
            ...
          </Statements>
        </ScriptModule>
      </Script>
    </PayloadData>
  </Payload>
  <Checksum>...</Checksum>                                      ← rewritten by fc bridge
</sd:VxData>
```
